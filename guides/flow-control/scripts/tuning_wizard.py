#!/usr/bin/env python3
"""
Flow Control Tuning Wizard

Calculates optimal concurrency and lookahead buffer limits for Gateway deployments.
Evaluates the two fundamental constraints of LLM serving:
  1. Compute SLA Constraint: Evaluated empirically using Little's Law.
  2. Memory Capacity Constraint: Evaluated analytically using KV cache size and sequence lengths.

The system's true concurrency limit is determined by the active bottleneck (the minimum of the two).
"""

import argparse
import math
import sys
from typing import Tuple, Optional

# ==========================================
# UI Helpers
# ==========================================

def get_float_input(prompt: str, default: Optional[float] = None) -> Optional[float]:
    while True:
        val = input(prompt)
        if not val:
            return default
        try:
            return float(val)
        except ValueError:
            print("  [!] Please enter a valid number.")

def get_int_input(prompt: str, default: Optional[int] = None) -> Optional[int]:
    while True:
        val = input(prompt)
        if not val:
            return default
        try:
            return int(val)
        except ValueError:
            print("  [!] Please enter a valid integer.")

def print_header(title: str) -> None:
    print(f"\n{'-'*60}\n{title}\n{'-'*60}")

# ==========================================
# Core Mathematical Logic
# ==========================================

def calculate_compute_constraint(throughput: float, latency_sec: float) -> int:
    """Calculates concurrency threshold using Little's Law (L = lambda * W)."""
    return math.floor(throughput * latency_sec)

def calculate_memory_constraint(
    gpu_blocks: int, block_size: int, paged_attention_efficiency: float,
    shared_prefix: int, enable_prefix_caching: bool,
    isl_mean: float, isl_std: float, osl_mean: float, osl_std: float,
    correlation_coefficient: float, z_score: float
) -> Tuple[int, float, float]:
    """Calculates max concurrency before KV cache exhaustion using the Central Limit Theorem (CLT).
    Returns: (memory_limit, marginal_isl, coefficient_of_variation)"""
    effective_tokens = gpu_blocks * block_size * paged_attention_efficiency

    if enable_prefix_caching:
        available_tokens = max(0, effective_tokens - shared_prefix)
        marginal_isl = max(0, isl_mean - shared_prefix)
    else:
        available_tokens = effective_tokens
        marginal_isl = isl_mean

    isl_std_eff = isl_std if marginal_isl > 0 else 0.0

    # VRAM footprint modeling (Mean cost over an autoregressive request's lifetime)
    mu_footprint = marginal_isl + (osl_mean / 2.0)
    var_output = (osl_std**2 / 3.0) + (osl_mean**2 / 12.0)
    var_footprint = (isl_std_eff**2) + var_output + (correlation_coefficient * isl_std_eff * osl_std)
    sigma_footprint = math.sqrt(var_footprint)

    cv = sigma_footprint / mu_footprint if mu_footprint > 0 else 0.0

    a = mu_footprint
    b = z_score * sigma_footprint
    c = -available_tokens

    discriminant = (b**2) - (4 * a * c)
    if discriminant < 0 or a <= 0:
        raise ValueError("Workload variance is too high for available VRAM.")

    x = (-b + math.sqrt(discriminant)) / (2 * a)
    return int(x**2), marginal_isl, cv

def calculate_lookahead_buffer(active_batch: int, max_num_batched_tokens: int, isl_mean: Optional[float]) -> int:
    """Sizes the engine's local queue to ensure continuous batching doesn't starve,
    capped at 15% of the active batch."""
    max_allowed_buffer = math.ceil(active_batch * 0.15)

    if isl_mean is None:
        # If running in Compute-Only mode, fallback to the 15% safety cap
        return max(1, max_allowed_buffer)

    effective_isl = max(1.0, isl_mean)
    buffer_size = math.ceil(max_num_batched_tokens / effective_isl)
    return max(1, min(buffer_size, max_allowed_buffer))

# ==========================================
# Main Execution / Runtime Loop
# ==========================================

