# GLM-5.2 C64 fixed-window data

This directory contains byte-for-byte copies of the exact-window request
summaries used for the 2-prefill/2-decode C64 tables in
[../../glm-5.2-h200.md](../../glm-5.2-h200.md). Every measured arm starts with
new engine pod UIDs.

The files preserve the full metric precision exported by the analysis:

* `complete-policy-source-exact-window.json` supplies the approximate-without-
  P2P and precise+P2P arms for the first complete-policy comparison. Its two
  other arm records do not enter a reported table.
* `four-arm-exact-window.json` contains approximate and precise routing, each
  with and without P2P, under one 300-second fixed window.
* `repeat-approx-first-exact-window.json` contains the approximate-without-P2P
  then precise+P2P repeat.
* `repeat-precise-first-exact-window.json` contains the reversed-order
  precise+P2P then approximate-without-P2P repeat.
* `three-pair-aggregate.json` combines the baseline and candidate from the
  complete-policy source with both repeats and records the per-metric means,
  medians, population coefficients of variation, and paired changes.

The unused approximate+P2P record in
`complete-policy-source-exact-window.json` binds the affinity and P2P plugins
to a named approximate producer while `inflight-load-producer` reads the
default producer. That record does not isolate P2P and is excluded from every
reported comparison.

The source campaign mapping is:

| data | campaign |
| --- | --- |
| complete-policy source | `maroon-c64-20260813-174030` |
| two complete-policy repeats | `maroon-c64-repeats-20260813-185900` |
| four-arm approximate/no-P2P, approximate+P2P, and precise/no-P2P | `glm-c64-factorial-20260814-160313` |
| four-arm precise+P2P | `glm-c64-factorial-20260814-175042` |

The copied source checksums are:

| file | SHA-256 |
| --- | --- |
| `complete-policy-source-exact-window.json` | `501edf614aa489f9eea8eb93153b169b977a83207886e17c0f812ffbf1915625` |
| `four-arm-exact-window.json` | `54f2a6db8fd1696c762e4a66a7fb737eb50a17bc7071bc5f241e9c733edd076a` |
| `repeat-approx-first-exact-window.json` | `b70a2772b5e77f646c387eaf989407e5db2c0d87cd37291954cb6be15f0c0f40` |
| `repeat-precise-first-exact-window.json` | `cea487ecbbab6fd5a7156dd858038287f9213b5f70005df5d60e13bda969bf2c` |
| `three-pair-aggregate.json` | `f482f4648cb91448befc9b3418db8f81492d11191924b360ac4f8f6b1e435241` |

Each source workload uses the same AIPerf configuration documented in
[../../../benchmarking/README.md](../../../benchmarking/README.md): 48 entries
from `semianalysis_cc_traces_weka_062126`, concurrency 64, seed 67, a
300-second admission window, and a 120-second drain. A request enters these
files only if it reaches a terminal state by
`min(credit_issued_ns) + 300 seconds`; drain completions outside that window
are excluded.

For throughput metric `x`, the paired percentage change is
`100 * (precise_p2p / approximate - 1)`. Latency changes use the same formula,
so a negative value is an improvement. The four router configurations are
committed in the benchmarking directory next to its README.

The per-request AIPerf records, rank time series, and DEBUG engine logs are not
part of this compact bundle. These files support recomputing every throughput
and latency value in the C64 tables, but not the exact-window extraction from
individual requests.
