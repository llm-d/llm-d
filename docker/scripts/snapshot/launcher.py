"""
Launcher for vLLM with GKE Fast Pod Snapshotting support.

Hooks vLLM's build_app (in vllm.entrypoints.launchers on v0.29.0+ or
vllm.entrypoints.openai.api_server on older releases) to wrap the FastAPI
application with patch_vllm_lifespan, then delegates CLI invocation to
vllm.entrypoints.cli.main.
"""

from __future__ import annotations

import importlib
import logging
import sys
from typing import Optional

from .vllm.wrapper import patch_vllm_lifespan

try:
    from vllm.logger import init_logger

    logger = init_logger("vllm.snapshot.launcher")
except ImportError:
    logger = logging.getLogger("vllm.snapshot.launcher")


def _hook_api_server() -> bool:
    """Hooks vLLM's build_app to patch the FastAPI lifespan context."""
    last_err: Optional[Exception] = None
    for mod_name in (
        "vllm.entrypoints.launchers.app",
        "vllm.entrypoints.openai.api_server",
    ):
        try:
            target = importlib.import_module(mod_name)
        except Exception as err:
            last_err = err
            continue
        if callable(getattr(target, "build_app", None)):
            break
        last_err = AttributeError(f"module '{mod_name}' has no callable 'build_app'")
    else:
        logger.warning(
            "vLLM API server is not importable (%s); no snapshot will be taken.",
            last_err,
        )
        return False

    _orig_build_app = target.build_app

    def _build_app(*args, **kwargs):
        return patch_vllm_lifespan(_orig_build_app(*args, **kwargs))

    setattr(target, "build_app", _build_app)
    for name, mod in list(sys.modules.items()):
        if (
            name.startswith("vllm.entrypoints")
            and getattr(mod, "build_app", None) is _orig_build_app
        ):
            setattr(mod, "build_app", _build_app)
    return True


# Module level execution so child processes also inherit the patched build_app
_hook_api_server()


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
