"""
Script to inject HF_TOKEN from a secret into an LMBenchmark job.
This is a temporary workaround until the PR is merged in fmperf.
https://github.com/fmperf-project/fmperf/pull/41
"""

import os
import time
import logging
from kubernetes import client, config, watch

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def inject_hf_token(namespace, job_name, secret_name, secret_key="HF_TOKEN"):
    """
    Wait for a job to be created and inject HF_TOKEN from a secret.
    
    Args:
        namespace (str): Kubernetes namespace
        job_name (str): Name of the job to watch for
        secret_name (str): Name of the secret containing the HF_TOKEN
        secret_key (str): Key in the secret for the HF_TOKEN (default: "HF_TOKEN")
    """
    logger.info("*** INJECT SCRIPT VERSION: 2025-05-18-V2 ***")
    logger.info(f"Starting to watch for job {job_name} in namespace {namespace}")
    logger.info(f"Will inject HF_TOKEN from secret {secret_name}")
    
    # Load kubernetes configuration
    try:
        config.load_incluster_config()
        logger.info("Loaded in-cluster Kubernetes configuration")
    except config.ConfigException:
        try:
            config.load_kube_config()
            logger.info("Loaded kube-config")
        except config.ConfigException as e:
            logger.error(f"Could not configure Kubernetes client: {e}")
            return
    
    batch_api = client.BatchV1Api()
    
    # Wait for the job to be created
    for i in range(10):  # Try for a maximum of 10 times
        try:
            job = batch_api.read_namespaced_job(name=job_name, namespace=namespace)
            break
        except client.exceptions.ApiException as e:
            if e.status != 404:  # If it's not a "not found" error, raise it
                raise
            logger.info(f"Job {job_name} not found yet, waiting (attempt {i+1}/10)...")
            time.sleep(5)
    else:
        logger.error(f"Timed out waiting for job {job_name} to be created")
        return
    
    # Fix init containers - replace the command completely according to the diff
    if job.spec.template.spec.init_containers:
        for init_container in job.spec.template.spec.init_containers:
            if init_container.name == "init-cache-dirs":
                logger.info("Modifying init-cache-dirs container command")
                
                # The exact change from the diff:
                # From: "mkdir -p /requests/hf_cache/datasets && FOLDER_NAME=$(echo $SAVE_FILE_KEY | sed 's|/requests/||' | sed 's|/LMBench||') && mkdir -p /requests/$FOLDER_NAME && chmod -R 777 /requests && ls -la /requests"
                # To:   "mkdir -p /requests/hf_cache/datasets && FOLDER_NAME=$(echo $SAVE_FILE_KEY | sed 's|/requests/||' | sed 's|/LMBench||') && mkdir -p /requests/$FOLDER_NAME && ls -la /requests"
                
                if isinstance(init_container.command, list) and len(init_container.command) >= 3:
                    for i, cmd in enumerate(init_container.command):
                        if i + 2 < len(init_container.command) and cmd == "sh" and init_container.command[i+1] == "-c":
                            # Found the shell command
                            original_cmd = init_container.command[i+2]
                            
                            # Replace the specific pattern from the diff
                            if "chmod -R 777 /requests" in original_cmd:
                                logger.info(f"Original init command: {original_cmd}")
                                
                                # Direct replacement of the specific pattern
                                new_cmd = original_cmd.replace(" && chmod -R 777 /requests", "")
                                
                                # Set the new command
                                init_container.command[i+2] = new_cmd
                                logger.info(f"Updated init command: {new_cmd}")
    
    # Check container args for bashrc-related issues in lmbenchmark container
    containers = job.spec.template.spec.containers
    for container in containers:
        if container.name == "lmbenchmark" and container.args:
            for i, arg in enumerate(container.args):
                # Check if this is the arg with bashrc and venv activation
                if ". .venv/bin/activate" in arg:
                    # From the diff:
                    # Replace: ". ~/.bashrc && . .venv/bin/activate && . .venv/bin/activate"
                    # With:    ". .venv/bin/activate"
                    if ". ~/.bashrc && . .venv/bin/activate && . .venv/bin/activate" in arg:
                        logger.info(f"Original bash arg: {arg}")
                        
                        # Direct replacement from the diff
                        container.args[i] = arg.replace(". ~/.bashrc && . .venv/bin/activate && . .venv/bin/activate", ". .venv/bin/activate")
                        
                        logger.info(f"Updated bash arg: {container.args[i]}")
    
    # Add HF_TOKEN from secret
    for container in containers:
        if not container.env:
            container.env = []
        
        # Check if token already exists
        token_exists = False
        for env in container.env:
            if env.name == "HF_TOKEN" and env.value_from and env.value_from.secret_key_ref:
                logger.info(f"HF_TOKEN already injected in container {container.name}")
                token_exists = True
                break
        
        if not token_exists:
            # Add HF_TOKEN from secret
            container.env.append(
                client.V1EnvVar(
                    name="HF_TOKEN",
                    value_from=client.V1EnvVarSource(
                        secret_key_ref=client.V1SecretKeySelector(
                            name=secret_name,
                            key=secret_key
                        )
                    )
                )
            )
            logger.info(f"Added HF_TOKEN to container {container.name}")
    
    # Update the job
    batch_api.patch_namespaced_job(
        name=job_name,
        namespace=namespace,
        body=job
    )
    
    logger.info(f"Successfully patched job {job_name} with HF_TOKEN from secret {secret_name}")

def main():
    # Get parameters from environment
    namespace = os.environ.get("FMPERF_NAMESPACE", "fmperf")
    job_id = os.environ.get("FMPERF_JOB_ID", "")
    job_name = f"lmbenchmark-evaluate-{job_id}" if job_id else "lmbenchmark-evaluate"
    secret_name = os.environ.get("HF_TOKEN_SECRET")
    
    if not secret_name:
        logger.warning("HF_TOKEN_SECRET environment variable not set, skipping injection")
        return
    
    try:
        inject_hf_token(namespace, job_name, secret_name)
    except Exception as e:
        logger.error(f"Error injecting HF_TOKEN: {e}")

if __name__ == "__main__":
    main() 
