#!/bin/bash
# Hotfix: universally align block table width to 128 tokens (vllm#50302)
# TODO: Remove this component when https://github.com/vllm-project/vllm/pull/50302 is merged.
#
# Shares one block-table width calculation between MRV1, MRV2, and the DSA
# indexer. Applies 128-token alignment and kernel-block splitting, preventing
# indexer buffer mismatches under alignment and DCP.
#
# Supersedes #48404, #50050, and the alignment portion of #43970.
# Patches 5 source files (no tests).

set -euo pipefail
VLLM=/usr/local/lib/python3.12/dist-packages/vllm

# ──────────────────────────────────────────────────────────────────
# 1/5  block_table.py
#      - Add import math
#      - Add get_block_table_width() function
#      - Replace inline alignment in MultiGroupBlockTable with get_block_table_width
# ──────────────────────────────────────────────────────────────────
python3 -c "
p='${VLLM}/v1/worker/block_table.py'
s=open(p).read()
ok=True

# 1a: add 'import math' at top
old='from enum import Enum'
new='import math\nfrom enum import Enum'
if old in s and 'import math' not in s: s=s.replace(old,new,1)
else: print('block_table.py: 1a (import math) not needed'); ok=False

# 1b: add get_block_table_width function after logger
old='logger = init_logger(__name__)\n\n\nclass SlotMappingMode'
new='''logger = init_logger(__name__)


def get_block_table_width(
    max_num_blocks: int,
    block_size: int,
    kernel_block_size: int | None = None,
    *,
    token_alignment: int | None = 128,
) -> int:
    \"\"\"Return the width after optional alignment and virtual block splitting.\"\"\"
    if kernel_block_size is None:
        kernel_block_size = block_size
    if block_size % kernel_block_size != 0:
        raise ValueError(
            f\"kernel_block_size {kernel_block_size} must divide \"
            f\"block_size {block_size} evenly\"
        )
    if token_alignment is not None:
        if token_alignment <= 0:
            raise ValueError(\"token_alignment must be positive\")
        block_alignment = token_alignment // math.gcd(token_alignment, block_size)
        max_num_blocks = cdiv(max_num_blocks, block_alignment) * block_alignment
    return max_num_blocks * block_size // kernel_block_size


class SlotMappingMode'''
if 'logger = init_logger(__name__)\n\n\nclass SlotMappingMode' in s: s=s.replace(old,new,1)
else: print('block_table.py: 1b (get_block_table_width fn) not needed'); ok=False

# 1c: replace inline alignment in MultiGroupBlockTable
old='''        # Align to a multiple of (128 / block_size) as required
        # by some attention backends such as TRTLLM (#39324)
        max_num_blocks = [
            cdiv(n, 128 // bs) * (128 // bs) if bs <= 128 else n
            for n, bs in zip(max_num_blocks, block_sizes)
        ]'''
new='''        max_num_blocks = [
            (
                get_block_table_width(n, block_size, token_alignment=None)
                if slot_mapping_mode == SlotMappingMode.NONE
                else get_block_table_width(n, block_size)
            )
            for n, block_size, slot_mapping_mode in zip(
                max_num_blocks, block_sizes, slot_mapping_modes
            )
        ]'''
if old in s: s=s.replace(old,new,1)
else: print('block_table.py: 1c (MultiGroupBlockTable alignment) not needed'); ok=False

if ok:
  open(p,'w').write(s); print('PATCHED block_table.py')
"

# ──────────────────────────────────────────────────────────────────
# 2/5  backend.py
#      - Add requires_block_table_width ClassVar
# ──────────────────────────────────────────────────────────────────
python3 -c "
p='${VLLM}/v1/attention/backend.py'
s=open(p).read()

old='    supports_update_block_table: bool = False\n\n    @abstractmethod'
new='    supports_update_block_table: bool = False\n    requires_block_table_width: ClassVar[bool] = False\n\n    @abstractmethod'
if old in s:
  open(p,'w').write(s.replace(old,new,1)); print('PATCHED backend.py')
else: print('backend.py: requires_block_table_width not needed')
"

# ──────────────────────────────────────────────────────────────────
# 3/5  indexer.py (MLA DSA)
#      - Remove cdiv and get_kv_cache_shard_count imports
#      - Add requires_block_table_width = True
#      - Change __init__ to accept block_table_width
#      - Fix expanded_block_table_buffer sizing
# ──────────────────────────────────────────────────────────────────
python3 -c "
p='${VLLM}/v1/attention/backends/mla/indexer.py'
s=open(p).read()
ok=True

# 3a: remove cdiv import
old='from vllm.utils.math_utils import cdiv\n'
if old in s: s=s.replace(old,'',1)
else: print('indexer.py: 3a (cdiv import) not needed'); ok=False

# 3b: remove get_kv_cache_shard_count import
old='from vllm.v1.worker.cp_utils import get_kv_cache_shard_count\n'
if old in s: s=s.replace(old,'',1)
else: print('indexer.py: 3b (cp_utils import) not needed'); ok=False

# 3c: add requires_block_table_width
old='    reorder_batch_threshold: int | None = None\n\n    @classmethod'
new='    reorder_batch_threshold: int | None = None\n    requires_block_table_width = True\n\n    @classmethod'
if old in s: s=s.replace(old,new,1)
else: print('indexer.py: 3c (requires_block_table_width) not needed'); ok=False

