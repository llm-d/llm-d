"""
SGLang hooks for GKE Fast Pod Snapshotting (single-rank deployments).

sglang/launcher.py passes sglang_warmup_and_hold (execute_warmup_func) and
sglang_snapshot_callback (launch_callback) to SGLang's launch_server so the server status is
held at ServerStatus.Starting while GPU memory is released, a checkpoint is taken, and GPU
memory is restored, before reporting ready.
"""

from __future__ import annotations

import logging
import os
import socket
import time
from typing import Any, Callable
from urllib.parse import urlsplit

import requests

from ..providers import GKESnapshotProvider

logger = logging.getLogger("sglang.snapshot.wrapper")


def _wait_for_server(server_url: str, attempts: int = 120) -> None:
    """
    Block until the server at server_url accepts TCP connections, retrying once a second.

    SGLang's warmup waits like this before its first request. With --skip-server-warmup, SGLang
    calls launch_callback from its warmup thread before uvicorn may have started listening.
    """
    url = urlsplit(server_url)
    address = (url.hostname, url.port or (443 if url.scheme == "https" else 80))
    for attempt in range(attempts):
        try:
            with socket.create_connection(address, timeout=5):
                return
        except OSError:
            if attempt == attempts - 1:
                raise
            time.sleep(1)


def _get_ssl_verify(server_args) -> bool | str:
    """Return the requests verify= setting from SGLang's ssl_verify_of, falling back to False."""
    try:
        from sglang.srt.arg_groups.serving_hook import ssl_verify_of  # pyright: ignore[reportMissingImports,reportUnknownVariableType]

        return ssl_verify_of(server_args)
    except ImportError:
        return False


def sglang_snapshot_callback(
    snapshot_provider: GKESnapshotProvider,
    server_args,
    held_status: dict[str, Any],
) -> None:
    """
    Executes the sleep/snapshot/wake cycle before the server reports ServerStatus.Up.

    Registered as launch_server's launch_callback, which SGLang calls after warmup, or straight
    away with --skip-server-warmup. held_status carries the post-warmup status saved by
    sglang_warmup_and_hold; without one, the current status is saved and restored instead.
    """
    from sglang.srt.entrypoints import http_server  # pyright: ignore[reportMissingImports,reportUnknownVariableType]

    tokenizer_manager: Any = http_server._global_state.tokenizer_manager  # pyright: ignore[reportOptionalMemberAccess]

    # Hold server_status at Starting during sleep, checkpoint, and wake-up so
    # readiness/health probes (/health, /ready) return 503 until memory is restored.
    # After a warmup, sglang_warmup_and_hold has already done this and saved the
    # post-warmup status. With --skip-server-warmup, SGLang sets Up right before calling
    # launch_callback, leaving a brief window before this reset where /health can see Up.
    prev_status = held_status.get("status", tokenizer_manager.server_status)
    tokenizer_manager.server_status = http_server.ServerStatus.Starting
    ssl_verify = _get_ssl_verify(server_args)

    try:
        server_url = server_args.url()
        key = server_args.admin_api_key or server_args.api_key
        headers = {"Authorization": f"Bearer {key}"} if key else {}

        def post(path: str) -> None:
            # An empty JSON body selects SGLang's default (GPU_MEMORY_ALL_TYPES: weights,
            # kv_cache, cuda_graph) while satisfying FastAPI's required Body() parameter.
            resp = requests.post(
                f"{server_url}{path}",
                headers=headers,
                json={},
                timeout=600,
                verify=ssl_verify,
            )
            resp.raise_for_status()

        # With --skip-server-warmup, nothing has waited for the server to start listening yet.
        _wait_for_server(server_url)

        # STEP 1: Release GPU memory occupation (KV cache), move weights, save addresses
        logger.info("Executing POST /release_memory_occupation to release physical VRAM...")
        post("/release_memory_occupation")

        # STEP 2: Trigger the snapshot / checkpoint
        try:
            logger.info("Triggering snapshot checkpoint...")
            snapshot_provider.trigger()
            logger.info("Snapshot checkpoint created successfully.")
        except Exception as e:
            # We continue without snapshotting but this can still leave us
            # in an inconsistent state. Ex: trigger may clear the weights
            # before it failed. They would need to be downloaded again.
            logger.error(
                "Snapshot checkpointing failed: %s. Resuming SGLang service without checkpoint.",
                e,
                exc_info=True,
            )
        finally:
            # STEP 3: Regardless of success or failure, resume GPU memory occupation, restore addresses, move weights, allocate VRAM
            logger.info("Executing POST /resume_memory_occupation to restore VRAM...")
            post("/resume_memory_occupation")
            logger.info("SGLang memory occupation resumed successfully.")

        # STEP 4: Restore the saved server status only after memory occupation has been restored
        tokenizer_manager.server_status = prev_status
        logger.info("Restored server_status to %s.", prev_status)
    except Exception as e:
        logger.error("Sleep/wake cycle failed: %s. Terminating SGLang server.", e, exc_info=True)
        http_server.kill_process_tree(os.getpid())


def sglang_warmup_and_hold(
    server_args,
    execute_warmup_func: Callable,
    held_status: dict[str, Any],
) -> bool:
    """
    Runs SGLang's server warmup, then holds server_status at Starting for the snapshot.

    Registered as launch_server's execute_warmup_func. A successful warmup sets server_status to
    Up (or UnHealthy after a partial PD warmup) before SGLang calls launch_callback. Save that
    status in held_status and set Starting again, so readiness probes keep failing until
    sglang_snapshot_callback has finished the cycle and restored it.
    """
    # Run upstream native warmup procedure.
    if not execute_warmup_func(server_args):
        return False

    from sglang.srt.entrypoints import http_server  # pyright: ignore[reportMissingImports,reportUnknownVariableType]

    tokenizer_manager: Any = http_server._global_state.tokenizer_manager  # pyright: ignore[reportOptionalMemberAccess]
    held_status["status"] = tokenizer_manager.server_status
    tokenizer_manager.server_status = http_server.ServerStatus.Starting
    logger.info("Holding server_status at Starting until the snapshot completes.")
    return True
