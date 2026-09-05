#!/usr/bin/env bash
# OS/arch assumptions: macOS (darwin) or Linux, bash 4+, gcloud, docker, kubectl.
# deploy_gke.sh — Deploy HPA evaluation app to Google Kubernetes Engine (zonal cluster).
# Usage: bash scripts/deploy_gke.sh [--env-file .env]
#
# Prerequisites:
#   - gcloud CLI authenticated: gcloud auth login
#   - Project billing enabled
#   - APIs enabled: container.googleapis.com, artifactregistry.googleapis.com
#   - Docker credential helper for ${REGION}-docker.pkg.dev (gcloud auth configure-docker)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
# shellcheck source=lib/gke_shape.sh
source "${SCRIPT_DIR}/lib/gke_shape.sh"

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
require_env ARTIFACT_REGISTRY_REPO

NAMESPACE="${NAMESPACE:-hpa-eval}"
MACHINE_TYPE="${GKE_MACHINE_TYPE}"
NUM_NODES="${GKE_NUM_NODES}"
NODE_DISK_SIZE_GB="${NODE_DISK_SIZE_GB}"

if ! git -C "${REPO_ROOT}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  die "deploy requires a git repository to derive immutable IMAGE_TAG"
fi
IMAGE_TAG="$(git -C "${REPO_ROOT}" rev-parse --short HEAD)"
IMAGE_URI="$(artifact_registry_image_uri "${REGION}" "${PROJECT_ID}" "${ARTIFACT_REGISTRY_REPO}" "${IMAGE_TAG}")"
REGISTRY_HOST="$(artifact_registry_host "${REGION}")"
DEPLOY_MANIFEST_PATH="${REPO_ROOT}/results/gke-deploy-manifest.json"

echo "=== Kubernetes HPA Evaluation — GKE Deploy ==="
echo "  Project:      ${PROJECT_ID}"
echo "  Region:       ${REGION} (Artifact Registry)"
echo "  Zone:         ${ZONE} (GKE cluster)"
echo "  Cluster:      ${CLUSTER_NAME}"
echo "  Machine type: ${MACHINE_TYPE}"
echo "  Nodes:        ${NUM_NODES} (fixed, single zone, no cluster autoscaler)"
echo "  Boot disk:    ${NODE_DISK_SIZE_GB}GB balanced PD per node (${NUM_NODES}×${NODE_DISK_SIZE_GB}=$(( NUM_NODES * NODE_DISK_SIZE_GB ))GB SSD_TOTAL_GB)"
echo "  Image:        ${IMAGE_URI}"
echo "  IMAGE_TAG:    ${IMAGE_TAG}"
echo ""

# ---------------------------------------------------------------------------
# Step 1: Enable required APIs
# ---------------------------------------------------------------------------
echo "[1/7] Enabling required GCP APIs..."
gcloud services enable container.googleapis.com \
    artifactregistry.googleapis.com \
    --project="${PROJECT_ID}"

# ---------------------------------------------------------------------------
# Step 2: Create GKE cluster (zonal — one node pool in ZONE, not triplicated)
# ---------------------------------------------------------------------------
echo "[2/7] Ensuring GKE cluster exists..."
if gcloud container clusters describe "${CLUSTER_NAME}" \
    --zone="${ZONE}" --project="${PROJECT_ID}" &>/dev/null; then
    echo "  CLUSTER_EXISTS zone=${ZONE} name=${CLUSTER_NAME} skipping creation"
else
    echo "  Creating GKE cluster (this takes ~3–5 minutes)..."
    gcloud container clusters create "${CLUSTER_NAME}" \
        --zone="${ZONE}" \
        --project="${PROJECT_ID}" \
        --machine-type="${MACHINE_TYPE}" \
        --num-nodes="${NUM_NODES}" \
        --disk-size="${NODE_DISK_SIZE_GB}" \
        --enable-ip-alias \
        --release-channel=regular
fi

# Configure kubectl
gcloud container clusters get-credentials "${CLUSTER_NAME}" \
    --zone="${ZONE}" \
    --project="${PROJECT_ID}"

# ---------------------------------------------------------------------------
# Step 3: Build and push Docker image to Artifact Registry
# ---------------------------------------------------------------------------
echo "[3/7] Building and pushing Docker image to Artifact Registry (${REGISTRY_HOST})..."
gcloud auth configure-docker "${REGISTRY_HOST}" --quiet
# GKE nodes are linux/amd64; explicit platform avoids arm64 host building wrong arch.
docker build --platform linux/amd64 -t "${IMAGE_URI}" "${REPO_ROOT}/app/"
docker push "${IMAGE_URI}"

mkdir -p "${REPO_ROOT}/results"
cat > "${DEPLOY_MANIFEST_PATH}" <<EOF
{
  "deployed_at": "$(date -u '+%Y-%m-%dT%H:%M:%SZ')",
  "project_id": "${PROJECT_ID}",
  "region": "${REGION}",
  "zone": "${ZONE}",
  "cluster_name": "${CLUSTER_NAME}",
  "artifact_registry_repo": "${ARTIFACT_REGISTRY_REPO}",
  "image_uri": "${IMAGE_URI}",
  "image_tag": "${IMAGE_TAG}",
  "git_sha_short": "${IMAGE_TAG}"
}
EOF
echo "DEPLOY_MANIFEST_WRITTEN path=${DEPLOY_MANIFEST_PATH} IMAGE_TAG=${IMAGE_TAG}"

# ---------------------------------------------------------------------------
# Step 4: Apply Kubernetes manifests
# ---------------------------------------------------------------------------
echo "[4/7] Applying Kubernetes manifests..."
kubectl apply -f "${REPO_ROOT}/k8s/namespace.yaml"

for deploy_file in "${REPO_ROOT}/k8s/deployment-fixed.yaml" "${REPO_ROOT}/k8s/deployment-hpa.yaml"; do
    kubectl apply -f <(apply_deployment_image_substitution "${deploy_file}" "${IMAGE_URI}")
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
echo "  IMAGE_TAG: ${IMAGE_TAG}"
echo ""
echo "  To access Prometheus:"
echo "    kubectl port-forward svc/prometheus 9090:9090 -n ${NAMESPACE}"
echo ""
echo "=== Run experiments ==="
echo "  bash scripts/run_benchmark.sh --env-file .env"
echo ""
kubectl get pods -n "${NAMESPACE}"