# 3d: change __init__ signature
old='    def __init__(self, *args, **kwargs):\n        super().__init__(*args, **kwargs)'
new='    def __init__(self, *args, block_table_width: int, **kwargs) -> None:\n        super().__init__(*args, **kwargs)'
if old in s: s=s.replace(old,new,1)
else: print('indexer.py: 3d (__init__ sig) not needed'); ok=False

# 3e: fix buffer sizing
old='''        max_num_blocks_per_req = cdiv(
            self.vllm_config.model_config.max_model_len,
            self.kv_cache_spec.block_size * get_kv_cache_shard_count(),
        )
        self.expanded_block_table_buffer = torch.zeros(
            (
                scheduler_config.max_num_batched_tokens,
                max_num_blocks_per_req,
            ),'''
new='''        self.expanded_block_table_buffer = torch.zeros(
            (scheduler_config.max_num_batched_tokens, block_table_width),'''
if old in s: s=s.replace(old,new,1)
else: print('indexer.py: 3e (buffer sizing) not needed'); ok=False

if ok:
  open(p,'w').write(s); print('PATCHED indexer.py')
"

# ──────────────────────────────────────────────────────────────────
# 4/5  gpu/model_runner.py
#      - Add get_block_table_width import
#      - Remove old inline alignment, add get_block_table_width calls
# ──────────────────────────────────────────────────────────────────
python3 -c "
p='${VLLM}/v1/worker/gpu/model_runner.py'
s=open(p).read()
ok=True

# 4a: add import
old='from vllm.v1.kv_cache_interface import KVCacheConfig, MambaSpec'
new='from vllm.v1.kv_cache_interface import KVCacheConfig, MambaSpec\nfrom vllm.v1.worker.block_table import get_block_table_width'
if old in s and 'from vllm.v1.worker.block_table import get_block_table_width' not in s:
  s=s.replace(old,new,1)
else: print('gpu/model_runner.py: 4a (import) not needed'); ok=False

# 4b: remove old alignment + restructure MambaSpec branch
old='''            # Align to a multiple of (128 / block_size) as required by some attention
            # backends such as TRTLLM (#39324)
            if spec.block_size <= 128:
                alignment = 128 // spec.block_size
                max_num_blocks = cdiv(max_num_blocks, alignment) * alignment
            # For Mamba/Hybrid Model, KVCaches need extra blocks for speculative tokens
            if isinstance(spec, MambaSpec):
                max_num_blocks = (
                    max_num_blocks if self.cache_config.enable_prefix_caching else 1
                ) + spec.num_speculative_blocks'''
new='''            # For Mamba/Hybrid Model, KVCaches need extra blocks for speculative tokens
            if isinstance(spec, MambaSpec):
                max_num_blocks = (
                    max_num_blocks if self.cache_config.enable_prefix_caching else 1
                ) + spec.num_speculative_blocks
                max_num_blocks = get_block_table_width(
                    max_num_blocks, spec.block_size, token_alignment=None
                )
            else:
                max_num_blocks = get_block_table_width(max_num_blocks, spec.block_size)'''
if old in s: s=s.replace(old,new,1)
else: print('gpu/model_runner.py: 4b (alignment restructure) not needed'); ok=False

if ok:
  open(p,'w').write(s); print('PATCHED gpu/model_runner.py')
"

# ──────────────────────────────────────────────────────────────────
# 5/5  utils.py
#      - Add get_block_table_width import
#      - Pass block_table_width to builders that require it
# ──────────────────────────────────────────────────────────────────
python3 -c "
p='${VLLM}/v1/worker/utils.py'
s=open(p).read()
ok=True

# 5a: add import
old='from vllm.v1.kv_cache_interface import (\n'
new='from vllm.v1.kv_cache_interface import (\n'
# Use a different anchor for import insertion
old2='from vllm.v1.worker.block_table import get_block_table_width'
if old2 not in s:
  # Find the right place to add the import
  anchor='from vllm.v1.kv_cache_interface import'
  if anchor in s:
    # Add after the kv_cache_interface import block
    import_line='\nfrom vllm.v1.worker.block_table import get_block_table_width'
    # Find the closing paren of the import
    idx = s.index(anchor)
    paren_idx = s.index(')', idx)
    s = s[:paren_idx+1] + import_line + s[paren_idx+1:]
  else:
    print('utils.py: 5a (import anchor) not found'); ok=False
else: print('utils.py: 5a (import) not needed'); ok=False

# 5b: add builder_kwargs with block_table_width
old='''        self.metadata_builders = [
            self.backend.get_builder_cls()(
                kv_cache_spec_builder,
                self.layer_names,
                vllm_config,
                device,
            )
            for _ in range(num_metadata_builders)
        ]'''
new='''        builder_cls = self.backend.get_builder_cls()
        builder_kwargs = {}
        if builder_cls.requires_block_table_width:
            max_num_blocks = self.kv_cache_spec.max_num_blocks_per_req(
                vllm_config, vllm_config.model_config.max_model_len
            )
            builder_kwargs[\"block_table_width\"] = get_block_table_width(
                max_num_blocks, self.kv_cache_spec.block_size, kernel_block_size
            )
        self.metadata_builders = [
            builder_cls(
                kv_cache_spec_builder,
                self.layer_names,
                vllm_config,
                device,
                **builder_kwargs,
            )
            for _ in range(num_metadata_builders)
        ]'''
if old in s: s=s.replace(old,new,1)
else: print('utils.py: 5b (builder_kwargs) not needed'); ok=False

if ok:
  open(p,'w').write(s); print('PATCHED utils.py')
"

echo "Hotfix 50302 complete."
