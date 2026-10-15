# GKE Patches - NCCL Tuner Configuration

## Disabling NCCL Tuner Plugin

You need this patch component when running tensor parallelism on a GKE cluster that has the gIB NCCL RDMA libraries installed. gIB is generally not required for inference workloads.

### Diagnosis

If gIB is installed, vLLM and other engines will try to load the gIB NCCL tuner plugin, which will fail.

To verify gIB is installed on a node, run the following command on the node:

```bash
ls /home/kubernetes/bin/gib
```

If you see the folder is not empty, it means gIB is installed.

To see the NCCL error log, add the following environment variable to your model server deployment:

```yaml
env:
  - name: NCCL_DEBUG
    value: "INFO"
```

You will see an NCCL tuner error message like:

```
NCCL WARN No NCCL_TUNER_CONFIG_PATH provided. Please populate NCCL_TUNER_CONFIG_PATH to use config-based tuner plugin.
NCCL INFO plugin/tuner/tuner_v2.cc:50 -> 3

(Worker pid=628) ERROR ... RuntimeError: NCCL error: internal error - please report this issue to the NCCL developers
```

### Fix

Disable the tuner plugin with the following environment variables:

```yaml
env:
  - name: NCCL_TUNER_PLUGIN
    value: "none"
  - name: NCCL_NET_PLUGIN
    value: ""
```

This shared component automatically patches these variables into your Deployment containers named `modelserver`.
