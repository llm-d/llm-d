"""
Modified version of example_openshift.py to run in a Kubernetes pod.
This script assumes it's running inside a pod and uses the environment variables
provided by the job configuration.
"""

import os
import urllib3
import yaml
import logging
import json
import threading
import subprocess
from datetime import datetime
import types
import time

import kubernetes
from kubernetes import client

from fmperf.Cluster import Cluster
from fmperf import LMBenchmarkWorkload
from fmperf.StackSpec import StackSpec
from fmperf.utils import run_benchmark

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Disable SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

"""
Script to inject HF_TOKEN from a secret into an LMBenchmark job.
This is a temporary workaround until the PR is merged in fmperf.
https://github.com/fmperf-project/fmperf/pull/41
"""
def inject_hf_token_in_background():
    """Run the inject_hf_token.py script in a background thread"""
    # Check if the HF_TOKEN_SECRET environment variable is set
    if not os.environ.get("HF_TOKEN_SECRET"):
        logger.info("HF_TOKEN_SECRET not set, skipping token injection")
        return
    
    # Log that we're injecting the token
    logger.info("Starting HF_TOKEN injection in background thread")
    logger.info("Note: This is a temporary workaround until PR is merged in fmperf")
    
    # Import and run the inject_hf_token module
    try:
        import inject_hf_token
        thread = threading.Thread(target=inject_hf_token.main)
        thread.daemon = True
        thread.start()
        logger.info("HF_TOKEN injection thread started")
    except ImportError:
        # If the module is not found, try to run it as a subprocess
        logger.warning("Failed to import inject_hf_token module, trying subprocess")
        try:
            script_path = os.path.join(os.path.dirname(__file__), "inject_hf_token.py")
            subprocess.Popen(["python", script_path])
            logger.info("HF_TOKEN injection subprocess started")
        except Exception as e:
            logger.error(f"Failed to start HF_TOKEN injection: {e}")

def update_workload_config(workload_spec, env_vars):
    """Update workload configuration with environment variables if provided."""
    logger.info("Updating workload configuration from environment variables")
    if 'FMPERF_BATCH_SIZE' in env_vars:
        workload_spec.batch_size = int(env_vars['FMPERF_BATCH_SIZE'])
        logger.info(f"Set batch_size to {workload_spec.batch_size}")
    if 'FMPERF_SEQUENCE_LENGTH' in env_vars:
        workload_spec.sequence_length = int(env_vars['FMPERF_SEQUENCE_LENGTH'])
        logger.info(f"Set sequence_length to {workload_spec.sequence_length}")
    if 'FMPERF_MAX_TOKENS' in env_vars:
        workload_spec.max_tokens = int(env_vars['FMPERF_MAX_TOKENS'])
        logger.info(f"Set max_tokens to {workload_spec.max_tokens}")
    if 'FMPERF_NUM_USERS_WARMUP' in env_vars:
        workload_spec.num_users_warmup = int(env_vars['FMPERF_NUM_USERS_WARMUP'])
        logger.info(f"Set num_users_warmup to {workload_spec.num_users_warmup}")
    if 'FMPERF_NUM_USERS' in env_vars:
        workload_spec.num_users = int(env_vars['FMPERF_NUM_USERS'])
        logger.info(f"Set num_users to {workload_spec.num_users}")
    if 'FMPERF_NUM_ROUNDS' in env_vars:
        workload_spec.num_rounds = int(env_vars['FMPERF_NUM_ROUNDS'])
        logger.info(f"Set num_rounds to {workload_spec.num_rounds}")
    if 'FMPERF_SYSTEM_PROMPT' in env_vars:
        workload_spec.system_prompt = int(env_vars['FMPERF_SYSTEM_PROMPT'])
        logger.info(f"Set system_prompt to {workload_spec.system_prompt}")
    if 'FMPERF_CHAT_HISTORY' in env_vars:
        workload_spec.chat_history = int(env_vars['FMPERF_CHAT_HISTORY'])
        logger.info(f"Set chat_history to {workload_spec.chat_history}")
    if 'FMPERF_ANSWER_LEN' in env_vars:
        workload_spec.answer_len = int(env_vars['FMPERF_ANSWER_LEN'])
        logger.info(f"Set answer_len to {workload_spec.answer_len}")
    if 'FMPERF_TEST_DURATION' in env_vars:
        workload_spec.test_duration = int(env_vars['FMPERF_TEST_DURATION'])
        logger.info(f"Set test_duration to {workload_spec.test_duration}")
    
    return workload_spec

