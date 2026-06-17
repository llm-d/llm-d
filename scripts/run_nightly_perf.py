#!/usr/bin/env python3

import argparse
import json
import os
import re
import subprocess
import socket
import sys
import time
from threading import Thread
import yaml

# Global flag to stop the metric monitoring thread
stop_monitoring = False

def run_cmd(cmd, check=True, capture_output=True, text=True):
    print(f"Running: {cmd}")
    if isinstance(cmd, str):
        res = subprocess.run(cmd, shell=True, check=check, capture_output=capture_output, text=text)
    else:
        res = subprocess.run(cmd, check=check, capture_output=capture_output, text=text)
    return res

def create_namespace(ns):
    print(f"Creating namespace: {ns}")
    run_cmd(f"kubectl create namespace {ns}")

def setup_hf_secret(ns):
    hf_token = os.environ.get("HF_TOKEN")
    if hf_token:
        print("Creating hf-secret from HF_TOKEN environment variable...")
        run_cmd(f"kubectl create secret generic hf-secret --from-literal=token={hf_token} -n {ns}")
    else:
        print("HF_TOKEN not set in environment. Falling back to copying hf-secret from llm-d-sim namespace...")
        cmd = f"kubectl get secret hf-secret -n llm-d-sim -o yaml | sed 's/namespace: llm-d-sim/namespace: {ns}/g' | kubectl apply -f -"
        run_cmd(cmd)

def setup_perf_sa(ns, enable_workload_identity=False, gcp_project=None):
    print("Creating service account inference-perf-sa...")
    run_cmd(f"kubectl create serviceaccount inference-perf-sa -n {ns}")
    if enable_workload_identity:
        if not gcp_project:
            raise RuntimeError("GCP Project ID is required to configure Workload Identity.")
        print(f"Annotating service account with Workload Identity for project {gcp_project}...")
        run_cmd(
            f"kubectl annotate serviceaccount inference-perf-sa -n {ns} "
            f"iam.gke.io/gcp-service-account=inference-perf-gsa@{gcp_project}.iam.gserviceaccount.com"
        )

def deploy_simulators(ns, sim_deploy_path, sim_svc_path, replicas=10):
    print(f"Deploying simulators to namespace: {ns} with {replicas} replicas")
    
    # Process deployment yaml
    with open(sim_deploy_path, "r") as f:
        deploy_docs = list(yaml.safe_load_all(f))
        
    for doc in deploy_docs:
        if not doc:
            continue
        doc["metadata"]["namespace"] = ns
        if doc.get("kind") == "Deployment":
            if "spec" in doc:
                doc["spec"]["replicas"] = replicas

    temp_deploy = "/tmp/temp-sim-deploy.yaml"
    with open(temp_deploy, "w") as f:
        yaml.safe_dump_all(deploy_docs, f)

    run_cmd(f"kubectl apply -f {temp_deploy} -n {ns}")

    # Process service yaml
    with open(sim_svc_path, "r") as f:
        svc_docs = list(yaml.safe_load_all(f))
        
    for doc in svc_docs:
        if not doc:
            continue
        doc["metadata"]["namespace"] = ns

    temp_svc = "/tmp/temp-sim-svc.yaml"
    with open(temp_svc, "w") as f:
        yaml.safe_dump_all(svc_docs, f)

    run_cmd(f"kubectl apply -f {temp_svc} -n {ns}")
    
    # Wait for rollout
    print("Waiting for llm-d-sim deployment to roll out...")
    run_cmd(f"kubectl rollout status deployment/llm-d-sim -n {ns} --timeout=10m")

def double_cpu(cpu_str):
    if cpu_str.endswith('m'):
        val = int(cpu_str[:-1])
        return f"{val * 2}m"
    val = float(cpu_str)
    if val.is_integer():
        return str(int(val * 2))
    return str(val * 2)

