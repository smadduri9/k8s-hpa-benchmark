#!/usr/bin/env bash
# deploy_gke.sh — Deploy HPA evaluation app to Google Kubernetes Engine
# Usage: bash scripts/deploy_gke.sh [PROJECT_ID] [REGION]
#
# Prerequisites:
#   - gcloud CLI authenticated: gcloud auth login
#   - Project billing enabled
#   - APIs enabled: container.googleapis.com, containerregistry.googleapis.com

set -euo pipefail

PROJECT_ID="${1:-$(gcloud config get-value project 2>/dev/null)}"
REGION="${2:-us-central1}"
CLUSTER_NAME="hpa-eval-cluster"
NAMESPACE="hpa-eval"
IMAGE_NAME="gcr.io/${PROJECT_ID}/hpa-eval-app"
IMAGE_TAG="latest"

if [[ -z "${PROJECT_ID}" ]]; then
    echo "ERROR: PROJECT_ID not set. Pass as argument or run: gcloud config set project PROJECT_ID"
    exit 1
fi

echo "=== Kubernetes HPA Evaluation — GKE Deploy ==="
echo "  Project:  ${PROJECT_ID}"
echo "  Region:   ${REGION}"
echo "  Cluster:  ${CLUSTER_NAME}"
echo ""

# ---------------------------------------------------------------------------
# Step 1: Enable required APIs
# ---------------------------------------------------------------------------
echo "[1/7] Enabling required GCP APIs..."
gcloud services enable container.googleapis.com \
    containerregistry.googleapis.com \
    --project="${PROJECT_ID}"

# ---------------------------------------------------------------------------
# Step 2: Create GKE cluster
# ---------------------------------------------------------------------------
echo "[2/7] Creating GKE cluster (this takes ~3–5 minutes)..."
if gcloud container clusters describe "${CLUSTER_NAME}" \
    --region="${REGION}" --project="${PROJECT_ID}" &>/dev/null; then
    echo "  Cluster already exists, skipping creation."
else
    gcloud container clusters create "${CLUSTER_NAME}" \
        --region="${REGION}" \
        --project="${PROJECT_ID}" \
        --machine-type="e2-standard-2" \
        --num-nodes=3 \
        --enable-autoscaling \
        --min-nodes=2 \
        --max-nodes=6 \
        --enable-ip-alias \
        --release-channel=regular
fi

# Configure kubectl
gcloud container clusters get-credentials "${CLUSTER_NAME}" \
    --region="${REGION}" \
    --project="${PROJECT_ID}"

# ---------------------------------------------------------------------------
# Step 3: Build and push Docker image
# ---------------------------------------------------------------------------
echo "[3/7] Building and pushing Docker image to GCR..."
docker build -t "${IMAGE_NAME}:${IMAGE_TAG}" ./app/
docker push "${IMAGE_NAME}:${IMAGE_TAG}"

# ---------------------------------------------------------------------------
# Step 4: Apply Kubernetes manifests
# ---------------------------------------------------------------------------
echo "[4/7] Applying Kubernetes manifests..."
kubectl apply -f k8s/namespace.yaml

# Patch image references
for deploy_file in k8s/deployment-fixed.yaml k8s/deployment-hpa.yaml; do
    kubectl apply -f <(sed "s|gcr.io/PROJECT_ID/|gcr.io/${PROJECT_ID}/|g" "$deploy_file")
done

kubectl apply -f k8s/service.yaml
kubectl apply -f k8s/hpa.yaml
kubectl apply -f k8s/prometheus/configmap.yaml
kubectl apply -f k8s/prometheus/deployment.yaml
kubectl apply -f k8s/prometheus/service.yaml

# ---------------------------------------------------------------------------
# Step 5: Wait for deployments
# ---------------------------------------------------------------------------
echo "[5/7] Waiting for all pods to be ready..."
kubectl rollout status deployment/hpa-eval-fixed -n "${NAMESPACE}" --timeout=120s
kubectl rollout status deployment/hpa-eval-hpa   -n "${NAMESPACE}" --timeout=120s
kubectl rollout status deployment/prometheus      -n "${NAMESPACE}" --timeout=120s || true

# ---------------------------------------------------------------------------
# Step 6: Verify metrics-server (required for HPA)
# ---------------------------------------------------------------------------
echo "[6/7] Verifying metrics-server..."
kubectl wait --for=condition=Available deployment/metrics-server \
    -n kube-system --timeout=60s || \
    echo "  [WARN] metrics-server not ready — HPA requires it. Check: kubectl top nodes"

# ---------------------------------------------------------------------------
# Step 7: Print access info
# ---------------------------------------------------------------------------
echo "[7/7] Deployment complete!"
echo ""
echo "=== Waiting for external IPs (may take 1–2 minutes) ==="
kubectl get svc -n "${NAMESPACE}" --watch &
WATCH_PID=$!
sleep 30
kill $WATCH_PID 2>/dev/null || true

FIXED_IP=$(kubectl get svc hpa-eval-fixed-svc -n "${NAMESPACE}" \
    -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || echo "pending")
HPA_IP=$(kubectl get svc hpa-eval-hpa-svc -n "${NAMESPACE}" \
    -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || echo "pending")

echo ""
echo "  Fixed app: http://${FIXED_IP}"
echo "  HPA app:   http://${HPA_IP}"
echo ""
echo "  To access Prometheus:"
echo "    kubectl port-forward svc/prometheus 9090:9090 -n ${NAMESPACE}"
echo ""
echo "=== Run experiments ==="
echo "  bash scripts/run_experiment.sh"
echo ""
kubectl get pods -n "${NAMESPACE}"
