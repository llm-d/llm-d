# NIXL/UCX stability diagnostic, 2026-08-25

## Verdict

The P-long-to-decode transport instability was stale cross-restart NIXL/UCX
endpoint state, the same class as vLLM issue #49238. A joint restart of all
three engines cleared it. Memory-registration pressure was not implicated: the
64 GiB per-rank CPU tier ran 27 clean transfers after the restart, so the
8 GiB-tier and NixlConnector-only isolation steps were not needed.

## Pre-restart evidence

The failing seed (run `loadgate-direct3-queued-candidate-20260825-1722`) is one
request end to end in the engine logs:

- 14:24:59.172Z P-long rank DP1 returns prefill `200` for the 102,400-token seed.
- 14:24:59.276Z, 104 ms later, P-long rank DP1: `mlx5dv_devx_obj_modify(opcode=0x503)
  failed, syndrome 0x5d668c: Remote I/O error` - the RDMA QP transition when
  decode begins the pull.
- 14:25:29Z P-long: `Releasing expired KV blocks ... retrieved by 0 remote
  worker(s)`.
- 14:30:52Z decode rank DP7: 23x `UCX AM send failed with status -80 (Endpoint
  timeout)` then `NIXL_ERR_REMOTE_DISCONNECT` from `check_xfer_state`.

The engines had not been restarted together: decode was recreated alone after
the 14:12Z Gloo crash, 13 minutes after the prefill pods, so P-long held NIXL
endpoint state for a dead decode incarnation. Per-pair behavior matches:
P-long rank5 to decode rank4 worked at 14:21Z while rank1 to rank7 failed at
14:24Z. P-short logged zero transport errors throughout.

## Joint restart

All three StatefulSets restarted together at ~15:37Z (pods recreated within a
5-second window; UIDs verified changed). New placement: P-long `10.0.2.131` on
`g13bc90`, P-short `10.0.9.35` on `g134dfa`, decode `10.0.8.233` on `gf2a19e`.

## Per-rank transfer sweeps

Three sweeps of eight seeds each; every seed is a unique-prefix 102,400-token
prompt sent to decode sidecar rank r with `x-prefiller-host-port` pinned to
P-long rank r, forcing a fresh ~5.6 GB NIXL transfer per rank pair.

- 24/24 HTTP 200; per-request latency 18.8-20.7 s (30.9 s first-request warmup).
- After sweep 3: every P-long rank at `prompt_tokens_total` 307,200 (3 x
  102,400), every decode rank at `nixl_xfer_time_seconds_count` 3.
- Zero `mlx5dv`/`UCX`/`NIXL_ERR`/lease-expiry lines on all three pods.

Raw results: `sweep1.jsonl`..`sweep3.jsonl` (session scratch); counters
verified live.

## Queued causal migration retest

`run-smoke.sh candidate 8250911` with the full direct-load gate
(3 x 115,200-token direct requests per rank, all 8 capacity queues plus
12,548+ EPP-visible in-flight tokens at release). Artifact:
`/workload/p2p-context-migration-glm/smoke-20260825-161142-candidate-8250911`.

All 27 requests returned 200 (24 direct, seed, trigger, follow-up). EPP log:
`narrowed to sticky` for the trigger, then exactly one `TTFT load gate broken`
at `bestStickyTTFT` 3,570.43 ms against the 3,500 ms budget (12,800 in-flight
tokens / 3,585 tok/s). Counter deltas are exact:

- P-long rank3: seed + trigger + 3 direct requests; `local_compute` 460,864 =
  345,600 + 102,400 (seed) + 12,864 (trigger tail); trigger restored 102,336
  from the local CPU tier.
- P-short rank6: `ext_hits`/`external_kv_transfer` +102,336, `local_compute`
  +1,088, prompt +103,424 - the migrated follow-up pulled the prefix from
  P-long rank3 and computed only the new tail.
- decode rank0: seed and follow-up on the same rank (session affinity);
  2 NIXL transfers; `local_cache_hit` +102,336 (follow-up reused the seed's
  decode KV); `external_kv_transfer` +103,488 = 102,400 (seed) + 1,088
  (follow-up delta).
- decode rank1: trigger decode, 1 NIXL transfer of 115,200 tokens.

Follow-up TTFT 15.2 s under the full queue (the pull source rank was
simultaneously serving three 115K prefills); the previously rejected version of
this scenario stalled indefinitely. Zero transport errors, zero restarts, all
pods Ready.

## Consequence

The C64 gate condition is met: repeated per-rank transfers and the queued
causal migration test pass with zero UCX/NIXL errors. Operational rule going
forward: never restart one engine of the P/D set alone; a partial restart
leaves surviving peers with stale NIXL endpoints and reproduces this failure.
The `run-agentx.sh` joint-restart-per-arm procedure already enforces this for
benchmark runs.
