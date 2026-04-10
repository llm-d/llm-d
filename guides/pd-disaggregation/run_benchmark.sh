#!/bin/bash

# Configuration
NAMESPACE="llm-d-pd"
JOB_NAME="qwen3-pd-benchmark"
MODEL_NAME="Qwen/Qwen3-32B"

echo "🔍 Discovering Gateway IP..."
GATEWAY_IP=10.128.0.15


TARGET_URL="http://$GATEWAY_IP"
echo "✅ Found Gateway at: " $TARGET_URL

echo "🗑️  Cleaning up old benchmark jobs..."
kubectl delete job $JOB_NAME -n $NAMESPACE --ignore-not-found=true

echo "🚀 Generating and applying Benchmark Job..."
cat <<EOF | kubectl apply -f -
apiVersion: batch/v1
kind: Job
metadata:
  name: $JOB_NAME
  namespace: $NAMESPACE
spec:
  template:
    spec:
      containers:
      - name: llm-benchmark
        image: vllm/vllm-openai:latest
        command: ["/bin/bash", "-c"]
        args:
        - |
          # 1. Download dataset
          if [ ! -f /data/sharegpt.json ]; then
            echo "Downloading ShareGPT dataset..."
            curl -L "https://huggingface.co/datasets/anon8231489123/ShareGPT_Vicuna_unfiltered/resolve/main/ShareGPT_V3_unfiltered_cleaned_split.json" -o /data/sharegpt.json
          fi

          # 2. Wait for Gateway readiness
          echo "Checking connectivity to $TARGET_URL..."
          until curl -s "$TARGET_URL/v1/models" | grep -q "$MODEL_NAME"; do
            echo "Waiting for Gateway backends to sync..."
            sleep 10
          done

          # 3. Run Benchmark
          vllm bench serve \
            --base-url "$TARGET_URL" \
            --model "$MODEL_NAME" \
            --dataset-name "sharegpt" \
            --dataset-path "/data/sharegpt.json" \
            --request-rate 80.0 \
            --num-prompts 2000 \
            --tokenizer "$MODEL_NAME"
        volumeMounts:
        - name: dataset-volume
          mountPath: /data
      restartPolicy: Never
      volumes:
      - name: dataset-volume
        emptyDir: {}
EOF

echo "⏳ Job submitted. Follow logs with:"
echo "kubectl logs -f job/$JOB_NAME -n $NAMESPACE"