def double_memory(mem_str):
    import re
    match = re.match(r'^(\d+)([a-zA-Z]+)$', mem_str.strip())
    if match:
        val = int(match.group(1))
        unit = match.group(2)
        return f"{val * 2}{unit}"
    val = int(mem_str)
    return str(val * 2)

def deploy_epp(ns, chart_path, values_path, epp_cpu="2", epp_memory="4Gi", machine_family=None):
    print(f"Deploying EPP standalone using Helm chart from: {chart_path}")
    
    cpu_limit = double_cpu(epp_cpu)
    mem_limit = double_memory(epp_memory)
    
    cmd = [
        "helm", "install", "optimized-baseline-sim-standalone", chart_path,
        "-f", values_path,
        "--set", "router.proxy.enabled=true",
        "--set", f"router.epp.resources.requests.cpu={epp_cpu}",
        "--set", f"router.epp.resources.limits.cpu={cpu_limit}",
        "--set", f"router.epp.resources.requests.memory={epp_memory}",
        "--set", f"router.epp.resources.limits.memory={mem_limit}",
        "-n", ns
    ]
    
    if machine_family:
        import json
        affinity = {
            "nodeAffinity": {
                "requiredDuringSchedulingIgnoredDuringExecution": {
                    "nodeSelectorTerms": [
                        {
                            "matchExpressions": [
                                {
                                    "key": "cloud.google.com/machine-family",
                                    "operator": "In",
                                    "values": [machine_family]
                                }
                            ]
                        }
                    ]
                }
            }
        }
        cmd.extend(["--set-json", f"router.epp.affinity={json.dumps(affinity)}"])
        
    run_cmd(cmd)
    
    print("Waiting for EPP deployment to become ready...")
    run_cmd(f"kubectl rollout status deployment/optimized-baseline-sim-standalone-epp -n {ns} --timeout=10m")

def get_epp_pod_name(ns):
    res = run_cmd(f"kubectl get pods -n {ns} -o jsonpath='{{.items[*].metadata.name}}'")
    pods = res.stdout.strip().split()
    for pod in pods:
        if pod.startswith("optimized-baseline-sim-standalone-epp"):
            return pod
    raise Exception(f"EPP pod not found in namespace {ns}. Available pods: {pods}")

def get_pod_containers(ns, pod_name):
    res = run_cmd(
        f"kubectl get pod {pod_name} -n {ns} -o jsonpath='{{.spec.containers[*].name}}'"
    )
    return res.stdout.strip().split()

def get_container_images(ns, pod_name):
    res = run_cmd(
        f"kubectl get pod {pod_name} -n {ns} -o jsonpath='{{.spec.containers[*].image}}'"
    )
    return res.stdout.strip().split()

def sample_resources(ns, pod_name):
    # Output of: kubectl top pod <pod> -n <ns> --containers --no-headers
    # Format: <pod>  <container>  <cpu>  <memory>
    res = run_cmd(f"kubectl top pod {pod_name} -n {ns} --containers --no-headers", check=False)
    if res.returncode != 0:
        return None
        
    metrics = {}
    total_cpu = 0
    total_mem = 0
    
    for line in res.stdout.strip().split('\n'):
        parts = line.split()
        if len(parts) < 4:
            continue
        container = parts[1]
        cpu_str = parts[2]
        mem_str = parts[3]
        
        # Parse CPU (e.g. 100m -> 100, or 2 -> 2000)
        if cpu_str.endswith('m'):
            cpu = int(cpu_str[:-1])
        else:
            cpu = int(float(cpu_str) * 1000)
            
        # Parse Memory (e.g. 500Mi -> 500)
        if mem_str.endswith('Mi'):
            mem = int(mem_str[:-2])
        elif mem_str.endswith('Gi'):
            mem = int(float(mem_str[:-2]) * 1024)
        else:
            mem = int(mem_str)
            
        metrics[container] = {'cpu': cpu, 'mem': mem}
        total_cpu += cpu
        total_mem += mem
        
    metrics['TOTAL'] = {'cpu': total_cpu, 'mem': total_mem}
    return metrics

