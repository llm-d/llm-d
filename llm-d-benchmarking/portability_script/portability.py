#!/usr/bin/env python3
"""
Compute τ_sat = R_peak × T_max for the §7.4 portability table.

UPDATED VERSION:
- Adjusted Qwen3-32B parameter count to the exact 32.8B.
- Adjusted B200 HGX TFLOPS to 2250.
- Integrated quadratic attention FLOPs (4 * L * B^2 * d_model) into the total_flops 
  calculation, which the baseline heuristic (2 * P * B) omits.
"""

# vendor peak bf16 TFLOPS
hardware_tflops = {
    'H100': 989,    # H100 SXM (80GB)
    'H200': 989,    # H200 SXM — same compute as H100, more HBM (141GB)
    'B200': 2250,   # Blackwell HGX, 192GB (Corrected from 2200)
    'A100': 312,    # A100 SXM (80GB)
    'MI300X': 1307,
    'TPU v5e': 197,
    'TPU v5p': 459,
    'TPU v6e': 918, # Trillium
}

# Combined MFU × collective-overhead per TP degree.
tp_efficiency = {
    1: 0.60,   # base MFU, no collective overhead
    2: 0.55,   # minor TP overhead
    4: 0.48,   # noticeable
    8: 0.38,   # heavy
}

def estimate_T_B(model_params_b: float, n_layers: int, d_model: int, hardware: str, tp: int, B: int = 8192) -> float:
    """Predicted single-chunk prefill wall time, in seconds."""
    peak = hardware_tflops[hardware]
    
    # 1. Linear Projection FLOPs (Dense Matrix Multiplications)
    linear_flops = 2 * (model_params_b * 1e9) * B
    
    # 2. Quadratic Attention FLOPs
    attention_flops = 4 * n_layers * d_model * (B ** 2)
    
    total_flops = linear_flops + attention_flops
    
    eff = tp_efficiency.get(tp, 0.60 * (0.85 ** (tp // 2)))
    effective_cluster_tflops = peak * tp * eff
    
    return total_flops / (effective_cluster_tflops * 1e12)

def tau_sat(T_B: float, T_max_sec: float, B: int = 8192) -> tuple[float, int]:
    """Return (R_peak in tok/s, τ_sat in tokens)."""
    R_peak = B / T_B
    return R_peak, int(R_peak * T_max_sec)

# === Configurations in the §7.4 table ===

T_MAX = 14.0   # seconds, operator's TTFT degradation tolerance
B = 8192       # max-num-batched-tokens

rows =

print(f"T_max = {T_MAX}s, B = {B}, dict = {tp_efficiency}\n")
print(f"{'Setup':<35} {'TP':>3} {'T(B)':>8} {'R_peak':>10} {'τ_sat':>12}")
print('-' * 75)

for label, params_b, n_layers, d_model, hw, tp, T_B_measured in rows:
    if T_B_measured is not None:
        T_B = T_B_measured
        note = 'measured'
    else:
        T_B = estimate_T_B(params_b, n_layers, d_model, hw, tp, B)
        note = 'estimated'
    R_peak, tau = tau_sat(T_B, T_MAX, B)
    print(f"{label:<35} {tp:>3} {T_B:>7.2f}s {R_peak/1000:>8.1f}k {tau:>11,}")