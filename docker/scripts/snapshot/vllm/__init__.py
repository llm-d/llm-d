"""
vLLM Snapshot Integration Package.
"""

from .wrapper import patch_vllm_lifespan

__all__ = ["patch_vllm_lifespan"]
