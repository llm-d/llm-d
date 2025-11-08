# Notes on SLO aware routing

## Deployment:

```bash
helmfile apply -n ${NAMESPACE} && k apply -f httproute.yaml -n ${NAMESPACE} && k apply -f destinationrule.yaml -n ${NAMESPACE}
```
genai-perf profile \
  -m meta-llama/Llama-3.1-8B-Instruct \
  --endpoint-type chat \
  --url http://infra-slo-aware-inference-gateway-istio.greg-slo-aware.svc.cluster.local \
  --tokenizer meta-llama/Llama-3.1-8B-Instruct \
  --num-prompts 300 \
  --concurrency 30 \
  --goodput time_to_first_token:200 inter_token_latency:50 \
  --streaming \
  --header x-slo-ttft-ms:200 \
  --header x-slo-tpot-ms:50 \
  --header x-prediction-based-scheduling:true \
  --artifact-dir /benchmark-results

# Bug

[2025-11-11 16:24:51] INFO     Invalid Service Level Objectives found: ttft, tpot. Valid Service Level Objectives are: time_to_first_token, time_to_second_token, inter_token_latency, request_latency,    llm_goodput_calculator.py:98
                               output_token_throughput_per_user.

-------------------------------

# Curling the predictions endpoint (from epp pod)

curl -X POST "http://10.0.1.86:8000/predict" \
  -H "Content-Type: application/json" \
  -d '{
    "prefix_cache_score": 0.1,
    "kv_cache_percentage": 0.3,
    "input_token_length": 100,
    "num_request_waiting": 2,
    "num_request_running": 1,
    "num_tokens_generated": 50
  }'



# Initial run:

┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━┳━━━━━━━━━━┓
┃                            Statistic ┃      avg ┃      min ┃      max ┃      p99 ┃      p90 ┃      p75 ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━╇━━━━━━━━━━┩
│             Time To First Token (ms) │   490.41 │    47.63 │   982.25 │   980.55 │   972.28 │   963.50 │
│            Time To Second Token (ms) │    11.80 │     0.00 │   126.15 │   125.65 │    27.37 │     3.24 │
│                 Request Latency (ms) │ 3,024.05 │ 1,448.77 │ 6,861.88 │ 6,308.39 │ 4,109.99 │ 3,539.09 │
│             Inter Token Latency (ms) │     6.63 │     6.05 │     7.13 │     7.12 │     6.99 │     6.76 │
│     Output Token Throughput Per User │   151.04 │   140.26 │   165.26 │   165.19 │   157.40 │   154.79 │
│                    (tokens/sec/user) │          │          │          │          │          │          │
│      Output Sequence Length (tokens) │   383.87 │   203.00 │   970.00 │   925.16 │   548.90 │   442.50 │
│       Input Sequence Length (tokens) │   550.00 │   550.00 │   550.00 │   550.00 │   550.00 │   550.00 │
│ Output Token Throughput (tokens/sec) │ 2,019.90 │      N/A │      N/A │      N/A │      N/A │      N/A │
│         Request Throughput (per sec) │     5.26 │      N/A │      N/A │      N/A │      N/A │      N/A │
│            Request Goodput (per sec) │    -1.00 │      N/A │      N/A │      N/A │      N/A │      N/A │
│                Request Count (count) │    60.00 │      N/A │      N/A │      N/A │      N/A │      N/A │
└──────────────────────────────────────┴──────────┴──────────┴──────────┴──────────┴──────────┴──────────┘

# Treelite

Its not part of request path, its being used for 
  - 

They are looking at shareGPT and highly shared 


Code walkthrough:
  - Everytime a request goes through, when a response comes back, 

  pkg/epp/requestcontrol/plugins/slo
  - understand the plugins pattern in EPP 
  - when a token comes back via streaming, they process that token
    - If its first token (TTFT), it will measure latency for that token (TPOT)
    - Everytime a token comes back they 
  - In parallel were predicting for monitoring - we want to know how accurate the model is 
    - need to predict before the token arrives
    - 

  If I am implementing this in go it will affect latencypredictor_async.go

Also batching for prediction but its not async
  - they can do it async but they just need to figure out how to do it
  - for scheduling they need to predict for every pod together (uses batching)
    - per pod it does not use batching 


latencypredictor-v1: python code for prediction (sidecars)
latencypredictor_async: hooks to talk to the sidecar in go
latencypredictor_helper: functions called by the EPP plugins that use functions from async
plugins: these include the scorer, profile picker, as well as the 4 request control hooks, those 6 plugins make up the actual latency prediction logic

- SLo Scorrer PredictWithMetrics

look at the diff for runner.go between the two branches

The dependency of prefixcache scorer vs slo scorer uses cycle-state which is how state is shared

There used to be 1 tracker and 1 plugin but now theres 1 "multi" plugin that does both

The order in which our stuff runs goes:
profile picker -> scheduling (slo-scorer) -> requestcontrol hooks (slo-tracking)

(the words plugins and hooks are used interchangeably, from a technical standpoint there are 3 plugins, the tracker has 4 requestcontrol hooks)
