"""
SGLang Wrapper Entrypoint for GKE Fast Pod Snapshotting (docker/scripts/snapshot/sglang/wrapper.py).

Note: Scope is single-rank deployments (DP/multi-rank barrier coordination is not covered).

Provides the two hooks that sglang/launcher.py passes to SGLang's launch_server, so the snapshot
lifecycle completes before the server reports ready (ServerStatus.Up):

sglang_warmup_and_snapshot (execute_warmup_func):
1. Run SGLang's standard server warmup.
2. Save the post-warmup tokenizer_manager.server_status (Up, or UnHealthy after a partial PD warmup)
   and set it back to ServerStatus.Starting.

sglang_snapshot_callback (launch_callback, which SGLang also calls with --skip-server-warmup):
3. Hold server_status at ServerStatus.Starting. With --skip-server-warmup, save the status SGLang set first.
4. Wait until the HTTP server accepts connections.
5. Release physical VRAM via HTTP POST to /release_memory_occupation (tags=["weights", "kv_cache"]).
6. Trigger the snapshot checkpoint via snapshot_provider.trigger() (clearing model weights cache on disk).
7. Re-allocate physical VRAM via HTTP POST to /resume_memory_occupation (tags=["weights", "kv_cache"]) upon restore.
8. Restore the saved server_status only after memory occupation is resumed.
9. Terminate the server process tree via kill_process_tree if the sleep/wake cycle fails.
"""

from __future__ import annotations

import logging
import os
import socket
import time
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

import requests

from ..providers import GKESnapshotProvider, get_snapshot_provider

logger = logging.getLogger("sglang.snapshot.wrapper")


def _get_tokenizer_manager():
    """Return SGLang's live TokenizerManager, or None (with a warning) if it is unavailable."""
    try:
        from sglang.srt.entrypoints import http_server  # pyright: ignore[reportMissingImports,reportUnknownVariableType]
    except ImportError as err:
        logger.warning(
            "SGLang http_server is not importable (%s); no snapshot will be taken.",
            err,
        )
        return None

    # SGLang keeps the TokenizerManager on http_server._global_state, which is
    # reassigned during launch_server, so read it from the module at call time.
    tokenizer_manager = getattr(getattr(http_server, "_global_state", None), "tokenizer_manager", None)
    if tokenizer_manager is None:
        # SGLang also calls launch_callback on the Rust server path (SGLANG_RUST_SERVER), which
        # has no Python tokenizer manager. Without one, server_status can't be held at Starting,
        # so readiness probes would pass while memory is released.
        logger.warning(
            "SGLang tokenizer_manager is not available (the Rust server is not supported); no snapshot will be taken."
        )
    return tokenizer_manager


def _get_auth_headers(server_args) -> dict[str, str]:
    """Auth header for SGLang admin endpoints; mirrors upstream _freeze_gc_after_server_warmup."""
    key = getattr(server_args, "admin_api_key", None) or getattr(server_args, "api_key", None)
    return {"Authorization": f"Bearer {key}"} if key else {}


def _get_ssl_verify(server_args) -> bool | str:
    """requests' verify= value for the server's own endpoints, computed as SGLang's warmup does."""
    try:
        from sglang.srt.arg_groups.serving_hook import ssl_verify_of  # pyright: ignore[reportMissingImports,reportUnknownVariableType]
    except ImportError:
        # Older SGLang (e.g. v0.5.15) has ServerArgs.ssl_verify() instead, or no TLS support at all.
        ssl_verify = getattr(server_args, "ssl_verify", None)
        return ssl_verify() if ssl_verify is not None else True
    return ssl_verify_of(server_args)


def _release_discards_weights(server_args) -> bool:
    """
    True with --enable-memory-saver but not --enable-weights-cpu-backup: SGLang then throws the
    weights away on release instead of copying them to CPU memory, so they'd be lost after resume.
    """
    return bool(getattr(server_args, "enable_memory_saver", False)) and not getattr(
        server_args, "enable_weights_cpu_backup", False
    )


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


