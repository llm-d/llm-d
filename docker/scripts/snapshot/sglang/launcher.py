"""
Launcher for SGLang with GKE Fast Pod Snapshotting support.

Delegates CLI invocation to sglang.srt.entrypoints.http_server.launch_server
with sglang_warmup_and_snapshot registered as execute_warmup_func so the
sleep/snapshot/wake cycle completes before the server flips to ServerStatus.Up.
"""

from __future__ import annotations

import functools
import logging
import sys

logger = logging.getLogger("sglang.snapshot.launcher")


def main() -> None:
    try:
        from sglang.srt.entrypoints.http_server import (  # pyright: ignore[reportMissingImports,reportUnknownVariableType]
            _execute_server_warmup,
            launch_server,
        )
        from sglang.srt.server_args import prepare_server_args  # pyright: ignore[reportMissingImports,reportUnknownVariableType]
        from .wrapper import sglang_warmup_and_snapshot
    except ImportError as err:
        raise RuntimeError(
            "sglang must be installed to run snapshot launcher (python3 -m docker.scripts.snapshot.sglang.launcher)"
        ) from err

    server_args = prepare_server_args(sys.argv[1:])
    warmup_func = functools.partial(
        sglang_warmup_and_snapshot,
        execute_warmup_func=_execute_server_warmup,
    )
    launch_server(server_args, execute_warmup_func=warmup_func)


if __name__ == "__main__":
    main()