def save_results_to_pvc(results, run_id):
    """Save benchmark results to the PVC-mounted directory."""
    results_dir = os.environ.get("FMPERF_RESULTS_DIR", "/requests")  # Default to /requests if not set
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    run_dir = os.path.join(results_dir, f"run_{timestamp}_{run_id}")
    os.makedirs(run_dir, exist_ok=True)

    results_file = os.path.join(run_dir, "benchmark_results.json")
    with open(results_file, 'w') as f:
        json.dump(results, f, indent=2)

    logger.info(f"Results saved to {results_file}")

def list_directory_contents(path, indent=0):
    """List contents of a directory recursively with proper indentation."""
    try:
        for item in os.listdir(path):
            item_path = os.path.join(path, item)
            if os.path.isdir(item_path):
                logger.info("  " * indent + f"📁 {item}/")
                list_directory_contents(item_path, indent + 1)
            else:
                size = os.path.getsize(item_path)
                logger.info("  " * indent + f"📄 {item} ({size} bytes)")
    except Exception as e:
        logger.error(f"Error listing directory {path}: {str(e)}")

# Monkey patch the evaluate method to fix issues before job creation
def _patched_evaluate(self, *args, **kwargs):
    # Store the original method
    original_evaluate = self._original_evaluate
    
    # Get the job_id from environment to properly identify generated job
    job_id = os.environ.get("FMPERF_JOB_ID", "")
    expected_job_name = f"lmbenchmark-evaluate-{job_id}" if job_id else "lmbenchmark-evaluate"
    logger.info(f"Will intercept and patch job: {expected_job_name}")
    
    # Get the results directory for this job
    results_dir = os.environ.get("FMPERF_RESULTS_DIR", "/requests")
    logger.info(f"Using results directory: {results_dir}")
    
    # Call the original method to get the job manifest
    # But we need to intercept the job creation
    batch_api_create = client.BatchV1Api.create_namespaced_job
    created_manifest = None

    def intercept_job_creation(self, namespace, body, **kwargs):
        nonlocal created_manifest
        # Store the manifest for modification
        created_manifest = body
        
        # Check if this is the job we want to modify
        job_name = created_manifest["metadata"]["name"]
        logger.info(f"Intercepted job creation: {job_name}")
        
        # Only modify the job if it matches our expected pattern
        if job_name.startswith("lmbenchmark-evaluate"):
            logger.info(f"Patching job {job_name}")
            
            # Add pod anti-affinity to ensure jobs don't run on the same node
            if "affinity" not in created_manifest["spec"]["template"]["spec"]:
                created_manifest["spec"]["template"]["spec"]["affinity"] = {}
            
            if "podAntiAffinity" not in created_manifest["spec"]["template"]["spec"]["affinity"]:
                created_manifest["spec"]["template"]["spec"]["affinity"]["podAntiAffinity"] = {}
            
            # Add anti-affinity to ensure pods don't get scheduled on the same node
            # This makes sure each evaluation job runs on a different node
            if "preferredDuringSchedulingIgnoredDuringExecution" not in created_manifest["spec"]["template"]["spec"]["affinity"]["podAntiAffinity"]:
                created_manifest["spec"]["template"]["spec"]["affinity"]["podAntiAffinity"]["preferredDuringSchedulingIgnoredDuringExecution"] = []
            
            # Add anti-affinity terms for all evaluation jobs using more precise and comprehensive matching
            anti_affinity_term = {
                "weight": 100,
                "podAffinityTerm": {
                    "labelSelector": {
                        "matchExpressions": [
                            {
                                "key": "job-name",
                                "operator": "Exists"
                            }
                        ]
                    },
                    "topologyKey": "kubernetes.io/hostname"
                }
            }
            
            created_manifest["spec"]["template"]["spec"]["affinity"]["podAntiAffinity"]["preferredDuringSchedulingIgnoredDuringExecution"].append(anti_affinity_term)
            logger.info("Added pod anti-affinity to evaluation job")
            
            # *** CRITICAL FIX: Completely remove the volume mounts for requests directory ***
            # This prevents multi-attach issues by not using the PVC at all in evaluation jobs
            volumes_to_keep = []
            if created_manifest["spec"]["template"]["spec"].get("volumes"):
                for vol in created_manifest["spec"]["template"]["spec"]["volumes"]:
                    # Keep volumes that don't reference PVCs with our results dirs
                    pvc_name = vol.get("persistentVolumeClaim", {}).get("claimName", "")
                    if (pvc_name != "baseline-results-pvc" and 
                        pvc_name != "llm-d-results-pvc" and 
                        pvc_name != "fmperf-results-pvc"):
                        volumes_to_keep.append(vol)
                
                # Replace volumes list with filtered list
                created_manifest["spec"]["template"]["spec"]["volumes"] = volumes_to_keep
                logger.info(f"Removed PVC volumes from evaluation job, kept {len(volumes_to_keep)} volumes")
            
            # Remove all volume mounts referencing the results directories
            for container in created_manifest["spec"]["template"]["spec"].get("containers", []):
                if container.get("volumeMounts"):
                    mounts_to_keep = []
                    for mount in container["volumeMounts"]:
                        if (mount["mountPath"] != "/requests" and 
                            mount["mountPath"] != "/baseline-requests" and 
                            mount["mountPath"] != "/llmd-requests"):
                            mounts_to_keep.append(mount)
                    
                    # Replace volume mounts with filtered list
                    container["volumeMounts"] = mounts_to_keep
                    logger.info(f"Removed results directory volume mounts from container {container['name']}")
            
            # Also handle init containers if present
            for init_container in created_manifest["spec"]["template"]["spec"].get("initContainers", []):
                if init_container.get("volumeMounts"):
                    mounts_to_keep = []
                    for mount in init_container["volumeMounts"]:
                        if (mount["mountPath"] != "/requests" and 
                            mount["mountPath"] != "/baseline-requests" and 
                            mount["mountPath"] != "/llmd-requests"):
                            mounts_to_keep.append(mount)
                    
                    # Replace volume mounts with filtered list
                    init_container["volumeMounts"] = mounts_to_keep
                    logger.info(f"Removed results directory volume mounts from init container {init_container['name']}")
            
            # Fix the init container command to remove the chmod
            if created_manifest["spec"]["template"]["spec"].get("initContainers"):
                for init_container in created_manifest["spec"]["template"]["spec"]["initContainers"]:
                    if init_container["name"] == "init-cache-dirs" and init_container.get("command"):
                        for i, cmd in enumerate(init_container["command"]):
                            if cmd == "sh" and i+1 < len(init_container["command"]) and init_container["command"][i+1] == "-c":
                                if i+2 < len(init_container["command"]) and "chmod -R 777 /requests" in init_container["command"][i+2]:
                                    logger.info(f"Original init command: {init_container['command'][i+2]}")
                                    # Remove the init command entirely since we don't have the volume anymore
                                    init_container["command"][i+2] = "mkdir -p /tmp/benchmark && echo 'Init complete'"
                                    logger.info(f"Updated init command: {init_container['command'][i+2]}")
                    
                    # Update volume mounts to match our results_dir
                    if results_dir != "/requests":
                        for mount in init_container.get("volumeMounts", []):
                            if mount["mountPath"] == "/requests":
                                logger.info(f"Updating init container volume mount from /requests to {results_dir}")
                                mount["mountPath"] = results_dir
                                    
            # Fix container args if needed
            if created_manifest["spec"]["template"]["spec"].get("containers"):
                for container in created_manifest["spec"]["template"]["spec"]["containers"]:
                    if container["name"] == "lmbenchmark" and container.get("args"):
                        for i, arg in enumerate(container["args"]):
                            if ". .venv/bin/activate" in arg:
                                # We need to completely remove the ~/.bashrc reference
                                if ". ~/.bashrc" in arg:
                                    logger.info(f"Original bash arg: {arg}")
                                    
                                    # Complete removal of any bashrc references
                                    new_arg = arg.replace(". ~/.bashrc && ", "")
                                    new_arg = new_arg.replace(" && . .venv/bin/activate", "")
                                    if ". .venv/bin/activate && . .venv/bin/activate" in new_arg:
                                        new_arg = new_arg.replace(". .venv/bin/activate && . .venv/bin/activate", ". .venv/bin/activate")
                                        
                                    container["args"][i] = new_arg
                                    logger.info(f"Updated bash arg: {container['args'][i]}")
                                    
                            # Update paths in args if we're using a different results_dir
                            if results_dir != "/requests" and "/requests" in arg:
                                container["args"][i] = arg.replace("/requests", results_dir)
                                logger.info(f"Updated path in args: {container['args'][i]}")
                    
                    # Update environment variables with /requests paths to use our results_dir
                    if results_dir != "/requests":
                        for env in container.get("env", []):
                            if env.get("value") and isinstance(env["value"], str) and "/requests" in env["value"]:
                                old_value = env["value"]
                                env["value"] = env["value"].replace("/requests", results_dir)
                                logger.info(f"Updated env var {env['name']} from {old_value} to {env['value']}")
                    
                    # Update volume mounts to use our results_dir
                    if results_dir != "/requests":
                        for mount in container.get("volumeMounts", []):
                            if mount["mountPath"] == "/requests":
                                logger.info(f"Updating container volume mount from /requests to {results_dir}")
                                mount["mountPath"] = results_dir
                    
                    # Add HF_TOKEN from secret if specified
                    secret_name = os.environ.get("HF_TOKEN_SECRET")
                    if secret_name:
                        if not container.get("env"):
                            container["env"] = []
                        
                        # Check if token already exists
                        token_exists = False
                        for env in container["env"]:
                            if env["name"] == "HF_TOKEN" and env.get("valueFrom") and env["valueFrom"].get("secretKeyRef"):
                                token_exists = True
                                break
                        
                        if not token_exists:
                            logger.info(f"Adding HF_TOKEN from secret {secret_name} before job creation")
                            container["env"].append({
                                "name": "HF_TOKEN",
                                "valueFrom": {
                                    "secretKeyRef": {
                                        "name": secret_name,
                                        "key": "HF_TOKEN"
                                    }
                                }
                            })
        else:
            logger.info(f"Skipping job {job_name} (not a benchmark evaluation job)")
        
        # Continue with the original job creation
        return batch_api_create(self, namespace, body, **kwargs)
    
    # Replace the job creation method with our interceptor
    client.BatchV1Api.create_namespaced_job = intercept_job_creation
    
    try:
        # Now call the original evaluate method, which will use our patched job creation
        result = original_evaluate(*args, **kwargs)
        return result
    finally:
        # Restore the original job creation method
        client.BatchV1Api.create_namespaced_job = batch_api_create

