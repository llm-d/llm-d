# Inference Cost Guide — Test Setup (OpenShift)

This guide sets up a fully isolated test stack in a throwaway namespace on an existing OpenShift
cluster. It reuses the cluster's existing vLLM workloads and KSM but keeps all generated `llm_*`
metrics isolated from the production OpenCost deployment.

## Prerequisites

- `helm`, `kubectl`, `oc`, and `jq` on your PATH
- All commands must be run from the **repo root** (the directory containing `guides/`)
- `prometheus-community` and `opencost-charts` Helm repos added:

```bash
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo add opencost-charts https://opencost.github.io/opencost-helm-chart
helm repo update
```

## Step 1 — Create the namespace

```bash
kubectl create namespace llm-d-cost-test
```

On OpenShift, `install-opencost.sh` automatically applies the SCC for the OpenCost pod
after Step 3. The Prometheus SCCs must be applied manually after Step 2 (see below).

## Step 2 — Install the test Prometheus

The production OpenCost in the `opencost` namespace already runs a node-exporter on port 9111
(bound to HOST_IP) on every node. The test node-exporter uses port 9113 on HOST_IP to avoid
the conflict.

```bash
helm install llm-d-test-prom prometheus-community/prometheus \
  --version 29.2.0 \
  --namespace llm-d-cost-test \
  -f guides/recipes/observability/inferencecost/values/prometheus-test.yaml
```

### OpenShift only — apply Prometheus SCCs

The `prometheus-server`, `kube-state-metrics`, and `node-exporter` components require
elevated privileges that the `restricted` PodSecurity policy blocks. Grant the SCCs and
restart the workloads before proceeding:

```bash
oc adm policy add-scc-to-user privileged \
  -z llm-d-test-prom-prometheus-server -n llm-d-cost-test
oc adm policy add-scc-to-user privileged \
  -z llm-d-test-prom-kube-state-metrics -n llm-d-cost-test
oc adm policy add-scc-to-user privileged \
  -z llm-d-test-prom-prometheus-node-exporter -n llm-d-cost-test

kubectl rollout restart \
  deployment/llm-d-test-prom-prometheus-server \
  deployment/llm-d-test-prom-kube-state-metrics \
  -n llm-d-cost-test
kubectl rollout restart \
  daemonset/llm-d-test-prom-prometheus-node-exporter \
  -n llm-d-cost-test
```

Wait until all three components are running before proceeding to Step 3:

```bash
kubectl get pods -n llm-d-cost-test -w
```

Expected — every pod `Running` before you continue:

```
llm-d-test-prom-prometheus-server-<hash>         2/2     Running   0
llm-d-test-prom-kube-state-metrics-<hash>        1/1     Running   0
llm-d-test-prom-prometheus-node-exporter-<hash>  1/1     Running   0  (one per node)
```

## Step 3 — Install the test OpenCost

```bash
./guides/recipes/observability/inferencecost/install-opencost.sh \
  --image "ghcr.io/opencost/opencost:develop-latest@sha256:e0c09b268d8243c45323fffec8ceea1434e9f7e982905af651b1adebfc7e3135" \
  --namespace llm-d-cost-test \
  --prometheus-endpoint http://llm-d-test-prom-prometheus-server.llm-d-cost-test.svc.cluster.local:80 \
  --prometheus-release-name llm-d-test-prom \
  --cluster-id llm-d-test \
  -y
```

After the script completes, check all pods reach Running:

```bash
kubectl get pods -n llm-d-cost-test
```

Expected: `llm-d-test-prom-prometheus-server` 2/2, `llm-d-test-prom-kube-state-metrics` 1/1,
`llm-d-test-prom-prometheus-node-exporter-*` 1/1 on each node, `opencost-llm-d-cost-test` 2/2.

> **OpenShift:** `install-opencost.sh` automatically grants `anyuid` to the OpenCost service
> account and restarts the OpenCost deployment at the end of Step 3. The `opencost-llm-d-cost-test`
> pod may briefly restart once — this is expected.

## Step 4 — Verify metrics and REST API

```bash
# Check inference cost metrics
kubectl port-forward -n llm-d-cost-test svc/opencost-llm-d-cost-test 9003:9003
curl -s http://localhost:9003/metrics | grep llm_

# Query the REST API
curl "http://localhost:9003/inferenceCost/total?window=1h"
curl "http://localhost:9003/inferenceCost/timeseries?window=24h&aggregate=model_name&accumulate=hour"
```

## Step 5 — Cleanup

```bash
# Uninstall Helm releases — this removes the Helm-managed ClusterRoles and
# ClusterRoleBindings (cluster-scoped resources that survive namespace deletion)
./guides/recipes/observability/inferencecost/install-opencost.sh \
  --uninstall -n llm-d-cost-test
helm uninstall llm-d-test-prom -n llm-d-cost-test

# Delete the namespace — removes all remaining namespaced resources including
# pods, services, configmaps, PVCs, and the SCC RoleBindings created by oc adm policy
kubectl delete namespace llm-d-cost-test
```

To verify nothing was left behind:

```bash
# Should return NotFound for both releases
helm list -n llm-d-cost-test 2>/dev/null

# Should return NotFound for all test ClusterRoles
kubectl get clusterrole,clusterrolebinding 2>/dev/null | \
  grep -E 'llm-d-cost-test|llm-d-test-prom'
```
