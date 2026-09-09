#!/usr/bin/env bash
# OS/arch assumptions: macOS (darwin) or Linux, bash 4+, kubectl.
# Verify metrics-server responds via kubectl top (GKE addon; not always a Deployment).

set -euo pipefail

metrics_server_assert_top_pods() {
  local namespace="${1:-hpa-eval}"
  local selector="${2:-}"
  local min_pods="${3:-1}"
  local output=""
  local rc=0
  local pod_count=0
  local -a kubectl_args=(top pods -n "${namespace}" --no-headers)

  if [[ -n "${selector}" ]]; then
    kubectl_args+=(-l "${selector}")
  fi

  set +e
  output="$(kubectl "${kubectl_args[@]}" 2>&1)"
  rc=$?
  set -e

  if [[ "${rc}" -ne 0 || -z "${output// }" ]]; then
    echo "ERROR: METRICS_SERVER_UNAVAILABLE namespace=${namespace} selector=${selector:-<none>} rc=${rc}" >&2
    if [[ -n "${output}" ]]; then
      echo "${output}" >&2
    fi
    return 1
  fi

  pod_count="$(printf '%s\n' "${output}" | awk 'NF { count++ } END { print count+0 }')"
  if (( pod_count < min_pods )); then
    echo "ERROR: METRICS_SERVER_SELECTOR_EMPTY namespace=${namespace} selector=${selector} pods=${pod_count} required=${min_pods}" >&2
    return 1
  fi

  printf '%s\n' "METRICS_SERVER_AVAILABLE=PASS source=kubectl_top_pods namespace=${namespace} selector=${selector:-<none>} pods=${pod_count}"
  return 0
}

metrics_server_assert_ready() {
  metrics_server_assert_top_pods "${1:-hpa-eval}" "" 1
}

metrics_server_assert_nodes() {
  local output=""
  local rc=0

  set +e
  output="$(kubectl top nodes --no-headers 2>&1)"
  rc=$?
  set -e

  if [[ "${rc}" -ne 0 || -z "${output// }" ]]; then
    echo "ERROR: METRICS_SERVER_UNAVAILABLE source=kubectl_top_nodes rc=${rc}" >&2
    if [[ -n "${output}" ]]; then
      echo "${output}" >&2
    fi
    return 1
  fi

  printf '%s\n' "METRICS_SERVER_AVAILABLE=PASS source=kubectl_top_nodes"
  return 0
}
