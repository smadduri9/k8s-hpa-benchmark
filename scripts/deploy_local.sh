#!/usr/bin/env bash
# deploy_local.sh — Deploy HPA evaluation app to local Minikube cluster
# Usage: bash scripts/deploy_local.sh

set -euo pipefail

NAMESPACE="hpa-eval"
IMAGE_NAME="hpa-eval-app"
IMAGE_TAG="latest"

echo "=== Kubernetes HPA Evaluation — Local Minikube Deploy ==="
echo ""

# ---------------------------------------------------------------------------
# Step 1: Start Minikube
# ---------------------------------------------------------------------------
echo "[1/6] Starting Minikube..."
if minikube status | grep -q "Running"; then
    echo "  Minikube already running."
else
    minikube start --cpus=4 --memory=4096 --driver=docker
fi

# ---------------------------------------------------------------------------
# Step 2: Enable required addons
# ---------------------------------------------------------------------------
echo "[2/6] Enabling metrics-server addon (required for HPA)..."
minikube addons enable metrics-server

echo "  Waiting for metrics-server to be ready..."
kubectl wait --for=condition=Available deployment/metrics-server \
    -n kube-system --timeout=120s || true

# ---------------------------------------------------------------------------
# Step 3: Build Docker image inside Minikube's Docker daemon
# ---------------------------------------------------------------------------
echo "[3/6] Building Docker image inside Minikube..."
eval "$(minikube docker-env)"
docker build -t "${IMAGE_NAME}:${IMAGE_TAG}" ./app/

# ---------------------------------------------------------------------------
# Step 4: Apply namespace and manifests
# ---------------------------------------------------------------------------
echo "[4/6] Applying Kubernetes manifests..."
kubectl apply -f k8s/namespace.yaml

# Patch deployments to use local image (no registry pull)
# Use sed to replace image reference for local build
for deploy_file in k8s/deployment-fixed.yaml k8s/deployment-hpa.yaml; do
    kubectl apply -f <(sed 's|gcr.io/PROJECT_ID/hpa-eval-app:latest|hpa-eval-app:latest|g' "$deploy_file")
done

# Patch services to use NodePort instead of LoadBalancer
kubectl apply -f <(sed 's|LoadBalancer|NodePort|g' k8s/service.yaml)

# Apply HPA
kubectl apply -f k8s/hpa.yaml

# Apply Prometheus
kubectl apply -f k8s/prometheus/configmap.yaml
kubectl apply -f k8s/prometheus/deployment.yaml
kubectl apply -f k8s/prometheus/service.yaml

# ---------------------------------------------------------------------------
# Step 5: Wait for pods to be ready
# ---------------------------------------------------------------------------
echo "[5/6] Waiting for pods to be ready..."
kubectl wait --for=condition=Ready pod \
    -l app=hpa-eval -n "${NAMESPACE}" --timeout=120s

kubectl wait --for=condition=Ready pod \
    -l app=prometheus -n "${NAMESPACE}" --timeout=120s || true

# ---------------------------------------------------------------------------
# Step 6: Print access info
# ---------------------------------------------------------------------------
echo "[6/6] Deployment complete!"
echo ""
echo "=== Access Information ==="
FIXED_PORT=$(kubectl get svc hpa-eval-fixed-svc -n "${NAMESPACE}" \
    -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null || echo "N/A")
HPA_PORT=$(kubectl get svc hpa-eval-hpa-svc -n "${NAMESPACE}" \
    -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null || echo "N/A")
MINIKUBE_IP=$(minikube ip)

echo "  Fixed app:    http://${MINIKUBE_IP}:${FIXED_PORT}"
echo "  HPA app:      http://${MINIKUBE_IP}:${HPA_PORT}"
echo ""
echo "  To access Prometheus:"
echo "    kubectl port-forward svc/prometheus 9090:9090 -n ${NAMESPACE}"
echo ""
echo "=== Run Load Test ==="
echo "  locust -f locust/locustfile.py --host http://${MINIKUBE_IP}:${HPA_PORT} --headless --run-time 18m --users 200 --spawn-rate 10"
echo ""
echo "=== Watch HPA scaling ==="
echo "  kubectl get hpa -n ${NAMESPACE} -w"
echo ""
kubectl get pods -n "${NAMESPACE}"
