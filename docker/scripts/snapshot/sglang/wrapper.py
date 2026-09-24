"""
SGLang Wrapper Entrypoint for GKE Fast Pod Snapshotting (docker/scripts/snapshot/sglang/wrapper.py).

Note: Scope is single-rank deployments (DP/multi-rank barrier coordination is not covered).

Patches sglang.srt.entrypoints.http_server._wait_and_warmup to:
1. Run standard server warmup (captures CUDA graphs, pre-allocates VRAM, and freezes GC).
2. Release physical VRAM via tokenizer_manager.release_memory_occupation(tags=["weights", "kv_cache"]).
3. Trigger the snapshot checkpoint via snapshot_provider.trigger() (clearing model weights cache on disk).
4. Re-allocate physical VRAM via tokenizer_manager.resume_memory_occupation(tags=["weights", "kv_cache"]) upon restore.
5. Mark tokenizer_manager.server_status = ServerStatus.Up and invoke launch_callback to begin serving traffic.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Optional

from ..providers import (
    GKESnapshotProvider,
    get_snapshot_provider,
)


logger = logging.getLogger("sglang.snapshot.wrapper")


def patch_sglang_wait_and_warmup(snapshot_provider: Optional[GKESnapshotProvider] = None):
    """
    Patches sglang's http_server._wait_and_warmup for GKE pod snapshotting (single-rank scope).

    Args:
        snapshot_provider: Snapshot provider instance. Defaults to the provider configured
            by the SNAPSHOT_PROVIDER environment variable (or None if unset/disabled).
    """
    from sglang.srt.entrypoints import http_server
    from sglang.srt.entrypoints.http_server import (
        ServerStatus,
        _execute_server_warmup,
        _freeze_gc_after_server_warmup,
        _wait_weights_ready,
        get_exec,
        get_model,
        get_observability,
        get_serving,
        kill_process_tree,
    )

    if snapshot_provider is None:
        # Get the configured snapshot provider, if any
        snapshot_provider = get_snapshot_provider()

    if snapshot_provider is None:
        logger.info(
            "No snapshot provider configured (SNAPSHOT_PROVIDER is unset or empty). Snapshotting is disabled."
        )
        return

    original_wait_and_warmup = http_server._wait_and_warmup

    def patched_wait_and_warmup(
        server_args,
        launch_callback=None,
        execute_warmup_func=_execute_server_warmup,
    ):
        # This is a blocking function not asynchronous context.
        if hasattr(snapshot_provider, "is_available") and not snapshot_provider.is_available():
            logger.warning(
                "Pod snapshot trigger not available (checkpoint file '%s' is not writable). Skipping snapshot.",
                getattr(snapshot_provider, "proc_path", "unknown"),
            )
            return original_wait_and_warmup(
                server_args,
                launch_callback=launch_callback,
                execute_warmup_func=execute_warmup_func,
            )

        if get_model().checkpoint_engine_wait_weights_before_ready:
            _wait_weights_ready()

        # Joiner schedulers are served through the primary after adoption.
        skip_elastic_joiner_warmup = get_exec().moe.is_ep_scale_joiner
        if skip_elastic_joiner_warmup:
            logger.debug(
                "[Elastic EP] Skipping server warmup for elastic joiner (ep_join_mode=%s)",
                get_exec().moe.ep_join_mode,
            )

        # Warmup captures CUDA graphs and pre-allocates VRAM
        if not get_serving().skip_server_warmup and not skip_elastic_joiner_warmup:
            if not execute_warmup_func(server_args):
                return
        else:
            logger.warning("[Control Plane] Warmup skipped.")

        _freeze_gc_after_server_warmup(server_args)

        tokenizer_manager = http_server._global_state.tokenizer_manager

        # SLEEP
        logger.info("[Control Plane] Sleep signal received. Releasing memory occupation...")
        tokenizer_manager.release_memory_occupation(tags=["weights", "kv_cache"])

        logger.info("Triggering snapshot checkpoint...")
        try:
            snapshot_provider.trigger()
            logger.info("Snapshot checkpoint created successfully.")
        except Exception as e:
            logger.error("Snapshot checkpointing failed: %s. Resuming sglang service without checkpoint.", e, exc_info=True)

        # WAKE
        logger.info("[Control Plane] Wake signal received. Resuming memory occupation...")
        tokenizer_manager.resume_memory_occupation(tags=["weights", "kv_cache"])

        # The server is ready for requests
        # Only set the server to ready once it has woken up again. This satisfies the readiness prob
        tokenizer_manager.server_status = ServerStatus.Up
        logger.info("The server is fired up and ready to roll!")

        if get_observability().debug_tensor_dump_input_file:
            kill_process_tree(os.getpid())

        if launch_callback is not None:
            launch_callback()

    http_server._wait_and_warmup = patched_wait_and_warmup
    logger.info("Successfully patched SGLang _wait_and_warmup for GKE snapshotting.")

