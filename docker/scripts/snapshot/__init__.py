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
    from .sglang import sglang_snapshot_callback, sglang_warmup_and_snapshot

    __all__.extend(["sglang_snapshot_callback", "sglang_warmup_and_snapshot"])
except ImportError:
    pass
