#!/usr/bin/env bash
# OS/arch assumptions: macOS (darwin) or Linux, bash 4+, kubectl, curl.
# Wait for GKE LoadBalancer Services to receive an external IP and pass /health.

set -euo pipefail

if [[ -z "${_LOADBALANCER_LIB_LOADED:-}" ]]; then
  _LOADBALANCER_LIB_LOADED=1
  LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  # shellcheck source=common.sh
  source "${LIB_DIR}/common.sh"
fi

LOADBALANCER_READY_TIMEOUT_SEC="${LOADBALANCER_READY_TIMEOUT_SEC:-600}"
LOADBALANCER_POLL_INTERVAL_SEC="${LOADBALANCER_POLL_INTERVAL_SEC:-10}"
LOADBALANCER_HEALTH_TIMEOUT_SEC="${LOADBALANCER_HEALTH_TIMEOUT_SEC:-5}"

service_type() {
  local svc="$1"
  kubectl get svc "${svc}" -n "${NAMESPACE}" -o jsonpath='{.spec.type}' 2>/dev/null || true
}

service_external_ip() {
  local svc="$1"
  kubectl get svc "${svc}" -n "${NAMESPACE}" -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true
}

probe_service_health() {
  local host="$1"
  local health_url="${host%/}/health"
  curl -sf --max-time "${LOADBALANCER_HEALTH_TIMEOUT_SEC}" "${health_url}" >/dev/null 2>&1
}

wait_loadbalancer_service_ready() {
  local svc="$1"
  local gate_start_epoch now_epoch elapsed ip host
  gate_start_epoch="$(_iso_to_epoch "$(iso_now)")"

  while true; do
    ip="$(service_external_ip "${svc}")"
    if [[ -n "${ip}" && "${ip}" != "<pending>" ]]; then
      host="http://${ip}"
      if probe_service_health "${host}"; then
        echo "LOADBALANCER_READY service=${svc} ip=${ip} health=/health" >&2
        printf '%s' "${host}"
        return 0
      fi
      echo "LOADBALANCER_WAITING service=${svc} ip=${ip} health=pending" >&2
    else
      echo "LOADBALANCER_WAITING service=${svc} ip=<pending>" >&2
    fi

    now_epoch="$(_iso_to_epoch "$(iso_now)")"
    elapsed=$((now_epoch - gate_start_epoch))
    if (( elapsed >= LOADBALANCER_READY_TIMEOUT_SEC )); then
      die "LOADBALANCER_NOT_READY service=${svc} elapsed_sec=${elapsed} last_ip=${ip:-<empty>}"
    fi
    sleep "${LOADBALANCER_POLL_INTERVAL_SEC}"
  done
}

uses_loadbalancer_services() {
  local fixed_type hpa_type
  fixed_type="$(service_type hpa-eval-fixed-svc)"
  hpa_type="$(service_type hpa-eval-hpa-svc)"
  [[ "${fixed_type}" == "LoadBalancer" || "${hpa_type}" == "LoadBalancer" ]]
}

discover_hosts_kind() {
  FIXED_HOST="http://127.0.0.1:30080"
  HPA_HOST="http://127.0.0.1:30081"
}

ensure_loadbalancer_hosts_ready() {
  if ! uses_loadbalancer_services; then
    if [[ -z "${FIXED_HOST:-}" || -z "${HPA_HOST:-}" ]]; then
      discover_hosts_kind
    fi
    echo "LOADBALANCER_GATE_SKIPPED reason=NodePort fixed_host=${FIXED_HOST} hpa_host=${HPA_HOST}" >&2
    return 0
  fi

  echo "LOADBALANCER_GATE_BEGIN services=hpa-eval-fixed-svc,hpa-eval-hpa-svc timeout_sec=${LOADBALANCER_READY_TIMEOUT_SEC}" >&2
  FIXED_HOST="$(wait_loadbalancer_service_ready hpa-eval-fixed-svc)"
  HPA_HOST="$(wait_loadbalancer_service_ready hpa-eval-hpa-svc)"
  echo "LOADBALANCER_GATE_PASS fixed_host=${FIXED_HOST} hpa_host=${HPA_HOST}" >&2
}
