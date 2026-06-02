# GKE Patches - Autopilot Node Selection

## GKE Autopilot Node Selection Component

This component adds GKE Autopilot-specific node selection hints to deployments. By default, it targets GKE Autopilot's `nvidia-h100-80gb` accelerator node pool and configures the workload to run on **Spot instances** for cost efficiency.

### Why this is needed

On GKE Autopilot, workloads requesting GPUs must specify a `nodeSelector` indicating which GPU accelerator type they require (such as `nvidia-l4`, `nvidia-a100-80gb`, etc.). Without this hint, GKE Autopilot will not know which GPU hardware to provision for the pods.

By using this component, you separate the environment/cluster-specific GKE node selection logic from the core, engine-agnostic base deployment configuration.

### Usage

To apply this GKE Autopilot node selection patch, reference it under the `components` section in your environment's `kustomization.yaml`:

```yaml
apiVersion: kustomize.config.k8s.io/v1beta1
kind: Kustomization

resources:
  # Reference your model server base (e.g. single-host or PD)
  - ../../base/single-host/default

components:
  # Enable GKE Autopilot node selection
  - ../../components/gke-autopilot
```

### Customization Options

#### 1. Spot vs. On-Demand Instances

By default, the component includes the Spot instance selector:
```yaml
cloud.google.com/gke-spot: "true"
```
* **To use Spot (Default):** Keep as is. This is highly recommended for development, testing, and non-critical batch inference to significantly reduce GKE costs.
* **To use On-Demand:** Edit `guides/recipes/modelserver/components/gke-autopilot/kustomization.yaml` and set `cloud.google.com/gke-spot` to `"false"` or delete the line entirely.

#### 2. Customizing the GPU Type

If you are using a different GPU accelerator type (e.g., `nvidia-a100-80gb` or `nvidia-h100-80gb`), update the `cloud.google.com/gke-accelerator` value under `nodeSelector`:
```yaml
nodeSelector:
  cloud.google.com/gke-accelerator: nvidia-a100-80gb
```
