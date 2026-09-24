"""
Launcher for SGLang with GKE Fast Pod Snapshotting support.

Hooks sglang.srt.entrypoints.http_server._wait_and_warmup with
patch_sglang_wait_and_warmup, then delegates CLI invocation to sglang.launch_server.
"""

from __future__ import annotations

import logging
import sys

logger = logging.getLogger("sglang.snapshot.launcher")


def _hook_api_server() -> bool:
    """Hooks sglang.srt.entrypoints.http_server._wait_and_warmup for GKE pod snapshotting (single-rank scope)."""
    try:
        from .wrapper import patch_sglang_wait_and_warmup

        patch_sglang_wait_and_warmup()
        return True
    except (ImportError, AttributeError) as err:
        logger.warning(
            "SGLang server is not importable (%s); no snapshot will be taken.",
            err,
        )
        return False


# Module level execution so child processes also inherit the patched _wait_and_warmup
_hook_api_server()


def main() -> None:
    try:
        from sglang.srt.entrypoints.http_server import launch_server
        from sglang.srt.server_args import prepare_server_args
    except ImportError as err:
        raise RuntimeError(
            "sglang must be installed to run snapshot launcher (python3 -m docker.scripts.snapshot.sglang.launcher)"
        ) from err

    server_args = prepare_server_args(sys.argv[1:])
    launch_server(server_args)


if __name__ == "__main__":
    main()
