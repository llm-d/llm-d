"""
Launcher for SGLang with GKE Fast Pod Snapshotting support.

Delegates CLI invocation to sglang.srt.entrypoints.http_server.launch_server. When a snapshot
provider is configured and available (and both --enable-memory-saver and
--enable-weights-cpu-backup are set), plugs the snapshot lifecycle into launch_server's two
warmup hooks (see wrapper.py):

- execute_warmup_func: sglang_warmup_and_hold runs SGLang's server warmup, then holds the
  server at ServerStatus.Starting.
- launch_callback: sglang_snapshot_callback runs the sleep/snapshot/wake cycle, then restores
  the post-warmup status.

SGLang calls launch_callback with or without --skip-server-warmup, so the snapshot is taken
either way, before the server reports ready.
"""

from __future__ import annotations

import functools
import logging
import os
import sys
from typing import Any, Optional

from ..providers import GKESnapshotProvider, get_snapshot_provider
from .wrapper import sglang_snapshot_callback, sglang_warmup_and_hold

logger = logging.getLogger("sglang.snapshot.launcher")


def _get_snapshot_provider_for_launch(server_args) -> Optional[GKESnapshotProvider]:
    snapshot_provider = get_snapshot_provider()
    if snapshot_provider is None:
        logger.info(
            "No snapshot provider configured (SNAPSHOT_PROVIDER is unset or empty). Snapshotting is disabled."
        )
        return None

    if not snapshot_provider.is_available():
        logger.warning(
            "Pod snapshot trigger not available (checkpoint file '%s' is not writable). Skipping snapshot.",
            snapshot_provider.proc_path,
        )
        return None

    # Without --enable-memory-saver, SGLang frees no GPU memory on release; without
    # --enable-weights-cpu-backup, SGLang throws the weights away instead of copying them to CPU memory.
    if not (server_args.enable_memory_saver and server_args.enable_weights_cpu_backup):
        logger.warning(
            "Both --enable-memory-saver and --enable-weights-cpu-backup are required for SGLang "
            "pod snapshotting; no snapshot will be taken."
        )
        return None

    return snapshot_provider


def main() -> None:
    try:
        from sglang.launch_server import run_server  # pyright: ignore[reportMissingImports,reportUnknownVariableType]
        from sglang.srt.entrypoints.http_server import (  # pyright: ignore[reportMissingImports,reportUnknownVariableType]
            _execute_server_warmup,
            launch_server,
        )
        from sglang.srt.plugins import load_plugins  # pyright: ignore[reportMissingImports,reportUnknownVariableType]
        from sglang.srt.server_args import prepare_server_args  # pyright: ignore[reportMissingImports,reportUnknownVariableType]
        from sglang.srt.utils import kill_process_tree  # pyright: ignore[reportMissingImports,reportUnknownVariableType]
    except ImportError as err:
        raise RuntimeError(
            "sglang must be installed to run snapshot launcher (python3 -m docker.scripts.snapshot.sglang.launcher)"
        ) from err

    load_plugins()
    server_args = prepare_server_args(sys.argv[1:])
    snapshot_provider = _get_snapshot_provider_for_launch(server_args)
    try:
        if snapshot_provider is None:
            run_server(server_args)
            return

        # The warmup hook saves the post-warmup server status here; the snapshot callback restores it.
        held_status: dict[str, Any] = {}

        launch_server(
            server_args,
            execute_warmup_func=functools.partial(
                sglang_warmup_and_hold,
                execute_warmup_func=_execute_server_warmup,
                held_status=held_status,
            ),
            launch_callback=functools.partial(
                sglang_snapshot_callback,
                snapshot_provider=snapshot_provider,
                server_args=server_args,
                held_status=held_status,
            ),
        )
    finally:
        kill_process_tree(os.getpid(), include_parent=False)


if __name__ == "__main__":
    main()
