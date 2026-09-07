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

PROMETHEUS_PF_PORT="${PROMETHEUS_PF_PORT:-9090}"
PROMETHEUS_PF_URL="http://127.0.0.1:${PROMETHEUS_PF_PORT}"
PROMETHEUS_PF_ESTABLISH_ATTEMPTS="${PROMETHEUS_PF_ESTABLISH_ATTEMPTS:-3}"
PROMETHEUS_PF_READY_TIMEOUT_SEC="${PROMETHEUS_PF_READY_TIMEOUT_SEC:-30}"

prometheus_port_forward_probe() {
  curl -sf "${PROMETHEUS_PF_URL}/api/v1/status/buildinfo" >/dev/null 2>&1
}

stop_prometheus_port_forward() {
  local pid
  for pid in "${CLEANUP_PF_PIDS[@]:-}"; do
    kill "${pid}" 2>/dev/null || true
    wait "${pid}" 2>/dev/null || true
  done
  CLEANUP_PF_PIDS=()
}

ensure_prometheus_port_forward() {
  if prometheus_port_forward_probe; then
    return 0
  fi

  stop_prometheus_port_forward

  local attempt
  for attempt in $(seq 1 "${PROMETHEUS_PF_ESTABLISH_ATTEMPTS}"); do
    kubectl port-forward "svc/prometheus" "${PROMETHEUS_PF_PORT}:9090" \
      -n "${NAMESPACE}" >/dev/null 2>&1 &
    local pf_pid=$!
    register_port_forward_pid "${pf_pid}"

    local waited=0
    while (( waited < PROMETHEUS_PF_READY_TIMEOUT_SEC )); do
      if prometheus_port_forward_probe; then
        echo "PROMETHEUS_PORT_FORWARD_READY attempt=${attempt} port=${PROMETHEUS_PF_PORT}"
        return 0
      fi
      if ! kill -0 "${pf_pid}" 2>/dev/null; then
        break
      fi
      sleep 1
      waited=$((waited + 1))
    done

    kill "${pf_pid}" 2>/dev/null || true
    wait "${pf_pid}" 2>/dev/null || true
    CLEANUP_PF_PIDS=()
    echo "PROMETHEUS_PORT_FORWARD_RETRY attempt=${attempt} port=${PROMETHEUS_PF_PORT}" >&2
  done

  die "PROMETHEUS_PORT_FORWARD_FAILED port=${PROMETHEUS_PF_PORT} attempts=${PROMETHEUS_PF_ESTABLISH_ATTEMPTS}"
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

reset_prometheus_deployment() {
  # Wipes TSDB only when Prometheus storage is ephemeral (kind smoke).
  # A PVC mount preserves data across rollout restart — do not add pvc.yaml to
  # k8s/smoke/kustomization.yaml or this reset silently stops working.
  local current_context
  current_context="$(kubectl config current-context 2>/dev/null || true)"
  if [[ "${current_context}" == gke_* ]]; then
    if [[ "${ALLOW_PROMETHEUS_RESET_ON_GKE:-}" != "1" ]]; then
      die "PROMETHEUS_RESET_REFUSED_GKE_CONTEXT context=${current_context} set ALLOW_PROMETHEUS_RESET_ON_GKE=1 to override"
    fi
  fi
  kubectl rollout restart deployment/prometheus -n "${NAMESPACE}" >/dev/null
  kubectl rollout status deployment/prometheus -n "${NAMESPACE}" --timeout=120s
  echo "PROMETHEUS_TSDB_RESET namespace=${NAMESPACE}"
}

wait_prometheus_scrape_ready() {
  ensure_prometheus_port_forward
  local attempt
  for attempt in $(seq 1 36); do
    if curl -sf "${PROMETHEUS_PF_URL}/api/v1/query?query=up" 2>/dev/null | grep -q '"status":"success"'; then
      echo "PROMETHEUS_SCRAPE_READY attempt=${attempt} port=${PROMETHEUS_PF_PORT}"
      return 0
    fi
    sleep 5
  done
  die "prometheus API not ready within 180s after TSDB reset"
}
