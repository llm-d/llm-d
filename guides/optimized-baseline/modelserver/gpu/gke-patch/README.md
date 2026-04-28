# GKE Patches

## Disabling NCCL Tuner Plugin

You need this patch when running tensor parallelism on a GKE cluster that has the gIB NCCL RDMA libraries installed. gIB is generally not required for inference workloads.

### Diagnosis

If gIB is installed, vLLM will try to load the gIB NCCL tuner plugin, which will fail. To see the error log, add the following environment variable to your model server deployment:

```
env:
    - name: NCCL_DEBUG
      value: "INFO"
```

You will see NCCL tuner error message like:

```
NCCL WARN No NCCL_TUNER_CONFIG_PATH provided. Please populate NCCL_TUNER_CONFIG_PATH to use config-based tuner plugin.
NCCL INFO plugin/tuner/tuner_v2.cc:50 -> 3

(Worker pid=628) ERROR ... RuntimeError: NCCL error: internal error - please report this issue to the NCCL developers
```

### Fix

Disable the tuner plugin with the following environment variable:

```
env:
    - name: NCCL_TUNER_PLUGIN
      value: "none"
    - name: NCCL_NET_PLUGIN
      value: ""
```
