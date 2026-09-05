#!/usr/bin/env bash
# OS/arch assumptions: macOS (darwin) or Linux, bash 4+, gcloud.
# GKE-side preflight checks (project access, APIs, Artifact Registry, regional quotas).

set -euo pipefail

_PREFLIGHT_GKE_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=gke_shape.sh
source "${_PREFLIGHT_GKE_LIB_DIR}/gke_shape.sh"

preflight_check_gke_quotas() {
  local quota_py="${_PREFLIGHT_GKE_LIB_DIR}/preflight_gke_quota.py"
  local region_json=""
  region_json="$(mktemp "${TMPDIR:-/tmp}/gke-quota.XXXXXX")"

  if ! gcloud compute regions describe "${REGION}" \
      --project="${PROJECT_ID}" \
      --format=json >"${region_json}" 2>/dev/null; then
    rm -f "${region_json}"
    preflight_fail "failed to query regional quotas for region=${REGION} project=${PROJECT_ID}"
    return
  fi

  printf '%s\n' "GKE_SHAPE num_nodes=${GKE_NUM_NODES} disk_gb=${NODE_DISK_SIZE_GB} machine=${GKE_MACHINE_TYPE} cpus_per_node=${GKE_CPUS_PER_NODE}"

  local audit_python="${VENV_PYTHON:-}"
  if [[ -z "${audit_python}" || ! -x "${audit_python}" ]]; then
    audit_python="$(command -v python3 || true)"
  fi
  if [[ -z "${audit_python}" ]]; then
    rm -f "${region_json}"
    preflight_fail "missing python3 for GKE quota audit"
    return
  fi

  set +e
  local quota_rc=0
  "${audit_python}" "${quota_py}" \
    "${region_json}" \
    "${GKE_NUM_NODES}" \
    "${NODE_DISK_SIZE_GB}" \
    "${GKE_CPUS_PER_NODE}"
  quota_rc=$?
  set -e
  rm -f "${region_json}"

  if [[ "${quota_rc}" -ne 0 ]]; then
    PREFLIGHT_FAILED=true
  fi
}

preflight_check_artifact_registry_docker_auth() {
  local registry_host helper
  registry_host="$(artifact_registry_host "${REGION}")"
  local docker_config="${HOME}/.docker/config.json"

  if [[ ! -f "${docker_config}" ]]; then
    preflight_fail "ARTIFACT_REGISTRY_DOCKER_AUTH_MISSING registry=${registry_host} reason=no_docker_config"
    return
  fi

  local audit_python="${VENV_PYTHON:-}"
  if [[ -z "${audit_python}" || ! -x "${audit_python}" ]]; then
    audit_python="$(command -v python3 || true)"
  fi
  if [[ -z "${audit_python}" ]]; then
    preflight_fail "missing python3 for docker credential helper audit"
    return
  fi

  helper="$("${audit_python}" - "${registry_host}" "${docker_config}" <<'PY'
import json
import sys

registry_host, docker_config = sys.argv[1], sys.argv[2]
with open(docker_config, encoding="utf-8") as handle:
    config = json.load(handle)
helpers = config.get("credHelpers", {})
helper = helpers.get(registry_host, "")
print(helper)
PY
)"

  if [[ -z "${helper}" ]]; then
    preflight_fail "ARTIFACT_REGISTRY_DOCKER_AUTH_MISSING registry=${registry_host} reason=no_cred_helper remediation=gcloud auth configure-docker ${registry_host}"
    return
  fi
  printf '%s\n' "GKE_DOCKER_CREDENTIAL_HELPER=PASS registry=${registry_host} helper=${helper}"

  if ! gcloud artifacts docker images list \
      "${registry_host}/${PROJECT_ID}/${ARTIFACT_REGISTRY_REPO}" \
      --project="${PROJECT_ID}" \
      --limit=1 >/dev/null 2>&1; then
    preflight_fail "ARTIFACT_REGISTRY_REPO_UNREACHABLE repo=${ARTIFACT_REGISTRY_REPO} host=${registry_host} project=${PROJECT_ID}"
    return
  fi
  printf '%s\n' "GKE_ARTIFACT_REGISTRY_REACHABLE=PASS repo=${ARTIFACT_REGISTRY_REPO} host=${registry_host}"
}

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

  preflight_check_artifact_registry_docker_auth
  preflight_check_gke_quotas

  printf '%s\n' "PROJECT_CLUSTER_VERIFICATION_REQUIRED"
}
