# Places Where gke-internal-lb-gateway Recipe Can Be Used

This document identifies all locations in the llm-d codebase where the `gke-internal-lb-gateway` recipe can be used as an alternative to the external GKE gateway configuration.

## Well-lit Path Guides

According to the recipe README, it "integrates with all well-lit path guides (Inference Scheduling, PD Disaggregation, Wide EP LWS)". These guides are the primary candidates for using the internal LB gateway.

### 1. Inference Scheduling Guide
**Location:** `guides/inference-scheduling/`

**Current GKE Usage:**
- Uses `httproute.gke.yaml` for GKE deployments (line 109)
- Helmfile supports `gke` and `gke_tpu` environments (helmfile.yaml.gotmpl lines 12-17)
- Uses external GKE gateway class via helmfile values

**How to Use Internal LB:**
```bash
# From guides/inference-scheduling/
kubectl apply -k ../../recipes/gateway/gke-internal-lb-gateway/internal-lb -n ${NAMESPACE}
kubectl apply -f httproute.gke.yaml -n ${NAMESPACE}
```

**Or integrate into helmfile.yaml.gotmpl:**
```yaml
bases:
  - ../../recipes/gateway/gke-internal-lb-gateway/internal-lb
```

### 2. PD Disaggregation Guide
**Location:** `guides/pd-disaggregation/`

**Current GKE Usage:**
- Uses `httproute.gke.yaml` for GKE deployments (README.md line 100)
- Helmfile supports `gke` and `gke_tpu` environments (helmfile.yaml.gotmpl lines 12-16)
- Uses external GKE gateway class via helmfile values

**How to Use Internal LB:**
```bash
# From guides/pd-disaggregation/
kubectl apply -k ../../recipes/gateway/gke-internal-lb-gateway/internal-lb -n ${NAMESPACE}
kubectl apply -f httproute.gke.yaml -n ${NAMESPACE}
```

**Or integrate into helmfile.yaml.gotmpl:**
```yaml
bases:
  - ../../recipes/gateway/gke-internal-lb-gateway/internal-lb
```

### 3. Wide EP LWS Guide
**Location:** `guides/wide-ep-lws/`

**Current GKE Usage:**
- Uses `manifests/gateway/gke-l7-regional-external-managed/` (README.md line 126)
- Has its own gateway manifest structure in `manifests/gateway/`
- Currently references external GKE gateway class

**How to Use Internal LB:**
Replace the gateway deployment command:
```bash
# Instead of:
kubectl apply -k ./manifests/gateway/gke-l7-regional-external-managed -n ${NAMESPACE}

# Use:
kubectl apply -k ../../recipes/gateway/gke-internal-lb-gateway/internal-lb -n ${NAMESPACE}
```

**Note:** This guide could also create an internal-lb variant in its manifests directory to maintain consistency with its structure.

## Feature Guides

### 4. Precise Prefix Cache Aware Guide
**Location:** `guides/precise-prefix-cache-aware/`

**Current GKE Usage:**
- Uses `httproute.gke.yaml` for GKE deployments (README.md line 59)
- Supports GKE as a gateway option

**How to Use Internal LB:**
```bash
# From guides/precise-prefix-cache-aware/
kubectl apply -k ../../recipes/gateway/gke-internal-lb-gateway/internal-lb -n ${NAMESPACE}
kubectl apply -f httproute.gke.yaml -n ${NAMESPACE}
```

### 5. Simulated Accelerators Guide
**Location:** `guides/simulated-accelerators/`

**Current GKE Usage:**
- Uses `httproute.gke.yaml` for GKE deployments (README.md line 62)
- Supports GKE as a gateway option

**How to Use Internal LB:**
```bash
# From guides/simulated-accelerators/
kubectl apply -k ../../recipes/gateway/gke-internal-lb-gateway/internal-lb -n ${NAMESPACE}
kubectl apply -f httproute.gke.yaml -n ${NAMESPACE}
```

### 6. Tiered Prefix Cache Guide
**Location:** `guides/tiered-prefix-cache/cpu/`

**Current GKE Usage:**
- Uses external GKE gateway recipe: `../../../../recipes/gateway/gke-l7-regional-external-managed` (README.md line 177)
- Shows example with `gke-l7-regional-external-managed` gateway class (README.md line 130)

**How to Use Internal LB:**
```bash
# From guides/tiered-prefix-cache/cpu/
kubectl apply -k ../../../../recipes/gateway/gke-internal-lb-gateway/internal-lb -n ${NAMESPACE}
```

## Documentation Updates Needed

### Gateway Recipes README
**Location:** `guides/recipes/gateway/README.md`

**Current State:**
- Lists GKE L7 Regional External Managed as a tab option
- Does not mention the internal LB option

**Recommendation:**
Add a new tab for "GKE L7 Regional Internal Managed" that references the `gke-internal-lb-gateway` recipe.

### Gateway Provider Prerequisites README
**Location:** `guides/prereq/gateway-provider/README.md`

**Current State:**
- Mentions both internal and external GKE load balancer options (line 49)
- References `gke-l7-rilb` class name (which may be outdated - the recipe uses `gke-l7-regional-internal-managed`)

**Recommendation:**
Update to reference the `gke-internal-lb-gateway` recipe and correct gateway class name.

## Summary

The `gke-internal-lb-gateway` recipe can be used in **6 guides** that currently support GKE:

1. ✅ **inference-scheduling** - Well-lit path guide
2. ✅ **pd-disaggregation** - Well-lit path guide  
3. ✅ **wide-ep-lws** - Well-lit path guide
4. ✅ **precise-prefix-cache-aware** - Feature guide
5. ✅ **simulated-accelerators** - Feature guide
6. ✅ **tiered-prefix-cache** - Feature guide

All of these guides either:
- Use `httproute.gke.yaml` (which works with the internal LB gateway)
- Have GKE-specific gateway configurations that can be replaced with the internal LB recipe

The recipe is designed to be a drop-in replacement for external GKE gateways when VPC-only access is required.

