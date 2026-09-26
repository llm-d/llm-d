#!/usr/bin/env python3
"""Calibrate the p2p-source-producer costModel constants (all except fleetWeight).

Inputs
  --crossover FILE     output of guides/recipes/router/calibration/calibrate-min-cached-token-delta.sh
                       (idle pull-vs-recompute table) -> prefillMicrosecondsPerToken, transferMicrosecondsPerToken
  --per-request FILE   inference-perf per_request_lifecycle_metrics.json from a LOADED run (repeatable)
  --epp-log FILE       EPP log captured during that run; only lines whose body is "p2p decision" are used
                       (requires a router build that logs one "p2p decision" line per request)
The loaded run must use the fixed-delta rule (no costModel block) so that every candidate pull is taken
and both pulled and local requests appear across queue states.

Method
  Idle: least-squares lines through the table: recompute_ms = a + P*tokens, pull_ms = b + T*tokens.
  Loaded: join each request's TTFT (first output token time - send time) to its pull decision by
  request id (vLLM completion id "cmpl-<id>" == router request id), then fit pulled and local requests
  jointly with one shared prefill-under-load slope and the transfer fixed at the idle rate:
    ttft = base + p*uncached + q*busy(computing) + pulled*(F + S*busy(source) + Q*busy(computing) + T_idle*delta)
  transferFixedMs = F, sourceWaitMs = S, requeueMs = Q (each floored at 0).
Stdlib only.
"""
import argparse, json, random, re, sys

