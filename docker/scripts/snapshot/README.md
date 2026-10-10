# GKE Fast Pod Snapshot Utility for vLLM

A modular container snapshot provider and vLLM lifespan wrapper that enables fast pod checkpointing and restoration on Google Kubernetes Engine (GKE) with GKE Sandbox (gVisor) and NVIDIA GPUs.

---

## Overview

Deploying large language models (LLMs) on Kubernetes often incurs high cold-start latencies due to downloading weights, loading them into memory, allocating GPU VRAM, and compiling CUDA graphs.

This package provides a drop-in launcher and snapshot provider that hooks into vLLM's FastAPI application lifespan to:

1. Initialize the vLLM engine and compile CUDA graphs during container cold start.
2. Put the vLLM engine to sleep (`engine.sleep(level=1)`) to release physical GPU VRAM while preserving virtual memory mappings.
3. Purge cached model weight files from disk to minimize the checkpoint storage footprint.
4. Trigger a gVisor userspace checkpoint via `/proc/gvisor/checkpoint`.
5. Restore the container and wake up the engine (`engine.wake_up()`) to re-allocate physical GPU VRAM upon restoration before binding HTTP ports and serving traffic.

> [!NOTE]
> This README is intended for developers maintaining and integrating this snapshot utility. For a comprehensive user guide on single-GPU deployment, cluster configuration, and verification, see the [Pod Snapshots User Guide](../../../guides/pod-snapshot/README.md).

---

## Architecture & Lifecycle

The snapshot lifecycle is orchestrated inside the FastAPI application lifespan context manager ([`vllm/wrapper.py`](vllm/wrapper.py)):

```mermaid
flowchart TD
    A["1. Cold Start Initialization<br/>Engine loads weights, compiles CUDA graphs"] --> B["2. VRAM Release (Sleep)<br/>engine.sleep(level=1) releases physical GPU VRAM"]
    B --> C["3. Disk Cache Purge<br/>Purge MODEL_CACHE_DIR"]
    C --> D["4. Snapshot Trigger (gVisor)<br/>Write to /proc/gvisor/checkpoint & block until restore"]
    D --> E["5. Container Restore & VRAM Allocation (Wake Up)<br/>engine.wake_up() re-allocates physical GPU VRAM"]
    E --> F["6. Serve Traffic<br/>FastAPI binds TCP port and begins serving requests"]
```

---

## Package Layout & Components

```text
docker/scripts/snapshot/
├── __init__.py           # Package exports (GKESnapshotProvider, SnapshotError, get_snapshot_provider)
├── launcher.py           # CLI entrypoint wrapping vllm serve
├── providers.py          # GKESnapshotProvider and provider factory
├── test_providers.py     # Unit test suite
├── README.md             # Developer documentation
├── sglang/
│   ├── __init__.py       # SGLang integration exports
│   ├── launcher.py       # CLI entrypoint wrapping SGLang's launch_server
│   └── wrapper.py        # Warmup hook and snapshot callback
└── vllm/
    ├── __init__.py       # vLLM integration exports
    └── wrapper.py        # FastAPI lifespan context manager patch
```

### Component Details

- **[`launcher.py`](launcher.py)**:
  CLI entrypoint intended to replace `vllm serve` or `python3 -m vllm.entrypoints.openai.api_server`. It dynamically intercepts `vllm.entrypoints.openai.api_server.build_app` to apply `patch_vllm_lifespan()` to the FastAPI application before passing control to `vllm.entrypoints.cli.main()`.

- **[`vllm/wrapper.py`](vllm/wrapper.py) (`patch_vllm_lifespan`)**:
  Wraps the FastAPI application's `router.lifespan_context`. During startup, after the original lifespan context initializes the vLLM engine:
  - Calls `await engine.sleep(level=1)` to release physical GPU memory.
  - Calls `snapshot_provider.trigger()` in a separate thread.
  - Upon process resumption, calls `await engine.wake_up()` to restore GPU memory mappings.
  - Resumes the lifespan lifecycle, allowing Uvicorn to bind TCP ports.

- **[`providers.py`](providers.py) (`GKESnapshotProvider`)**:
  Handles the low-level interaction with gVisor's `/proc/gvisor/checkpoint` interface:
  - `clear_cache()`: Recursively deletes downloaded model weight files from `MODEL_CACHE_DIR` to reduce checkpoint size.
  - `is_available()`: Checks if the procfs checkpoint trigger file is writable.
  - `trigger()`: Clears the cache, opens `/proc/gvisor/checkpoint`, writes `1` to initiate checkpointing, and blocks on a 1-byte read until the container is restored.

- **[`providers.py`](providers.py) (`get_snapshot_provider`)**:
  Factory function that returns a `GKESnapshotProvider` if `SNAPSHOT_PROVIDER=gke_gvisor`, or `None` if unset/disabled.

---

## SGLang

[`sglang/launcher.py`](sglang/launcher.py) starts SGLang's HTTP server with two hooks from [`sglang/wrapper.py`](sglang/wrapper.py), so the sleep/snapshot/wake cycle finishes before the server reports ready:

- `execute_warmup_func` (`sglang_warmup_and_hold`): runs SGLang's server warmup, then holds the server status at `Starting` so readiness probes keep failing.
- `launch_callback` (`sglang_snapshot_callback`): releases GPU memory with `POST /release_memory_occupation`, triggers the snapshot, resumes GPU memory with `POST /resume_memory_occupation`, then restores the server status. SGLang calls `launch_callback` with or without `--skip-server-warmup`.

> [!IMPORTANT]
> Start SGLang with `--enable-memory-saver` and `--enable-weights-cpu-backup`, which together are SGLang's counterpart to vLLM's `--enable-sleep-mode`. Without `--enable-memory-saver`, `POST /release_memory_occupation` succeeds but frees no GPU memory, so the snapshot is taken with that memory still allocated. Without `--enable-weights-cpu-backup`, releasing GPU memory would throw the model weights away instead of copying them to CPU memory.

If the snapshot trigger fails, the server resumes and starts without a snapshot. If releasing or resuming GPU memory fails, the server process is terminated. If either `--enable-memory-saver` or `--enable-weights-cpu-backup` is omitted, no snapshot is taken and a warning is logged.

Scope is single-rank Python HTTP server deployments (`--tokenizer-worker-num 1`, without `SGLANG_RUST_SERVER`).

---

## Running Unit Tests

Run the test suite locally using Python's standard `unittest` runner or `pytest`:

```bash
python3 -m unittest docker/scripts/snapshot/test_providers.py
```

Or:

```bash
pytest docker/scripts/snapshot/test_providers.py
```

> [!NOTE]
> Unit tests are also automatically executed in CI/CD via [`.github/workflows/ci-pr-checks.yaml`](../../../.github/workflows/ci-pr-checks.yaml).