def sglang_snapshot_callback(
    server_url: str,
    snapshot_provider: Optional[GKESnapshotProvider] = None,
    server_args=None,
    held_status: Optional[dict[str, Any]] = None,
) -> None:
    """
    Executes the sleep/snapshot/wake cycle before the server reports ServerStatus.Up.

    Registered as launch_server's launch_callback, which SGLang calls after warmup, or straight
    away with --skip-server-warmup. held_status carries the post-warmup status saved by
    sglang_warmup_and_snapshot; without one, the current status is saved and restored instead.
    """
    if snapshot_provider is None:
        snapshot_provider = get_snapshot_provider()

    if snapshot_provider is None:
        logger.info(
            "No snapshot provider configured (SNAPSHOT_PROVIDER is unset or empty). Snapshotting is disabled."
        )
        return

    if not snapshot_provider.is_available():
        logger.warning(
            "Pod snapshot trigger not available (checkpoint file '%s' is not writable). Skipping snapshot.",
            snapshot_provider.proc_path,
        )
        return

    if _release_discards_weights(server_args):
        logger.warning(
            "--enable-memory-saver is set without --enable-weights-cpu-backup, so releasing GPU memory "
            "would discard the model weights; no snapshot will be taken."
        )
        return

    tokenizer_manager = _get_tokenizer_manager()
    if tokenizer_manager is None:
        return

    headers = _get_auth_headers(server_args)
    ssl_verify = _get_ssl_verify(server_args)

    def post(path: str) -> None:
        # Release and resume differ only in the path: both act on the weights and the KV cache.
        resp = requests.post(
            f"{server_url}{path}",
            headers=headers,
            json={"tags": ["weights", "kv_cache"]},
            timeout=600,
            verify=ssl_verify,
        )
        resp.raise_for_status()

    from sglang.srt.entrypoints.http_server import ServerStatus  # pyright: ignore[reportMissingImports,reportUnknownVariableType]

    # Hold server_status at Starting during sleep, checkpoint, and wake-up so
    # readiness/health probes (/health, /ready) return 503 until memory is restored.
    # After a warmup, sglang_warmup_and_snapshot has already done this and saved the
    # post-warmup status. With --skip-server-warmup, SGLang has just set Up itself.
    prev_status = held_status.get("status") if held_status is not None else None
    if prev_status is None:
        prev_status = tokenizer_manager.server_status
    tokenizer_manager.server_status = ServerStatus.Starting

    try:
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
        try:
            from sglang.srt.entrypoints.http_server import kill_process_tree  # pyright: ignore[reportMissingImports,reportUnknownVariableType]
            kill_process_tree(os.getpid())
        except (ImportError, AttributeError) as err:
            logger.critical("SGLang kill_process_tree is not importable (%s); forcing exit.", err)
            os._exit(1)


def sglang_warmup_and_snapshot(
    server_args,
    execute_warmup_func: Callable,
    held_status: dict[str, Any],
    snapshot_provider: Optional[GKESnapshotProvider] = None,
) -> bool:
    """
    Runs SGLang's server warmup, then holds server_status at Starting for the snapshot.

    Registered as launch_server's execute_warmup_func. A successful warmup sets server_status to
    Up (or UnHealthy after a partial PD warmup) before SGLang calls launch_callback. When a
    snapshot will be taken, save that status in held_status and set Starting again, so readiness
    probes keep failing until sglang_snapshot_callback has finished the cycle and restored it.
    """
    # Run upstream native warmup procedure.
    if not execute_warmup_func(server_args):
        return False

    # Only hold when sglang_snapshot_callback will run the cycle and restore the status.
    # These are the checks it makes (and logs) before starting.
    provider = snapshot_provider if snapshot_provider is not None else get_snapshot_provider()
    if provider is None or not provider.is_available() or _release_discards_weights(server_args):
        return True
    tokenizer_manager = _get_tokenizer_manager()
    if tokenizer_manager is None:
        return True

    from sglang.srt.entrypoints.http_server import ServerStatus  # pyright: ignore[reportMissingImports,reportUnknownVariableType]

    held_status["status"] = tokenizer_manager.server_status
    tokenizer_manager.server_status = ServerStatus.Starting
    logger.info("Holding server_status at Starting until the snapshot completes.")
    return True
