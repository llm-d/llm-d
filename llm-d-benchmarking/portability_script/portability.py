#!/usr/bin/env python3
"""
Compute τ_sat = R_peak × T_max for the §7.4 portability table.

Uses the prefill compute model from the user's earlier draft:

    total_flops      = 2 × params × B
    effective_tflops = peak_tflops × tp × tp_efficiency
    T(B)             = total_flops / (effective_tflops × 1e12)
    R_peak           = B / T(B)
    τ_sat            = R_peak × T_max

For Qwen3-32B / H100 / TP=2: T(B) is taken from direct measurement
(0.40s), not from the formula — the script's tp_efficiency dict is
conservative (implied MFU 0.55 at TP=2; actual measured MFU ~0.66).
The other rows use the formula since no measurements are available.
"""

# vendor peak bf16 TFLOPS
hardware_tflops = {
    'H100': 989,    # H100 SXM (80GB)
    'H200': 989,    # H200 SXM — same compute as H100, more HBM (141GB)
    'B200': 2200,   # Blackwell, 192GB
    'A100': 312,    # A100 SXM (80GB)
    'MI300X': 1307,
    'TPU v5e': 197,
    'TPU v5p': 459,
    'TPU v6e': 918, # Trillium
}

# Combined MFU × collective-overhead per TP degree.
# Slightly conservative — real H100/TP=2 hits ~0.66 vs the 0.55 here.
tp_efficiency = {
    1: 0.60,   # base MFU, no collective overhead
    2: 0.55,   # minor TP overhead
    4: 0.48,   # noticeable
    8: 0.38,   # heavy
}


def estimate_T_B(model_params_b: float, hardware: str, tp: int, B: int = 8192) -> float:
    """Predicted single-chunk prefill wall time, in seconds."""
    peak = hardware_tflops[hardware]
    total_flops = 2 * (model_params_b * 1e9) * B
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

rows = [
    # (label, model_B, hardware, tp, measured_T_B_or_None)
    ('Qwen3-32B / H100 (measured)',          32, 'H100',    2, 0.40),
    ('Qwen3-32B / H200 (estimated)',         32, 'H200',    2, None),
    ('Qwen3-32B / B200 (estimated)',         32, 'B200',    2, None),
    ('Qwen3-32B / A100 80GB (estimated)',    32, 'A100',    2, None),
    ('Qwen3-32B / TPU v5e (estimated)',      32, 'TPU v5e', 8, None),
    ('Qwen3-32B / TPU v5p (estimated)',      32, 'TPU v5p', 2, None),
    ('Qwen3-32B / TPU v6e Trillium (est.)',  32, 'TPU v6e', 4, None),
    ('Llama3-8B / H100 (estimated)',          8, 'H100',    1, None),
]

print(f"T_max = {T_MAX}s, B = {B}, dict = {tp_efficiency}\n")
print(f"{'Setup':<35} {'TP':>3} {'T(B)':>8} {'R_peak':>10} {'τ_sat':>12}")
print('-' * 75)

for label, params_b, hw, tp, T_B_measured in rows:
    if T_B_measured is not None:
        T_B = T_B_measured
        note = 'measured'
    else:
        T_B = estimate_T_B(params_b, hw, tp, B)
        note = 'estimated'
    R_peak, tau = tau_sat(T_B, T_MAX, B)
    print(f"{label:<35} {tp:>3} {T_B:>7.2f}s {R_peak/1000:>8.1f}k {tau:>11,}")
    