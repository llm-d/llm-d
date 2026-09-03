"""
vLLM Wrapper Entrypoint for GKE Fast Pod Snapshotting (docker/scripts/snapshot/vllm/wrapper.py).

Note: Scope is single-rank deployments (DP/multi-rank barrier coordination is not covered).

Patches vLLM's FastAPI application lifespan context manager to:
1. Run standard engine initialization (loads weights, compiles graphs).
2. Release physical VRAM maps via engine.sleep(level=1).
3. Trigger the snapshot checkpoint (clearing model weights cache on disk).
4. Re-allocate physical VRAM maps via engine.wake_up() upon container restore.
5. Yield control back to uvicorn to bind TCP ports and begin serving traffic.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from typing import Optional

from ..providers import (
    GKESnapshotProvider,
    get_snapshot_provider,
)

try:
    from vllm.logger import init_logger

    logger = init_logger("vllm.snapshot.wrapper")
except ImportError:
    logger = logging.getLogger("vllm.snapshot.wrapper")


def patch_vllm_lifespan(app, snapshot_provider: Optional[GKESnapshotProvider] = None):
    """
    Patches the FastAPI app lifespan context manager for vLLM snapshotting (single-rank scope).

    Args:
        app: The vLLM FastAPI application instance.
        snapshot_provider: Snapshot provider instance. Defaults to the provider configured
            by the SNAPSHOT_PROVIDER environment variable (or None if unset/disabled).
    """
    if snapshot_provider is None:
        # Get the configured snapshot provider, if any
        snapshot_provider = get_snapshot_provider()

    if snapshot_provider is None:
        logger.info(
            "No snapshot provider configured (SNAPSHOT_PROVIDER is unset or empty). Snapshotting is disabled."
        )
        return app

    original_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def patched_lifespan(app_inst):
        async with original_lifespan(app_inst) as state:
            if hasattr(snapshot_provider, "is_available") and not snapshot_provider.is_available():
                logger.warning(
                    "Pod snapshot trigger not available (checkpoint file '%s' is not writable). Skipping snapshot.",
                    getattr(snapshot_provider, "proc_path", "unknown"),
                )
                yield state
                return

            engine = getattr(app_inst.state, "engine_client", None)

            # Release physical VRAM maps (keeps virtual memory addresses stable)
            if engine and hasattr(engine, "sleep"):
                logger.info("Executing engine.sleep(level=1) to release physical VRAM...")
                await engine.sleep(level=1)
            else:
                logger.error("vLLM engine does not support sleep(level=1); physical VRAM maps were not released before snapshot.")

            logger.info("Triggering snapshot checkpoint...")
            try:
                await asyncio.to_thread(snapshot_provider.trigger)
                logger.info("Snapshot checkpoint created successfully.")
            except Exception as e:
                logger.error("Snapshot checkpointing failed: %s. Resuming vLLM service without checkpoint.", e, exc_info=True)

            # --- PROCESS RESTORED ON WAKE-UP regardless if snapshot was successful ---
            logger.info("Process restored from snapshot checkpoint. Resuming engine...")

            # Re-allocate physical VRAM maps
            if engine and hasattr(engine, "wake_up"):
                logger.info("Executing engine.wake_up() to restore VRAM...")
                await engine.wake_up()
            else:
                logger.error("vLLM engine does not support wake_up(); physical VRAM maps were not re-allocated after snapshot.")

            yield state

    app.router.lifespan_context = patched_lifespan
    logger.info("Successfully patched vLLM FastAPI lifespan context for GKE snapshotting.")
    return app