def main():
    parser = argparse.ArgumentParser(description="Flow Control Tuning Wizard")

    group_compute = parser.add_argument_group("Compute SLA Constraints")
    group_compute.add_argument("--throughput", type=float, help="Mean throughput (RPS)")
    group_compute.add_argument("--latency-sec", type=float, dest="latency_sec", help="Mean end-to-end latency (seconds)")

    group_memory = parser.add_argument_group("Memory Capacity Constraints")
    group_memory.add_argument("--gpu-blocks", type=int, dest="gpu_blocks", help="Available KV blocks from engine logs")
    group_memory.add_argument("--block-size", type=int, dest="block_size", default=16, help="Tokens per KV block")
    group_memory.add_argument("--isl-mean", type=float, dest="isl_mean", help="Mean Input Sequence Length")
    group_memory.add_argument("--isl-std", type=float, dest="isl_std", help="StdDev of ISL")
    group_memory.add_argument("--osl-mean", type=float, dest="osl_mean", help="Mean Output Sequence Length")
    group_memory.add_argument("--osl-std", type=float, dest="osl_std", help="StdDev of OSL")

    group_engine = parser.add_argument_group("Engine Architecture")
    group_engine.add_argument("--shared-prefix", type=int, dest="shared_prefix", default=0, help="Static system prompt length")
    group_engine.add_argument("--enable-prefix-caching", action="store_true", help="Set if caching is ON")
    group_engine.add_argument("--max-num-batched-tokens", type=int, dest="max_num_batched_tokens", default=2048, help="Engine prefill budget")

    group_adv = parser.add_argument_group("Advanced Statistical Parameters")
    group_adv.add_argument("--z-score", type=float, dest="z_score", default=2.0, help="Statistical safety margin")
    group_adv.add_argument("--paged-attention-efficiency", type=float, dest="paged_attention_efficiency", default=0.90, help="VRAM fragmentation buffer")
    group_adv.add_argument("--correlation-coefficient", type=float, dest="correlation_coefficient", default=0.0, help="ISL/OSL correlation")

    args = parser.parse_args()
    interactive = len(sys.argv) == 1

    if interactive:
        print("=== LLM Capacity Tuning Wizard ===")
        print("Press Enter to accept defaults where available.\n")

        mode = input("Select calculation mode (compute/memory/both) [both]: ") or "both"

        if mode in ["compute", "both"]:
            print_header("Step 1: Compute SLA Constraint")
            args.throughput = get_float_input("  > Mean throughput (RPS): ")
            args.latency_sec = get_float_input("  > Mean end-to-end latency (SECONDS): ")

        if mode in ["memory", "both"]:
            print_header("Step 2: Memory Capacity Constraint")
            args.gpu_blocks = get_int_input("  > Total available KV cache blocks (# GPU blocks): ")
            args.block_size = get_int_input("  > Tokens per KV block[16]: ", default=16)
            args.isl_mean = get_float_input("  > Mean Input Sequence Length (tokens): ")

            # Exponential distribution fallback
            val_isl = get_float_input("  > StdDev of Input (leave blank to assume exponential distribution): ")
            args.isl_std = val_isl if val_isl is not None else args.isl_mean

            args.osl_mean = get_float_input("  > Mean Output Sequence Length (tokens): ")
            val_osl = get_float_input("  > StdDev of Output (leave blank to assume exponential distribution): ")
            args.osl_std = val_osl if val_osl is not None else args.osl_mean

            print("\n  [Engine Context & Caching]")
            args.shared_prefix = get_int_input("  > Length of shared system prompt [0]: ", default=0)

            if args.shared_prefix > 0:
                print("  [!] WARNING: Enabling caching here without enabling it on the engine causes OOMs.")
                ans = input("  > Is prefix caching explicitly enabled on your engine? (y/n) [n]: ") or "n"
                args.enable_prefix_caching = ans.lower().startswith('y')
            else:
                args.enable_prefix_caching = False

            args.max_num_batched_tokens = get_int_input("  > Engine chunked prefill budget (--max-num-batched-tokens)[2048]: ", default=2048)

        print_header("Step 3: Statistical Safety")
        args.z_score = get_float_input("  > Z-score safety margin (e.g., 2.0 for 95% confidence) [2.0]: ", default=2.0)

    # Fallbacks for CLI args
    if args.isl_std is None and args.isl_mean is not None: args.isl_std = args.isl_mean
    if args.osl_std is None and args.osl_mean is not None: args.osl_std = args.osl_mean

    # Validation
    run_compute = args.throughput is not None and args.latency_sec is not None
    run_memory = all(v is not None for v in[args.gpu_blocks, args.isl_mean, args.isl_std, args.osl_mean, args.osl_std])

    if not run_compute and not run_memory:
        sys.exit("\nError: You must provide arguments for Compute, Memory, or both.")

    compute_limit, memory_limit, marginal_isl, cv = None, None, None, 0.0

    if run_compute:
        if args.latency_sec > 100:
            print("  [!] WARNING: Latency is high. Ensure it's in seconds, not ms.")
        compute_limit = calculate_compute_constraint(args.throughput, args.latency_sec)

    if run_memory:
        if args.shared_prefix > args.isl_mean:
            print("  [!] WARNING: Shared prefix is larger than mean ISL. Capping to mean ISL.")
            args.shared_prefix = int(args.isl_mean)
        try:
            memory_limit, marginal_isl, cv = calculate_memory_constraint(
                args.gpu_blocks, args.block_size, args.paged_attention_efficiency,
                args.shared_prefix, args.enable_prefix_caching,
                args.isl_mean, args.isl_std, args.osl_mean, args.osl_std,
                args.correlation_coefficient, args.z_score
            )
        except ValueError as e:
            sys.exit(f"\nError: {e}")

    # Bottleneck Resolution
    if run_compute and run_memory:
        safe_active_batch = min(compute_limit, memory_limit)
        bottleneck = "Compute (Latency SLAs)" if compute_limit <= memory_limit else "Memory (KV Cache)"
    elif run_compute:
        safe_active_batch = compute_limit
        bottleneck = "Compute Only (Warning: OOM Risk)"
    else:
        safe_active_batch = memory_limit
        bottleneck = "Memory Only (Warning: Latency Risk)"

    if safe_active_batch < 1:
        sys.exit("\nError: Safe active batch is < 1. Hardware cannot support this workload.")

    buffer_size = calculate_lookahead_buffer(safe_active_batch, args.max_num_batched_tokens, args.isl_mean)
    gateway_concurrency = safe_active_batch + buffer_size

    # Results Block
    print_header("TUNING WIZARD RESULTS")
    if run_compute: print(f"Calculated Compute Limit: {compute_limit} requests")
    if run_memory:  print(f"Calculated Memory Limit:  {memory_limit} requests")
    print(f"Active Bottleneck:        {bottleneck}")
    print("-" * 60)
    print(f"Engine Target (N_active): {safe_active_batch}")
    print(f"Lookahead Buffer (B):     {buffer_size} (Dynamic chunked prefill capacity)")
    print(f"Gateway Max Concurrency:  {gateway_concurrency} (N_active + B)")

    # Heavy-Tail Warnings
    if (run_compute and compute_limit < 30) or (run_memory and cv > 0.5):
        print("\n[!] WARNING: HIGH VARIANCE / SMALL BATCH DETECTED")
        if run_compute and compute_limit < 30:
            print(f"  - Batch size ({compute_limit}) is small (N < 30).")
        if run_memory and cv > 0.5:
            print(f"  - Coefficient of Variation (CV = {cv:.2f}) > 0.5 indicates a heavy-tailed distribution.")
        print("  -> The Gaussian assumption of the CLT may underestimate peak VRAM usage.")
        print("  -> Consider increasing --z-score to 3.0+ or using P99 sequence lengths.")
        print("  -> Ensure your engine's token limits prevent OOMs during extreme tail events.")

    # Output Configurations
    recommended_headroom = 0.0
    if bottleneck.startswith("Compute") and args.enable_prefix_caching:
        recommended_headroom = 0.1

    print_header("CONFIGURATION SNIPPETS")
    print("1. Gateway Configuration (EndpointPickerConfig YAML):")
    print(f"""apiVersion: inference.networking.x-k8s.io/v1alpha1
kind: EndpointPickerConfig
featureGates:
- flowControl
plugins:
- name: my-concurrency-detector
  type: concurrency-detector
  parameters:
    maxConcurrency: {gateway_concurrency}
    headroom: {recommended_headroom} # Adjust 10-20% only if compute-bound & cache enabled
saturationDetector:
  pluginRef: my-concurrency-detector
flowControl:
  maxRequests: 200 # Recommended minimum burst queue capacity
  maxBytes: "10Gi"
""")

    print("2. Model Server Configuration (Engine Target):")
    print(f"   Target Active Concurrency per replica: {safe_active_batch}")
    print(f"   Example for vLLM: vllm serve ... --max-num-seqs {safe_active_batch}")
    print(f"   Example for TGI:  text-generation-launcher ... --max-concurrent-requests {safe_active_batch}\n")

    # CLI Reproducer
    if interactive:
        print_header("AUTOMATION REPRODUCER")
        print("To reproduce this exact calculation via CLI in the future:")
        cmd =["python3 scripts/tuning_wizard.py"]

        def add(flag: str, val: any):
            if val is not None:
                cmd.append(f"{flag} {val}")

        add("--throughput", args.throughput)
        add("--latency-sec", args.latency_sec)
        add("--gpu-blocks", args.gpu_blocks)
        if args.block_size != 16: add("--block-size", args.block_size)
        add("--isl-mean", args.isl_mean)
        add("--isl-std", args.isl_std)
        add("--osl-mean", args.osl_mean)
        add("--osl-std", args.osl_std)
        if args.shared_prefix: add("--shared-prefix", args.shared_prefix)
        if args.enable_prefix_caching: cmd.append("--enable-prefix-caching")
        if args.max_num_batched_tokens != 2048: add("--max-num-batched-tokens", args.max_num_batched_tokens)
        if args.z_score != 2.0: add("--z-score", args.z_score)
        if args.paged_attention_efficiency != 0.90: add("--paged-attention-efficiency", args.paged_attention_efficiency)
        if args.correlation_coefficient != 0.0: add("--correlation-coefficient", args.correlation_coefficient)

        print(" \\\n  ".join(cmd) + "\n")

if __name__ == "__main__":
    main()