def monitor_resources_loop(ns, pod_name, interval, peak_metrics):
    global stop_monitoring
    print("Starting background resource monitoring...")
    while not stop_monitoring:
        sample = sample_resources(ns, pod_name)
        if sample:
            for container, usage in sample.items():
                if container not in peak_metrics:
                    peak_metrics[container] = {'cpu': 0, 'mem': 0}
                peak_metrics[container]['cpu'] = max(peak_metrics[container]['cpu'], usage['cpu'])
                peak_metrics[container]['mem'] = max(peak_metrics[container]['mem'], usage['mem'])
        time.sleep(interval)
    print("Stopped background resource monitoring.")

def find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('localhost', 0))
        return s.getsockname()[1]

def scrape_scheduler_metrics(ns, pod_name):
    pf_port = find_free_port()
    # We port-forward 9090 from the pod locally in the background, curl, and kill it.
    print(f"Scraping metrics for pod {pod_name} using port-forward on port {pf_port}...")
    pf_process = subprocess.Popen(
        ["kubectl", "port-forward", "-n", ns, f"pod/{pod_name}", f"{pf_port}:9090"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL
    )
    
    # Wait for port-forward to establish
    time.sleep(3)
    
    metrics = {
        'buckets': {},
        'sum': 0.0,
        'count': 0
    }
    
    try:
        import urllib.request
        with urllib.request.urlopen(f"http://localhost:{pf_port}/metrics", timeout=5) as response:
            content = response.read().decode('utf-8')
            
        for line in content.split('\n'):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            
            # Check primary name
            match = re.match(r'llm_d_epp_scheduler_e2e_duration_seconds_bucket\{le="([^"]+)"\} ([\d.e+-]+)', line)
            if not match:
                match = re.match(r'llm_d_router_epp_scheduler_e2e_duration_seconds_bucket\{le="([^"]+)"\} ([\d.e+-]+)', line)
            if not match:
                match = re.match(r'inference_extension_scheduler_e2e_duration_seconds_bucket\{le="([^"]+)"\} ([\d.e+-]+)', line)
                
            if match:
                le = match.group(1)
                val = float(match.group(2))
                metrics['buckets'][le] = val
                continue
                
            match = re.match(r'llm_d_epp_scheduler_e2e_duration_seconds_sum ([\d.e+-]+)', line)
            if not match:
                match = re.match(r'llm_d_router_epp_scheduler_e2e_duration_seconds_sum ([\d.e+-]+)', line)
            if not match:
                match = re.match(r'inference_extension_scheduler_e2e_duration_seconds_sum ([\d.e+-]+)', line)
            if match:
                metrics['sum'] = float(match.group(1))
                continue
                
            match = re.match(r'llm_d_epp_scheduler_e2e_duration_seconds_count ([\d.e+-]+)', line)
            if not match:
                match = re.match(r'llm_d_router_epp_scheduler_e2e_duration_seconds_count ([\d.e+-]+)', line)
            if not match:
                match = re.match(r'inference_extension_scheduler_e2e_duration_seconds_count ([\d.e+-]+)', line)
            if match:
                metrics['count'] = int(float(match.group(1)))
                continue
    except Exception as e:
        print(f"Error scraping metrics over port-forward: {e}")
        metrics = None
    finally:
        pf_process.terminate()
        pf_process.wait()
        
    return metrics

def interpolate_percentile(sorted_buckets, total_count, percentile):
    target_rank = percentile * total_count
    cumulative_count = 0.0
    prev_le = 0.0
    prev_count = 0.0
    
    for le_str, count in sorted_buckets:
        cumulative_count = count
        if le_str == '+Inf':
            le = float('inf')
        else:
            le = float(le_str)
            
        if cumulative_count >= target_rank:
            if le == float('inf'):
                return prev_le
                
            width = le - prev_le
            if cumulative_count == prev_count:
                return le
                
            fraction = (target_rank - prev_count) / (cumulative_count - prev_count)
            value = prev_le + fraction * width
            return value
            
        prev_le = le
        prev_count = cumulative_count
        
    return prev_le

def calculate_percentiles(before, after):
    if not before or not after:
        return 0.0, 0.0
        
    diff_count = after['count'] - before['count']
    if diff_count <= 0:
        print("No new scheduler events recorded during the test.")
        return 0.0, 0.0
        
    diff_buckets = {}
    for le in after['buckets']:
        if le in before['buckets']:
            diff_buckets[le] = after['buckets'][le] - before['buckets'][le]
        else:
            diff_buckets[le] = after['buckets'][le]
            
    def bucket_key(item):
        le = item[0]
        if le == '+Inf':
            return float('inf')
        return float(le)
        
    sorted_buckets = sorted(diff_buckets.items(), key=bucket_key)
    p50 = interpolate_percentile(sorted_buckets, diff_count, 0.50)
    p95 = interpolate_percentile(sorted_buckets, diff_count, 0.95)
    
    return p50 * 1000, p95 * 1000  # Convert to milliseconds

def run_benchmark(ns, job_values_path, chart_path):
    print(f"Deploying benchmark job in namespace: {ns}")
    
    # Process job values file to point to EPP local Service URL
    with open(job_values_path, "r") as f:
        job_docs = yaml.safe_load(f)
        
    # Override server url to local namespace service
    job_docs["config"]["server"]["base_url"] = "http://optimized-baseline-sim-standalone-epp:80"
    job_docs["token"]["hfSecret"]["name"] = "hf-secret"
    job_docs["token"]["hfSecret"]["key"] = "token"
    job_docs["job"]["serviceAccountName"] = "inference-perf-sa"

    temp_job_values = "/tmp/temp-job-values.yaml"
    with open(temp_job_values, "w") as f:
        yaml.safe_dump(job_docs, f)

    run_cmd(f"helm install inference-perf {chart_path} -f {temp_job_values} -n {ns}")
    
    # Wait for the job pod to start and finish
    print("Waiting for inference-perf job to start...")
    job_pod_name = ""
    for _ in range(120):
        res = run_cmd(f"kubectl get pods -n {ns} -l app=inference-perf -o jsonpath='{{.items[0].metadata.name}}'", check=False)
        if res.returncode == 0 and res.stdout.strip():
            job_pod_name = res.stdout.strip()
            break
        time.sleep(5)
        
    if not job_pod_name:
        raise RuntimeError("Timed out waiting for inference-perf job pod to be created.")
        
    print(f"Found benchmark job pod: {job_pod_name}. Waiting for completion...")
    run_cmd(f"kubectl wait --for=condition=complete job/inference-perf-job -n {ns} --timeout=15m")
    print("Benchmark job completed.")

def cleanup_namespace(ns):
    print(f"Cleaning up namespace: {ns}")
    run_cmd(f"kubectl delete namespace {ns} --wait=false")

def write_results_to_markdown(results_file, run_time, ns, images, idle_metrics, peak_metrics, p50, p95, status):
    file_exists = os.path.exists(results_file)
    
    with open(results_file, "a") as f:
        if not file_exists:
            # Write header
            f.write("# EPP Router Performance Benchmarking Results\n\n")
            f.write("| Timestamp | Namespace | EPP Images | Container | Idle CPU (m) | Idle Mem (MiB) | Peak CPU (m) | Peak Mem (MiB) | P50 Latency (ms) | P95 Latency (ms) | Status |\n")
            f.write("|---|---|---|---|---|---|---|---|---|---|---|\n")
            
        # Write rows for each container
        for container in sorted(peak_metrics.keys()):
            idle_cpu = idle_metrics.get(container, {}).get('cpu', '-')
            idle_mem = idle_metrics.get(container, {}).get('mem', '-')
            peak_cpu = peak_metrics.get(container, {}).get('cpu', '-')
            peak_mem = peak_metrics.get(container, {}).get('mem', '-')
            
            epp_images = "<br>".join(images)
            f.write(f"| {run_time} | {ns} | {epp_images} | {container} | {idle_cpu} | {idle_mem} | {peak_cpu} | {peak_mem} | {p50:.2f} | {p95:.2f} | {status} |\n")


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    
    # Resolve relative defaults
    default_sim_deploy = os.path.join(script_dir, "perf-configs", "llm-d-sim-deployment.yaml")
    default_sim_svc = os.path.join(script_dir, "perf-configs", "llm-d-sim-service.yaml")
    default_router_chart = os.path.abspath(os.path.join(script_dir, "..", "..", "llm-d-router", "config/charts/llm-d-router-standalone"))
    default_router_values = os.path.join(script_dir, "perf-configs", "router-optimized-baseline.values.yaml")
    default_perf_chart = os.path.abspath(os.path.join(script_dir, "..", "..", "..", "inference-perf", "deploy", "inference-perf"))
    default_perf_job = os.path.join(script_dir, "perf-configs", "shared_prefix_job1.yaml")
    default_results_file = os.path.abspath(os.path.join(script_dir, "..", "..", "..", "loadtesting", "perf_results.md"))

    parser = argparse.ArgumentParser(description="Nightly EPP Performance Benchmarking")
    parser.add_argument("--namespace", default=None, help="Dedicated namespace name (auto-generated if omitted)")
    parser.add_argument("--sim-deploy", default=default_sim_deploy, help="Path to simulator deployment yaml")
    parser.add_argument("--sim-svc", default=default_sim_svc, help="Path to simulator service yaml")
    parser.add_argument("--router-chart", default=default_router_chart, help="Path to EPP Helm chart")
    parser.add_argument("--router-values", default=default_router_values, help="Path to EPP Helm values")
    parser.add_argument("--perf-chart", default=default_perf_chart, help="Path to inference-perf Helm chart")
    parser.add_argument("--perf-job", default=default_perf_job, help="Path to inference-perf job configuration")
    parser.add_argument("--results-file", default=default_results_file, help="Path to output markdown results file")
    parser.add_argument("--sim-replicas", type=int, default=10, help="Number of simulator replicas")
    parser.add_argument("--no-cleanup", action="store_true", help="Skip namespace deletion on completion")
    parser.add_argument("--enable-workload-identity", action="store_true", help="Annotate service account with Workload Identity configuration")
    parser.add_argument("--gcp-project", default=None, help="Google Cloud project ID (inferred from active gcloud config if omitted)")
    parser.add_argument("--epp-cpu", default="2", help="EPP CPU request (limit will be 2x this amount)")
    parser.add_argument("--epp-memory", default="4Gi", help="EPP memory request (limit will be 2x this amount)")
    parser.add_argument("--router-machine-family", default=None, help="Add node affinity for specific Google Cloud machine-family (e.g. c3)")
    args = parser.parse_args()


    global stop_monitoring
    run_time = time.strftime("%Y-%m-%d %H:%M:%S")
    ns = args.namespace if args.namespace else f"llm-d-perf-{int(time.time())}"
    
    print(f"=== EPP Benchmarking Suite Started at {run_time} ===")
    print(f"Namespace: {ns}")

    status = "SUCCESS"
    idle_metrics = {}
    peak_metrics = {}
    p50, p95 = 0.0, 0.0
    images = []

    try:
        # Step 1: Create Namespace
        create_namespace(ns)

        # Step 2: Setup HF Secret
        setup_hf_secret(ns)

        # Resolve GCP project if needed
        gcp_project = args.gcp_project
        if not gcp_project:
            res = run_cmd("gcloud config get-value project", check=False)
            if res.returncode == 0:
                gcp_project = res.stdout.strip()
            if not gcp_project:
                raise RuntimeError("GCP project ID could not be determined. Please specify it via --gcp-project.")
        print(f"Using GCP Project: {gcp_project}")

        # Step 2.5: Setup inference-perf ServiceAccount
        setup_perf_sa(ns, args.enable_workload_identity, gcp_project)

        # Step 3: Deploy Simulators
        deploy_simulators(ns, args.sim_deploy, args.sim_svc, args.sim_replicas)

        # Step 4: Deploy EPP Standalone Router
        deploy_epp(
            ns, 
            args.router_chart, 
            args.router_values, 
            epp_cpu=args.epp_cpu, 
            epp_memory=args.epp_memory, 
            machine_family=args.router_machine_family
        )

        # Get EPP pod details
        pod_name = get_epp_pod_name(ns)
        print(f"EPP Pod name: {pod_name}")
        images = get_container_images(ns, pod_name)
        print(f"EPP Container images: {images}")

        # Step 5: Measure Idle Performance
        print("Measuring idle resource usage (waiting up to 5m for metrics-server)...")
        time.sleep(15)
        idle_samples = []
        start_time = time.time()
        timeout = 300  # 5 minutes
        backoff = 5
        
        while len(idle_samples) < 3:
            if time.time() - start_time > timeout:
                print("Timeout waiting for idle resource metrics.")
                break
                
            sample = sample_resources(ns, pod_name)
            if sample:
                idle_samples.append(sample)
                print(f"Captured idle metrics sample {len(idle_samples)}/3.")
                backoff = 5
                if len(idle_samples) < 3:
                    time.sleep(5)
            else:
                print(f"Metrics not available yet. Retrying in {backoff}s...")
                time.sleep(backoff)
                backoff = min(backoff * 2, 30)

        if idle_samples:
            for container in idle_samples[0].keys():
                cpus = [s[container]['cpu'] for s in idle_samples if container in s]
                mems = [s[container]['mem'] for s in idle_samples if container in s]
                idle_metrics[container] = {
                    'cpu': int(sum(cpus)/len(cpus)) if cpus else 0,
                    'mem': int(sum(mems)/len(mems)) if mems else 0
                }
            print(f"Idle metrics captured: {idle_metrics}")
        else:
            print("Warning: failed to capture idle metrics.")

        # Step 6: Scrape Pre-Benchmark Metrics
        metrics_before = scrape_scheduler_metrics(ns, pod_name)

        # Step 7: Execute Benchmark & Monitor Peak Usage
        monitoring_thread = Thread(target=monitor_resources_loop, args=(ns, pod_name, 5, peak_metrics))
        monitoring_thread.start()

        try:
            run_benchmark(ns, args.perf_job, args.perf_chart)
        finally:
            stop_monitoring = True
            monitoring_thread.join()

        # Step 8: Scrape Post-Benchmark Metrics & Compute Latency
        metrics_after = scrape_scheduler_metrics(ns, pod_name)
        p50, p95 = calculate_percentiles(metrics_before, metrics_after)
        print(f"Benchmark latencies: P50 = {p50:.2f} ms, P95 = {p95:.2f} ms")
        print(f"Peak resource usage: {peak_metrics}")

    except Exception as e:
        print(f"Test run failed with exception: {e}", file=sys.stderr)
        status = "FAILED"
        import traceback
        traceback.print_exc()
    finally:
        # Step 9: Teardown & Cleanup
        if not args.no_cleanup:
            cleanup_namespace(ns)
        else:
            print(f"Skipping namespace cleanup. Namespace '{ns}' remains active.")

        # Step 10: Log Results to Markdown File
        if idle_metrics or peak_metrics:
            write_results_to_markdown(args.results_file, run_time, ns, images, idle_metrics, peak_metrics, p50, p95, status)
            print(f"Performance results successfully written to {args.results_file}")

if __name__ == "__main__":
    main()
