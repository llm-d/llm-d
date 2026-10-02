"""
Snapshot Package Initialization.
"""

from .providers import (
    GKESnapshotProvider,
    SnapshotError,
    get_snapshot_provider,
)

__all__ = [
    "GKESnapshotProvider",
    "SnapshotError",
    "get_snapshot_provider",
]

try:
    from .vllm import patch_vllm_lifespan

    __all__.append("patch_vllm_lifespan")
except ImportError:
    pass

try:
    from .sglang import patch_sglang_wait_and_warmup

    __all__.append("patch_sglang_wait_and_warmup")
except ImportError:
    pass
