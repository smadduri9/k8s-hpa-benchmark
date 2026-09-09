#!/usr/bin/env bash
# OS/arch assumptions: macOS (darwin) or Linux, bash 4+, kubectl.
# Verify metrics-server responds via kubectl top (GKE addon; not always a Deployment).

set -euo pipefail

metrics_server_assert_ready() {
  local namespace="${1:-hpa-eval}"
  local output=""
  local rc=0

  set +e
  output="$(kubectl top pods -n "${namespace}" --no-headers 2>&1)"
  rc=$?
  set -e

  if [[ "${rc}" -ne 0 || -z "${output// }" ]]; then
    echo "ERROR: METRICS_SERVER_UNAVAILABLE namespace=${namespace} rc=${rc}" >&2
    if [[ -n "${output}" ]]; then
      echo "${output}" >&2
    fi
    return 1
  fi

  printf '%s\n' "METRICS_SERVER_AVAILABLE=PASS source=kubectl_top_pods namespace=${namespace}"
  return 0
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
