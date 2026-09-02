#!/usr/bin/env bash
# OS/arch assumptions: macOS (darwin) or Linux, bash 4+, kubectl, gcloud.
# Trap-based cleanup helpers.

set -euo pipefail

LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=common.sh
source "${LIB_DIR}/common.sh"
CLEANUP_PF_PIDS=()
CLEANUP_HEARTBEAT_PIDS=()

register_port_forward_pid() {
  CLEANUP_PF_PIDS+=("$1")
}

register_heartbeat_pid() {
  CLEANUP_HEARTBEAT_PIDS+=("$1")
}

cleanup_background_jobs() {
  local pid
  for pid in "${CLEANUP_HEARTBEAT_PIDS[@]:-}"; do
    kill "${pid}" 2>/dev/null || true
    wait "${pid}" 2>/dev/null || true
  done
  for pid in "${CLEANUP_PF_PIDS[@]:-}"; do
    kill "${pid}" 2>/dev/null || true
    wait "${pid}" 2>/dev/null || true
  done
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
