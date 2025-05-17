FROM registry.access.redhat.com/ubi9/python-39:latest

WORKDIR /app

# Copy requirements and run_benchmark.py
COPY requirements.txt .

# Script to inject HF_TOKEN from a secret into an LMBenchmark job.
# This is a temporary workaround until the PR is merged in fmperf.
# https://github.com/fmperf-project/fmperf/pull/41
COPY inject_hf_token.py /app/

COPY run_benchmark.py /app/

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Add GitHub repository as a pip requirement
RUN pip install git+https://github.com/fmperf-project/fmperf.git@main

ENV PYTHONPATH=/app

ENTRYPOINT ["python", "/app/run_benchmark.py"]
