#!/usr/bin/env bash
# OS/arch assumptions: macOS (darwin) or Linux, bash 4+.
# Shared GKE cluster shape constants for deploy_gke.sh and preflight quota checks.

# Fixed node count — no cluster autoscaler (see deploy_gke.sh arithmetic).
GKE_NUM_NODES="${GKE_NUM_NODES:-3}"

# Balanced PD boot disks (GKE 1.24+ default) count against SSD_TOTAL_GB, not DISKS_TOTAL_GB.
# hpa-benchmark-2026 us-central1 SSD_TOTAL_GB limit is 250; 3×50=150 leaves headroom.
NODE_DISK_SIZE_GB="${NODE_DISK_SIZE_GB:-50}"

GKE_MACHINE_TYPE="${GKE_MACHINE_TYPE:-e2-standard-2}"
# e2-standard-2 vCPU count (used for CPUS quota: NUM_NODES * GKE_CPUS_PER_NODE).
GKE_CPUS_PER_NODE=2

# Deployment image placeholders substituted by deploy_gke.sh / deploy_local.sh.
IMAGE_PLACEHOLDER_REGISTRY="PLACEHOLDER_REGISTRY"
IMAGE_PLACEHOLDER_TAG="PLACEHOLDER_TAG"
IMAGE_APP_NAME="hpa-eval-app"

artifact_registry_host() {
  printf '%s-docker.pkg.dev' "$1"
}

artifact_registry_image_uri() {
  local region="$1"
  local project="$2"
  local repo="$3"
  local tag="$4"
  printf '%s/%s/%s/%s:%s' "$(artifact_registry_host "${region}")" "${project}" "${repo}" "${IMAGE_APP_NAME}" "${tag}"
}

manifest_image_placeholder() {
  printf '%s/%s:%s' "${IMAGE_PLACEHOLDER_REGISTRY}" "${IMAGE_APP_NAME}" "${IMAGE_PLACEHOLDER_TAG}"
}

apply_deployment_image_substitution() {
  local deploy_file="$1"
  local image_uri="$2"
  sed -e "s|${IMAGE_PLACEHOLDER_REGISTRY}/${IMAGE_APP_NAME}:${IMAGE_PLACEHOLDER_TAG}|${image_uri}|g" "${deploy_file}"
}
