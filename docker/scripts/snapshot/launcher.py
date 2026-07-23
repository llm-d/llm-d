"""
Launcher for vLLM with GKE Fast Pod Snapshotting support.

Hooks vllm.entrypoints.openai.api_server.build_app to wrap the FastAPI application
with patch_vllm_lifespan, then delegates CLI invocation to vllm.entrypoints.cli.main.
"""

from __future__ import annotations

import sys

from .vllm.wrapper import patch_vllm_lifespan

try:
    from vllm.entrypoints.openai import api_server

    _orig_build_app = api_server.build_app

    def _build_app(*args, **kwargs):
        return patch_vllm_lifespan(_orig_build_app(*args, **kwargs))

    api_server.build_app = _build_app
except ImportError:
    api_server = None


def main() -> None:
    try:
        from vllm.entrypoints.cli.main import main as vllm_main
    except ImportError as err:
        raise RuntimeError(
            "vLLM must be installed to run snapshot launcher (python3 -m docker.scripts.snapshot.launcher)"
        ) from err

    sys.argv = ["vllm", "serve", *sys.argv[1:]]
    vllm_main()


if __name__ == "__main__":
    main()
