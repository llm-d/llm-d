curl -v http://localhost:8000/v1/completions \
  -H 'Content-Type: application/json' \
  -H 'x-prediction-based-scheduling: true' \
  -H 'x-slo-ttft-ms: 500' \
  -H 'x-slo-tpot-ms: 100' \
  -d '{
    "model": "Qwen/Qwen3-Coder-480B-A35B-Instruct-FP8",
    "prompt": "Explain Kubernetes pod scheduling in detail.",
    "max_tokens": 200,
    "temperature": 0,
    "stream": true,
    "stream_options": {"include_usage": true}
  }' | tee ./output-of-curl.log