def lstsq(X, y):
    """Least squares via normal equations with Gaussian elimination (small, well-posed systems)."""
    n = len(X[0])
    A = [[sum(r[i] * r[j] for r in X) for j in range(n)] for i in range(n)]
    b = [sum(r[i] * t for r, t in zip(X, y)) for i in range(n)]
    for i in range(n):  # tiny ridge keeps an all-zero column (e.g. never busy) solvable
        A[i][i] += 1e-9
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(A[r][c]))
        A[c], A[p] = A[p], A[c]; b[c], b[p] = b[p], b[c]
        for r in range(c + 1, n):
            f = A[r][c] / A[c][c]
            for k in range(c, n): A[r][k] -= f * A[c][k]
            b[r] -= f * b[c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (b[r] - sum(A[r][k] * x[k] for k in range(r + 1, n))) / A[r][r]
    rmse = (sum((sum(a * c for a, c in zip(row, x)) - t) ** 2 for row, t in zip(X, y)) / len(y)) ** 0.5
    return x, rmse

def idle_fit(path):
    rows = []
    for line in open(path):
        m = re.match(r"^\s*(\d+)\s+([\d.]+)\s+([\d.]+)\s+[-+]?[\d.]+%", line)
        if m: rows.append((int(m.group(1)), float(m.group(2)), float(m.group(3))))
    if len(rows) < 2: sys.exit(f"no crossover table found in {path}")
    X = [[1, t / 1000] for t, _, _ in rows]
    (ra, rp), _ = lstsq(X, [r for _, r, _ in rows])
    (pa, pt), _ = lstsq(X, [p for _, _, p in rows])
    return dict(points=len(rows), prefill_us=rp, recompute_intercept_ms=ra, transfer_us=pt, pull_intercept_ms=pa)

def load_decisions(path):
    dec = {}
    for line in open(path, errors="replace"):
        i = line.find("{")
        if i < 0 or "p2p decision" not in line: continue
        try: d = json.loads(line[i:])
        except ValueError: continue
        if d.get("body", d.get("msg")) == "p2p decision" and d.get("requestID"): dec[d["requestID"]] = d
    return dec

def load_requests(paths):
    out = []
    for p in paths:
        for r in json.load(open(p)):
            if r.get("error"): continue
            rm = (r.get("info") or {}).get("response_metrics") or {}
            times = rm.get("output_token_times") or []
            chunks = rm.get("response_chunks") or []
            if not times or not chunks: continue
            try: rid = json.loads(chunks[0]).get("id", "")
            except ValueError: continue
            rid = rid[5:] if rid.startswith("cmpl-") else rid
            out.append(dict(rid=rid, ttft_ms=(times[0] - r["start_time"]) * 1000, input_tokens=(r.get("info") or {}).get("input_tokens")))
    return out

def loaded_fit(reqs, dec, busy_threshold, min_samples, idle_transfer_us, bootstrap):
    """Joint fit over pulled and local requests with one shared prefill-under-load slope:
         ttft = d0 + p*uncached + dQ*busy(computing)
                + pulled * (F + cS*busy(source) + Qx*busy(computing)) + pulled * T*transferred
       with T fixed to the idle transfer rate (transferred tokens are subtracted first), so F, cS and Qx
       are estimated as differences against local requests in the same load state. A free-T fit is also
       run and reported as a diagnostic."""
    rows, unmatched, n_pull, n_local, sb_n, db_n, dbl_n = [], 0, 0, 0, 0, 0, 0
    for q in reqs:
        d = dec.get(q["rid"])
        if d is None or q["input_tokens"] is None: unmatched += 1; continue
        sb = 1 if d.get("sourceWaiting", 0) >= busy_threshold else 0
        db = 1 if d.get("computingWaiting", 0) >= busy_threshold else 0
        if d["outcome"] == "pulled":
            unc = q["input_tokens"] - d["bestCachedTokens"]; n_pull += 1; sb_n += sb; db_n += db
            rows.append(dict(pulled=1, unc=unc, delta=d["deltaTokens"], sb=sb, db=db, ttft=q["ttft_ms"]))
        elif d["outcome"] in ("self", "below-floor", "no-best-match"):
            unc = q["input_tokens"] - d.get("computingCachedTokens", 0); n_local += 1; dbl_n += db
            rows.append(dict(pulled=0, unc=unc, delta=0, sb=0, db=db, ttft=q["ttft_ms"]))
    res = dict(requests=len(reqs), joined=len(reqs) - unmatched, pulled=n_pull, local=n_local,
               pulled_src_busy=sb_n, pulled_dst_busy=db_n, local_dst_busy=dbl_n)
    if n_pull < min_samples or n_local < min_samples:
        res["error"] = f"need >= {min_samples} pulled and local requests (got {n_pull} / {n_local}); run longer or at higher load"
        return res
    X = [[1, r["unc"] / 1000, r["db"], r["pulled"], r["pulled"] * r["sb"], r["pulled"] * r["db"]] for r in rows]
    y = [r["ttft"] - r["pulled"] * r["delta"] * idle_transfer_us / 1000 for r in rows]
    c, rmse = lstsq(X, y)
    # Bootstrap over requests: the spread of F, S and Q says whether the run supports the constants.
    rng = random.Random(0); boots = []
    idx = list(range(len(rows)))
    for _ in range(bootstrap):
        pick = [rng.choice(idx) for _ in idx]
        if sum(rows[i]["pulled"] for i in pick) < 5: continue
        cb, _ = lstsq([X[i] for i in pick], [y[i] for i in pick])
        boots.append((cb[3], cb[4], cb[5]))
    def interval(k):
        v = sorted(b[k] for b in boots)
        return (v[int(0.1 * (len(v) - 1))], v[int(0.9 * (len(v) - 1))]) if v else (float("nan"), float("nan"))
    res["interval80"] = dict(transferFixedMs=interval(0), sourceWaitMs=interval(1), requeueMs=interval(2))
    Xf = [x + [r["pulled"] * r["delta"] / 1000] for x, r in zip(X, rows)]
    cf, _ = lstsq(Xf, [r["ttft"] for r in rows])
    res.update(fit=dict(base_ms=c[0], prefill_under_load_ms_per_ktok=c[1], busy_computing_ms=c[2],
                        pull_fixed_ms=c[3], pull_busy_source_ms=c[4], pull_extra_busy_computing_ms=c[5], rmse_ms=rmse),
               transferFixedMs=max(0.0, c[3]), sourceWaitMs=max(0.0, c[4]), requeueMs=max(0.0, c[5]),
               loaded_transfer_us=cf[6])
    return res

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--crossover", required=True)
    ap.add_argument("--per-request", action="append", required=True)
    ap.add_argument("--epp-log", required=True)
    ap.add_argument("--busy-queue-threshold", type=int, default=1)
    ap.add_argument("--min-samples", type=int, default=30)
    ap.add_argument("--transfer", choices=["idle", "loaded"], default="idle",
                    help="transfer rate to emit: the idle pair's (default) or the free loaded fit")
    ap.add_argument("--bootstrap", type=int, default=200, help="bootstrap resamples for the 80%% intervals (0 disables)")
    ap.add_argument("--json", action="store_true", help="print the full fit as JSON")
    a = ap.parse_args()
    idle = idle_fit(a.crossover)
    dec = load_decisions(a.epp_log)
    reqs = load_requests(a.per_request)
    loaded = loaded_fit(reqs, dec, a.busy_queue_threshold, a.min_samples, idle["transfer_us"], a.bootstrap)
    if a.json:
        print(json.dumps(dict(idle=idle, loaded=loaded, decisions=len(dec)), indent=2))
    print(f"# idle pair ({idle['points']} lengths): recompute {idle['prefill_us']:.1f} us/token, pull {idle['transfer_us']:.1f} us/token + {idle['pull_intercept_ms']:.0f} ms", file=sys.stderr)
    print(f"# loaded run: {loaded['requests']} requests, {loaded['joined']} joined to {len(dec)} decisions; "
          f"{loaded['pulled']} pulled ({loaded['pulled_src_busy']} busy source, {loaded['pulled_dst_busy']} busy computing), "
          f"{loaded['local']} local ({loaded['local_dst_busy']} busy computing)", file=sys.stderr)
    if "error" in loaded: sys.exit("error: " + loaded["error"])
    f = loaded["fit"]
    print(f"# joint fit (transfer fixed at the idle {idle['transfer_us']:.1f} us/token): TTFT = {f['base_ms']:.0f} + {f['prefill_under_load_ms_per_ktok']:.1f}/Ktok uncached"
          f" + {f['busy_computing_ms']:.0f}[busy computing]; a pull adds {f['pull_fixed_ms']:.0f} fixed + {f['pull_busy_source_ms']:.0f}[busy source]"
          f" + {f['pull_extra_busy_computing_ms']:.0f}[busy computing] ms (rmse {f['rmse_ms']:.0f})", file=sys.stderr)
    print(f"# diagnostic: transfer fitted freely under load = {loaded['loaded_transfer_us']:.1f} us/token"
          " (confounded with queueing when transferred sizes barely vary; not used by default)", file=sys.stderr)
    iv = loaded["interval80"]
    print("# 80% bootstrap intervals: " + ", ".join(f"{k} {lo:.0f} to {hi:.0f}" for k, (lo, hi) in iv.items()), file=sys.stderr)
    wide = [k for k, (lo, hi) in iv.items() if hi - lo > 250]
    if wide:
        print(f"# WARNING: {', '.join(wide)} not well determined (interval wider than 250 ms): pool more runs, add pulls"
              " at more delta sizes, or run at a steadier load before using these values", file=sys.stderr)
    transfer = {"idle": idle["transfer_us"], "loaded": loaded["loaded_transfer_us"]}[a.transfer]
    prefill = idle["prefill_us"]
    if transfer >= prefill:
        sys.exit(f"error: transfer {transfer:.1f} us/token is not below prefill {prefill:.1f}; the pull cannot win on this transport")
    for name, key in (("transferFixedMs", "transferFixedMs"), ("requeueMs", "requeueMs")):
        if loaded[key] == 0: print(f"# note: {name} fit to <= 0 and was floored at 0", file=sys.stderr)
    print(f"""costModel:
  prefillMicrosecondsPerToken: {prefill:.1f}
  transferMicrosecondsPerToken: {transfer:.1f}
  transferFixedMs: {loaded['transferFixedMs']:.0f}
  requeueMs: {loaded['requeueMs']:.0f}
  sourceWaitMs: {loaded['sourceWaitMs']:.0f}
  busyQueueThreshold: {a.busy_queue_threshold}
  fleetWeight: 1  # not calibrated by this script; choose explicitly""")

if __name__ == "__main__":
    main()
