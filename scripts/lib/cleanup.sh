#!/usr/bin/env bash
# OS/arch assumptions: macOS (darwin) or Linux, bash 4+, kubectl, gcloud.
# Trap-based cleanup helpers.

set -euo pipefail

if [[ -z "${_CLEANUP_LIB_LOADED:-}" ]]; then
  _CLEANUP_LIB_LOADED=1
  LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  # shellcheck source=common.sh
  source "${LIB_DIR}/common.sh"
  CLEANUP_PF_PIDS=()
  CLEANUP_HEARTBEAT_PIDS=()
  CLEANUP_LOCUST_PIDS=()
fi

register_port_forward_pid() {
  CLEANUP_PF_PIDS+=("$1")
}

register_heartbeat_pid() {
  CLEANUP_HEARTBEAT_PIDS+=("$1")
}

register_locust_pid() {
  CLEANUP_LOCUST_PIDS+=("$1")
}

cleanup_background_jobs() {
  local pid
  for pid in "${CLEANUP_LOCUST_PIDS[@]:-}"; do
    kill "${pid}" 2>/dev/null || true
    wait "${pid}" 2>/dev/null || true
  done
  for pid in "${CLEANUP_HEARTBEAT_PIDS[@]:-}"; do
    kill "${pid}" 2>/dev/null || true
    wait "${pid}" 2>/dev/null || true
  done
  for pid in "${CLEANUP_PF_PIDS[@]:-}"; do
    kill "${pid}" 2>/dev/null || true
    wait "${pid}" 2>/dev/null || true
  done
  CLEANUP_HEARTBEAT_PIDS=()
  CLEANUP_PF_PIDS=()
  CLEANUP_LOCUST_PIDS=()
}

destructive_gke_teardown() {
  local expected_project="$1"
  local expected_cluster="$2"
  local expected_region="${3:-}"
  verify_cluster_target "${expected_project}" "${expected_cluster}" "${expected_region}"
  log "DESTRUCTIVE_GKE_TEARDOWN_AUTHORIZED project=${PROJECT_ID} cluster=${CLUSTER_NAME}"
}

verify_cluster_target() {
  local expected_project="$1"
  local expected_cluster="$2"
  local expected_region="${3:-}"

  require_env PROJECT_ID
  require_env CLUSTER_NAME

  if [[ "${PROJECT_ID}" != "${expected_project}" ]]; then
    die "project mismatch: expected ${expected_project}, got ${PROJECT_ID}"
  fi
  if [[ "${CLUSTER_NAME}" != "${expected_cluster}" ]]; then
    die "cluster mismatch: expected ${expected_cluster}, got ${CLUSTER_NAME}"
  fi

  local current_cluster
  current_cluster="$(kubectl config current-context 2>/dev/null || true)"
  if [[ -z "${current_cluster}" ]]; then
    die "kubectl has no current context"
  fi

  log "PROJECT_CLUSTER_VERIFICATION_REQUIRED"
  log "Verified target project=${PROJECT_ID} cluster=${CLUSTER_NAME} region=${expected_region:-N/A} context=${current_cluster}"
}
