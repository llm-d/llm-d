#!/bin/bash

# DigitalOcean DOKS Monitoring Setup for llm-d
# Deploys Prometheus, Grafana, and GPU monitoring stack

set -e

NAMESPACE="llm-d-monitoring"
RELEASE_NAME="llm-d-monitoring"

echo "🔧 Setting up monitoring stack for DigitalOcean DOKS..."

# Create namespace
kubectl create namespace $NAMESPACE --dry-run=client -o yaml | kubectl apply -f -

# Add Prometheus community Helm repo
helm repo add prometheus-community https://prometheus-community.github.io/helm-charts
helm repo update

echo "📊 Installing Prometheus & Grafana..."

# Install kube-prometheus-stack with DigitalOcean optimizations
helm upgrade --install $RELEASE_NAME prometheus-community/kube-prometheus-stack \
  --namespace $NAMESPACE \
  --set prometheus.prometheusSpec.serviceMonitorSelectorNilUsesHelmValues=false \
  --set prometheus.prometheusSpec.podMonitorSelectorNilUsesHelmValues=false \
  --set prometheus.prometheusSpec.storageSpec.volumeClaimTemplate.spec.accessModes[0]=ReadWriteOnce \
  --set prometheus.prometheusSpec.storageSpec.volumeClaimTemplate.spec.resources.requests.storage=20Gi \
  --set prometheus.prometheusSpec.storageSpec.volumeClaimTemplate.spec.storageClassName=do-block-storage \
  --set grafana.persistence.enabled=true \
  --set grafana.persistence.size=10Gi \
  --set grafana.persistence.storageClassName=do-block-storage \
  --set grafana.adminPassword=admin \
  --set alertmanager.alertmanagerSpec.storage.volumeClaimTemplate.spec.accessModes[0]=ReadWriteOnce \
  --set alertmanager.alertmanagerSpec.storage.volumeClaimTemplate.spec.resources.requests.storage=10Gi \
  --set alertmanager.alertmanagerSpec.storage.volumeClaimTemplate.spec.storageClassName=do-block-storage

echo "🎯 Installing NVIDIA DCGM Exporter for GPU metrics..."

# Install NVIDIA DCGM Exporter
kubectl apply -f - <<EOF
apiVersion: apps/v1
kind: DaemonSet
metadata:
  name: nvidia-dcgm-exporter
  namespace: $NAMESPACE
  labels:
    app.kubernetes.io/name: nvidia-dcgm-exporter
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: nvidia-dcgm-exporter
  template:
    metadata:
      labels:
        app.kubernetes.io/name: nvidia-dcgm-exporter
    spec:
      tolerations:
      - key: nvidia.com/gpu
        operator: Exists
        effect: NoSchedule
      nodeSelector:
        nvidia.com/gpu.present: "true"
      containers:
      - name: nvidia-dcgm-exporter
        image: nvcr.io/nvidia/k8s/dcgm-exporter:3.3.0-3.2.0-ubuntu22.04
        ports:
        - name: metrics
          containerPort: 9400
        securityContext:
          privileged: true
        volumeMounts:
        - name: proc
          mountPath: /host/proc
          readOnly: true
        - name: sys
          mountPath: /host/sys
          readOnly: true
      volumes:
      - name: proc
        hostPath:
          path: /proc
      - name: sys
        hostPath:
          path: /sys
      hostNetwork: true
      hostPID: true
---
apiVersion: v1
kind: Service
metadata:
  name: nvidia-dcgm-exporter
  namespace: $NAMESPACE
  labels:
    app.kubernetes.io/name: nvidia-dcgm-exporter
spec:
  ports:
  - name: metrics
    port: 9400
    targetPort: 9400
  selector:
    app.kubernetes.io/name: nvidia-dcgm-exporter
---
apiVersion: monitoring.coreos.com/v1
kind: ServiceMonitor
metadata:
  name: nvidia-dcgm-exporter
  namespace: $NAMESPACE
  labels:
    app.kubernetes.io/name: nvidia-dcgm-exporter
spec:
  selector:
    matchLabels:
      app.kubernetes.io/name: nvidia-dcgm-exporter
  endpoints:
  - port: metrics
    interval: 30s
    path: /metrics
EOF

echo "📈 Creating llm-d specific Grafana dashboard..."

# Create ConfigMap with llm-d dashboard
kubectl apply -f - <<EOF
apiVersion: v1
kind: ConfigMap
metadata:
  name: llm-d-dashboard
  namespace: $NAMESPACE
  labels:
    grafana_dashboard: "1"
data:
  llm-d-dashboard.json: |
    {
      "dashboard": {
        "id": null,
        "title": "llm-d DigitalOcean DOKS Dashboard",
        "description": "Monitoring dashboard for llm-d on DigitalOcean Kubernetes Service",
        "panels": [
          {
            "title": "GPU Utilization",
            "type": "stat",
            "targets": [
              {
                "expr": "DCGM_FI_DEV_GPU_UTIL",
                "refId": "A"
              }
            ]
          },
          {
            "title": "GPU Memory Usage",
            "type": "stat",
            "targets": [
              {
                "expr": "DCGM_FI_DEV_MEM_COPY_UTIL",
                "refId": "A"
              }
            ]
          },
          {
            "title": "Inference Request Rate",
            "type": "graph",
            "targets": [
              {
                "expr": "rate(http_requests_total{job=~\".*llm-d.*\"}[5m])",
                "refId": "A"
              }
            ]
          }
        ]
      }
    }
EOF

echo ""
echo "✅ Monitoring stack installed successfully!"
echo ""
echo "🔍 Access Information:"
echo "  Grafana: kubectl port-forward -n $NAMESPACE svc/llm-d-monitoring-grafana 3000:80"
echo "  Prometheus: kubectl port-forward -n $NAMESPACE svc/llm-d-monitoring-kube-prom-prometheus 9090:9090"
echo "  Username: admin"
echo "  Password: admin"
echo ""
echo "📊 Available Metrics:"
echo "  - GPU utilization (DCGM_FI_DEV_GPU_UTIL)"
echo "  - GPU memory usage (DCGM_FI_DEV_FB_USED, DCGM_FI_DEV_FB_TOTAL)"
echo "  - GPU temperature (DCGM_FI_DEV_GPU_TEMP)"
echo "  - Kubernetes resources (CPU, memory, network)"
echo "  - llm-d specific metrics (if exposed)"
echo ""
echo "🔧 Customize dashboards in Grafana UI or add more ServiceMonitors for your workloads."