def main():
    logger.info("Starting benchmark run - VERSION: 2025-05-18-PATCH-NO-PVC-MULTI")
    env_vars = os.environ
    stack_name = env_vars.get("FMPERF_STACK_NAME", "llm-d-32b-instruct")
    stack_type = env_vars.get("FMPERF_STACK_TYPE", "llm-d")
    endpoint_url = env_vars.get("FMPERF_ENDPOINT_URL", "inference-gateway")
    workload_file = env_vars.get("FMPERF_WORKLOAD_FILE", "lmbench_llama32b_instruct.yaml")
    repetition = int(env_vars.get("FMPERF_REPETITION", "1"))
    namespace = env_vars.get("FMPERF_NAMESPACE", "fmperf")
    job_id = env_vars.get("FMPERF_JOB_ID", stack_name)
    
    # Get results directory for configuration
    results_dir = env_vars.get("FMPERF_RESULTS_DIR", "/requests")
    
    # Always use a placeholder directory for all jobs to completely avoid PVC mounting
    # This is the most reliable approach to avoid multi-attach issues
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    placeholder_dir = f"/tmp/fmperf_placeholder_{job_id}_{timestamp}"
    
    logger.info(f"Always using placeholder directory {placeholder_dir} to avoid PVC multi-attach")
    use_placeholder = True
    results_save_dir = placeholder_dir
    # Create placeholder directory
    os.makedirs(placeholder_dir, exist_ok=True)

    logger.info(f"Using configuration:")
    logger.info(f"  Stack name: {stack_name}")
    logger.info(f"  Stack type: {stack_type}")
    logger.info(f"  Endpoint URL: {endpoint_url}")
    logger.info(f"  Workload file: {workload_file}")
    logger.info(f"  Repetition: {repetition}")
    logger.info(f"  Namespace: {namespace}")
    logger.info(f"  Job ID: {job_id}")
    logger.info(f"  Results directory (PVC): {results_dir}")
    logger.info(f"  Using placeholder directory: {placeholder_dir}")

    # Start the HF_TOKEN injection script in a background thread
    inject_hf_token_in_background()

    workload_file_path = os.path.join("/app/yamls", workload_file)
    logger.info(f"Loading workload configuration from {workload_file_path}")
    workload_spec = LMBenchmarkWorkload.from_yaml(workload_file_path)
    
    logger.info("Updating workload configuration with environment variables")
    workload_spec = update_workload_config(workload_spec, env_vars)

    logger.info("Creating stack specification")
    stack_spec = StackSpec(
        name=stack_name,
        stack_type=stack_type,
        refresh_interval=300,
        endpoint_url=endpoint_url
    )

    logger.info("Initializing Kubernetes client")
    kubernetes.config.load_incluster_config()
    apiclient = client.ApiClient()
    cluster = Cluster(name="in-cluster", apiclient=apiclient, namespace=namespace)

    # Apply the monkey patch before running the benchmark
    logger.info("Applying monkey patch to intercept job creation")
    cluster._original_evaluate = cluster.evaluate
    cluster.evaluate = types.MethodType(_patched_evaluate, cluster)

    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    
    logger.info("Starting benchmark run")
    try:
        results = run_benchmark(
            cluster=cluster,
            stack_spec=stack_spec,
            workload_spec=workload_spec,
            repetition=repetition,
            id=job_id,
        )
        
        # Save results if we have them and a place to save them
        if results and not use_placeholder:
            # Use the real PVC if available
            logger.info(f"Saving results to {results_dir}")
            save_results_to_pvc(results, run_id)
            
            # List contents of results directory
            logger.info(f"\nContents of {results_dir} directory:")
            list_directory_contents(results_dir)
        elif results:
            # Otherwise just log that we would have saved results
            logger.info(f"Would save results to {results_dir}/run_{run_id}_{job_id}")
            logger.info(f"(Actually using placeholder at {placeholder_dir})")
            logger.info("Note: Parent job doesn't have PVC mounted to avoid multiattach error")
        
        # Add a delay to ensure the job has time to be created
        # This is mostly useful in the compare jobs scenario
        if use_placeholder:
            logger.info("Sleeping for 10 seconds to ensure job has time to be created...")
            time.sleep(10)
            
        logger.info("Benchmark run completed successfully")

    except Exception as e:
        logger.error(f"Benchmark run failed: {str(e)}")
        raise

if __name__ == "__main__":
    main()
