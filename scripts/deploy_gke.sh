#!/usr/bin/env bash
# OS/arch assumptions: macOS (darwin) or Linux, bash 4+, gcloud, docker, kubectl.
# deploy_gke.sh — Deploy HPA evaluation app to Google Kubernetes Engine (zonal cluster).
# Usage: bash scripts/deploy_gke.sh [--env-file .env]
#
# Prerequisites:
#   - gcloud CLI authenticated: gcloud auth login
#   - Project billing enabled
#   - APIs enabled: container.googleapis.com, containerregistry.googleapis.com

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

ENV_FILE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file) ENV_FILE="$2"; shift 2 ;;
    -h|--help)
      echo "Usage: bash scripts/deploy_gke.sh [--env-file .env]"
      exit 0
      ;;
    *)
      die "unknown argument: $1 (use --env-file .env; do not pass PROJECT_ID positionally)"
      ;;
  esac
done

load_env_file "${ENV_FILE}"
require_env PROJECT_ID
require_env REGION
require_env ZONE
require_env CLUSTER_NAME

NAMESPACE="${NAMESPACE:-hpa-eval}"
IMAGE_NAME="gcr.io/${PROJECT_ID}/hpa-eval-app"
IMAGE_TAG="latest"
MACHINE_TYPE="${GKE_MACHINE_TYPE:-e2-standard-2}"
# Fixed node count — no cluster autoscaler (CA adds/removes nodes reactively and
# contaminates HPA scaling latency measurements and arm-to-arm node baselines).
# Sizing: e2-standard-2 allocatable ~1930m CPU / ~6172Mi per node after GKE
# kube+system reserve; ~250m CPU / ~400Mi per node for daemonsets.
# Peak concurrent requests: HPA maxReplicas=10×100m + fixed 3×100m + prom 100m = 1400m.
# Require N×(1930-250) ≥ 1400 + N×250 → N ≥ 1.97 → minimum 2; use 3 for ~57% CPU headroom.
NUM_NODES="${GKE_NUM_NODES:-3}"

echo "=== Kubernetes HPA Evaluation — GKE Deploy ==="
echo "  Project:      ${PROJECT_ID}"
echo "  Region:       ${REGION} (Artifact Registry)"
echo "  Zone:         ${ZONE} (GKE cluster)"
echo "  Cluster:      ${CLUSTER_NAME}"
echo "  Machine type: ${MACHINE_TYPE}"
echo "  Nodes:        ${NUM_NODES} (fixed, single zone, no cluster autoscaler)"
echo ""

# ---------------------------------------------------------------------------
# Step 1: Enable required APIs
# ---------------------------------------------------------------------------
echo "[1/7] Enabling required GCP APIs..."
gcloud services enable container.googleapis.com \
    containerregistry.googleapis.com \
    --project="${PROJECT_ID}"

# ---------------------------------------------------------------------------
# Step 2: Create GKE cluster (zonal — one node pool in ZONE, not triplicated)
# ---------------------------------------------------------------------------
echo "[2/7] Creating GKE cluster (this takes ~3–5 minutes)..."
if gcloud container clusters describe "${CLUSTER_NAME}" \
    --zone="${ZONE}" --project="${PROJECT_ID}" &>/dev/null; then
    echo "  Cluster already exists, skipping creation."
else
    gcloud container clusters create "${CLUSTER_NAME}" \
        --zone="${ZONE}" \
        --project="${PROJECT_ID}" \
        --machine-type="${MACHINE_TYPE}" \
        --num-nodes="${NUM_NODES}" \
        --enable-ip-alias \
        --release-channel=regular
fi

# Configure kubectl
gcloud container clusters get-credentials "${CLUSTER_NAME}" \
    --zone="${ZONE}" \
    --project="${PROJECT_ID}"

# ---------------------------------------------------------------------------
# Step 3: Build and push Docker image
# ---------------------------------------------------------------------------
echo "[3/7] Building and pushing Docker image to GCR..."
# GKE nodes are linux/amd64; explicit platform avoids arm64 host building wrong arch.
docker build --platform linux/amd64 -t "${IMAGE_NAME}:${IMAGE_TAG}" "${REPO_ROOT}/app/"
docker push "${IMAGE_NAME}:${IMAGE_TAG}"

# ---------------------------------------------------------------------------
# Step 4: Apply Kubernetes manifests
# ---------------------------------------------------------------------------
echo "[4/7] Applying Kubernetes manifests..."
kubectl apply -f "${REPO_ROOT}/k8s/namespace.yaml"

# Patch image references
for deploy_file in "${REPO_ROOT}/k8s/deployment-fixed.yaml" "${REPO_ROOT}/k8s/deployment-hpa.yaml"; do
    kubectl apply -f <(sed "s|gcr.io/PROJECT_ID/|gcr.io/${PROJECT_ID}/|g" "${deploy_file}")
done

kubectl apply -f "${REPO_ROOT}/k8s/service.yaml"
kubectl apply -f "${REPO_ROOT}/k8s/hpa.yaml"
kubectl apply -f "${REPO_ROOT}/k8s/prometheus/configmap.yaml"
kubectl apply -f "${REPO_ROOT}/k8s/prometheus/deployment.yaml"
kubectl apply -f "${REPO_ROOT}/k8s/prometheus/service.yaml"

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
