"""
SGLang Wrapper Entrypoint for GKE Fast Pod Snapshotting (docker/scripts/snapshot/sglang/wrapper.py).

Note: Scope is single-rank deployments (DP/multi-rank barrier coordination is not covered).

Provides sglang_warmup_and_snapshot (invoked via launch_server's execute_warmup_func) and
sglang_snapshot_callback to execute the snapshot lifecycle before the server flips to ServerStatus.Up:
1. Run standard server warmup while keeping tokenizer_manager.server_status at ServerStatus.Starting.
2. Release physical VRAM via HTTP POST to /release_memory_occupation (tags=["weights", "kv_cache"]).
3. Trigger the snapshot checkpoint via snapshot_provider.trigger() (clearing model weights cache on disk).
4. Re-allocate physical VRAM via HTTP POST to /resume_memory_occupation (tags=["weights", "kv_cache"]) upon restore.
5. Flip tokenizer_manager.server_status to ServerStatus.Up only after memory occupation is resumed.
6. Terminate the server process tree via kill_process_tree if the sleep/wake cycle fails.
"""

from __future__ import annotations

import logging
import os
import requests
from typing import Callable, Optional

from ..providers import GKESnapshotProvider, get_snapshot_provider

logger = logging.getLogger("sglang.snapshot.wrapper")


def _set_server_status(status_name: str) -> None:
    """Update SGLang's tokenizer_manager.server_status if initialized."""
    try:
        from sglang.srt.entrypoints import http_server  # pyright: ignore[reportMissingImports,reportUnknownVariableType]

        global_state = getattr(http_server, "_global_state", None)
        tokenizer_manager = getattr(global_state, "tokenizer_manager", None)
        server_status_enum = getattr(http_server, "ServerStatus", None)
        if tokenizer_manager is not None and server_status_enum is not None:
            tokenizer_manager.server_status = getattr(server_status_enum, status_name)
    except ImportError:
        pass


def sglang_snapshot_callback(
    server_url: str,
    snapshot_provider: Optional[GKESnapshotProvider] = None,
) -> bool:
    """
    Executes the snapshot cycle after SGLang warmup and before the server flips to ServerStatus.Up.
    """
    if snapshot_provider is None:
        snapshot_provider = get_snapshot_provider()

    if snapshot_provider is None:
        logger.info(
            "No snapshot provider configured (SNAPSHOT_PROVIDER is unset or empty). Snapshotting is disabled."
        )
        return True

    if hasattr(snapshot_provider, "is_available") and not snapshot_provider.is_available():
        logger.warning(
            "Pod snapshot trigger not available (checkpoint file '%s' is not writable). Skipping snapshot.",
            getattr(snapshot_provider, "proc_path", "unknown"),
        )
        return True

    # Ensure server_status remains Starting during sleep, checkpoint, and wake-up so
    # readiness/health probes (/health, /ready) return 503 until memory is restored.
    _set_server_status("Starting")

    try:
        # STEP 1: Release GPU memory occupation (KV cache), move weights, save addresses
        logger.info("[Control Plane] Sleep signal received. Releasing memory occupation...")
        resp = requests.post(
            f"{server_url}/release_memory_occupation",
            json={"tags": ["weights", "kv_cache"]},
            timeout=600,
        )
        resp.raise_for_status()

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
                "Snapshot checkpointing failed: %s. Resuming sglang service without checkpoint.",
                e,
                exc_info=True,
            )
        finally:
            # STEP 3: Regardless of success or failure, resume GPU memory occupation, restore addresses, move weights, allocate VRAM
            logger.info("Resuming sglang memory occupation...")
            resp = requests.post(
                f"{server_url}/resume_memory_occupation",
                json={"tags": ["weights", "kv_cache"]},
                timeout=600,
            )
            resp.raise_for_status()
            logger.info("SGLang memory occupation resumed successfully.")

        # STEP 4: Flip server status to Up only after memory occupation has been restored
        _set_server_status("Up")
        return True
    except Exception as e:
        logger.error("Sleep/wake cycle failed; killing server: %s", e, exc_info=True)
        try:
            from sglang.srt.entrypoints.http_server import kill_process_tree  # pyright: ignore[reportMissingImports,reportUnknownVariableType]
            kill_process_tree(os.getpid())
        except ImportError:
            logger.critical(
                "Failed to import kill_process_tree. Forcing exit."
            )
            os._exit(1)
        return False


def sglang_warmup_and_snapshot(
    server_args,
    snapshot_provider: Optional[GKESnapshotProvider] = None,
    execute_warmup_func: Optional[Callable] = None,
) -> bool:
    """
    Runs SGLang's standard server warmup followed by the sleep/snapshot/wake cycle
    before _wait_and_warmup marks the server as ready.
    """
    if execute_warmup_func is None:
        from sglang.srt.entrypoints.http_server import _execute_server_warmup  # pyright: ignore[reportMissingImports,reportUnknownVariableType]

        warmup_fn: Callable = _execute_server_warmup  # pyright: ignore[reportAssignmentType]
    else:
        warmup_fn = execute_warmup_func

    if not warmup_fn(server_args):
        return False

    server_url = (
        server_args.url()
        if hasattr(server_args, "url")
        else f"http://{server_args.host}:{server_args.port}"
    )
    return sglang_snapshot_callback(
        server_url=server_url,
        snapshot_provider=snapshot_provider,
    )