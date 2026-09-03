#!/usr/bin/env bash
# OS/arch assumptions: macOS (darwin) or Linux, bash 4+, gcloud.
# GKE-side preflight checks (project access, APIs, Artifact Registry).

set -euo pipefail

preflight_require_gke() {
  require_env PROJECT_ID
  require_env REGION
  require_env CLUSTER_NAME
  require_env ARTIFACT_REGISTRY_REPO

  printf '%s\n' "PROJECT_ID=${PROJECT_ID}"
  printf '%s\n' "REGION=${REGION}"
  printf '%s\n' "CLUSTER_NAME=${CLUSTER_NAME}"
  printf '%s\n' "ARTIFACT_REGISTRY_REPO=${ARTIFACT_REGISTRY_REPO}"

  if ! command -v gcloud >/dev/null 2>&1; then
    preflight_fail "missing required command: gcloud"
    return
  fi

  local active_account
  active_account="$(gcloud auth list --filter=status:ACTIVE --format='value(account)' 2>/dev/null | head -n 1 || true)"
  if [[ -z "${active_account}" ]]; then
    preflight_fail "no active gcloud account; run: gcloud auth login"
    return
  fi
  printf '%s\n' "GKE_ACTIVE_ACCOUNT=${active_account}"

  if ! gcloud projects describe "${PROJECT_ID}" --format='value(projectId)' >/dev/null 2>&1; then
    preflight_fail "GKE project access denied or PROJECT_ID invalid: ${PROJECT_ID}"
    return
  fi
  printf '%s\n' "GKE_PROJECT_ACCESS=PASS project=${PROJECT_ID}"

  local api
  for api in container.googleapis.com artifactregistry.googleapis.com; do
    if ! gcloud services list --enabled --project="${PROJECT_ID}" \
        --filter="config.name=${api}" --format='value(config.name)' 2>/dev/null | grep -qx "${api}"; then
      preflight_fail "required API not enabled on ${PROJECT_ID}: ${api}"
      return
    fi
    printf '%s\n' "GKE_API_ENABLED=${api}"
  done

  if ! gcloud artifacts repositories describe "${ARTIFACT_REGISTRY_REPO}" \
      --location="${REGION}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
    preflight_fail "Artifact Registry repo not found: ${ARTIFACT_REGISTRY_REPO} region=${REGION} project=${PROJECT_ID}"
    return
  fi
  printf '%s\n' "GKE_ARTIFACT_REGISTRY_REPO=PASS repo=${ARTIFACT_REGISTRY_REPO} region=${REGION}"

  printf '%s\n' "PROJECT_CLUSTER_VERIFICATION_REQUIRED"
}
