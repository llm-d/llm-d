"""
Launcher for SGLang with GKE Fast Pod Snapshotting support.

Delegates CLI invocation to sglang.srt.entrypoints.http_server.launch_server and plugs the
snapshot lifecycle into its two warmup hooks (see wrapper.py):

- execute_warmup_func: sglang_warmup_and_snapshot runs SGLang's server warmup, then holds the
  server at ServerStatus.Starting.
- launch_callback: sglang_snapshot_callback runs the sleep/snapshot/wake cycle, then restores
  the post-warmup status.

SGLang calls launch_callback with or without --skip-server-warmup, so the snapshot is taken
either way, before the server reports ready.
"""

from __future__ import annotations

import functools
import sys
from typing import Any

from .wrapper import sglang_snapshot_callback, sglang_warmup_and_snapshot


def main() -> None:
    try:
        from sglang.srt.entrypoints.http_server import (  # pyright: ignore[reportMissingImports,reportUnknownVariableType]
            _execute_server_warmup,
            launch_server,
        )
        from sglang.srt.server_args import prepare_server_args  # pyright: ignore[reportMissingImports,reportUnknownVariableType]
    except ImportError as err:
        raise RuntimeError(
            "sglang must be installed to run snapshot launcher (python3 -m docker.scripts.snapshot.sglang.launcher)"
        ) from err

    server_args = prepare_server_args(sys.argv[1:])
    # Same URL SGLang's own warmup uses (https with --ssl-certfile, loopback for
    # 0.0.0.0/::, bracketed IPv6). Don't read server_args.host/.port directly:
    # SGLang injects those fields at runtime, so type checkers can't see them.
    server_url = server_args.url()

    # The warmup hook saves the post-warmup server status here; the snapshot callback restores it.
    held_status: dict[str, Any] = {}

    launch_server(
        server_args,
        execute_warmup_func=functools.partial(
            sglang_warmup_and_snapshot,
            execute_warmup_func=_execute_server_warmup,
            held_status=held_status,
        ),
        launch_callback=functools.partial(
            sglang_snapshot_callback,
            server_url=server_url,
            server_args=server_args,
            held_status=held_status,
        ),
    )


if __name__ == "__main__":
    main()
