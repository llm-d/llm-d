# Kubernetes Benchmark Launcher

This guide explains how to run benchmarks on LLM deployments.
It uses [fmperf](https://github.com/fmperf-project/fmperf), specifically [fmperf's run_benchmark](https://github.com/fmperf-project/fmperf/blob/main/fmperf/utils/Benchmarking.py#L48)
The environment vars are configured via a [configmap](./workload-configmap.yaml).
This example runs LLM benchmarks in a Kubernetes cluster using a two-level job structure:

1. A launcher job that sets up the environment and configuration
2. A benchmark job that performs the actual load testing

The launcher job (`fmperf-benchmark`) creates and monitors a benchmark job (`lmbenchmark-evaluate-{timestamp}`) that runs the actual load tests against your model.

## Prerequisites

1. A running Kubernetes cluster
2. An existing model deployment with an accessible inference endpoint
3. Hugging Face Token secret

### Using Hugging Face Token secret

Some models and benchmarks require authentication with Hugging Face. Even if you already have the model served,
evaluation code will reach out to HuggingFace to download tokenizer files if it cannot find them locally.
The benchmark code supports an HF_TOKEN_SECRET

Create a Kubernetes secret with your Hugging Face token:
   ```bash
   kubectl create secret generic huggingface-secret \
     --from-literal=HF_TOKEN=your_hf_token_here \
     -n fmperf
   ```

Set the `HF_TOKEN_SECRET` environment variable in the `job.yaml` file:
   ```yaml
    - name: HF_TOKEN_SECRET
      value: "huggingface-secret"  # Name of your secret
   ```

This method is secure and doesn't expose your token in the pod's environment variables.

## Compare Multiple LLM Deployments

FMPerf supports comparing two different LLM deployments side-by-side
(for example, comparing a vanilla deployment with an optimized version like llm-d).

For complete instructions on running comparative benchmarks, please see:
- [Compare-README.md](Compare-README.md) - Step-by-step guide for running comparison benchmarks
- [analyze-compare-results.py](./compare-baseline-llm/analyze-compare-results.py) - Script for comparing benchmark results
- [readme-analyze-compare-template.md](compare-baseline-llmd/readme-analyze-compare-template.md) - Template for comparison results

The comparison workflow allows you to:
1. Run benchmarks against two different LLM deployments
2. Collect results from both
3. Generate side-by-side visualizations and statistics
4. Quantify performance improvements

## Important Notes

- The RBAC permissions in `rbac.yaml` are configured for:
  - Service Account: `fmperf-runner`
  - Namespace: `fmperf`
- Keep these values unchanged unless you update the RBAC configuration accordingly
- PVC is expected to be `fmperf-results-pvc`, as is named in the pvc definition file.
- The benchmark results will be stored in the PVC mounted at `/requests`

## Run the Benchmarks for a Single Model Service

1. Create the PVC:
   ```bash
   kubectl apply -f pvc.yaml
   ```

2. Apply ServiceAccount and RBAC permissions:
   ```bash
   kubectl apply -f sa.yaml
   kubectl apply -f rbac.yaml
   ```

3. Create the ConfigMap:
   ```bash
   kubectl apply -f workload-configmap.yaml
   ```

4. Create the launcher job:
   ```bash
   kubectl apply -f job.yaml
   ```

5. Monitor the jobs:
   ```bash
   # Watch the launcher job
   kubectl get jobs -n fmperf fmperf-benchmark -w
   
   # Watch the benchmark job
   kubectl get jobs -n fmperf lmbenchmark-evaluate-* -w
   ```

## Retrieve Results

The benchmark results are organized in directories within the PVC:

```bash
# Create local directory for the results
mkdir -p ./fmperf-results

# List the available benchmark results in the pod
kubectl exec -n fmperf $(kubectl get pods -n fmperf -l job-name=fmperf-benchmark -o name | sed 's|pod/||') -- ls -la /requests

# Copy the results directly to your local machine
kubectl cp fmperf/$(kubectl get pods -n fmperf -l job-name=fmperf-benchmark -o name | sed 's|pod/||'):/requests/ ./fmperf-results/ -c retriever

# Set the TIMESTAMP environment variable for easy reference
export TIMESTAMP=$(ls -la ./fmperf-results | grep "run_" | head -1 | awk '{print $9}')
echo "Using timestamp directory: $TIMESTAMP"
```

The retriever container will continue running for 6 hours, giving you plenty of time to copy the results. After that, it will automatically exit.

## Analyzing Results

The benchmark results can be analyzed using the provided Python script. The script generates visualizations and statistics for latency and throughput metrics.

1. Install required packages:

   ```bash
   pip install pandas matplotlib seaborn grip
   ```

2. Run the analysis script:

   ```bash
   # Analyze the results using the timestamp variable
   python analyze_results.py --results-dir ./fmperf-results/$TIMESTAMP
   ```

The script will create a `plots` directory inside your results directory and generate:
- `plots/latency_analysis.png`: Shows latency metrics across different QPS levels
- `plots/throughput_analysis.png`: Shows throughput and token count metrics 
- `plots/README.md`: Contains detailed descriptions of the plots

The README.md is generated using the `readme-analyze-template.md` template, which provides a standardized format for understanding the benchmark results.

3. Viewing the analysis results:

   ```bash
   # Navigate to the plots directory
   cd ./fmperf-results/$TIMESTAMP/plots
   
   # View the README with GitHub styling using grip
   grip README.md --browser
   ```

The script also prints detailed statistics in the terminal, including:
- Overall statistics (total requests, unique users, QPS levels)
- Per-QPS statistics (latency and token metrics)
- Token statistics (prompt and generation tokens)
