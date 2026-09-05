#!/usr/bin/env bash
# OS/arch assumptions: macOS (darwin) or Linux, bash 4+, kubectl.
# Cold-start helpers: scale to zero, poll until pods gone, restore declared replicas, verify ready.

set -euo pipefail

# Poll interval while waiting for pods to terminate (seconds). Not a readiness sleep.
COLD_START_PODS_POLL_INTERVAL="${COLD_START_PODS_POLL_INTERVAL:-2}"
COLD_START_PODS_ZERO_TIMEOUT_SEC="${COLD_START_PODS_ZERO_TIMEOUT_SEC:-300}"
COLD_START_READINESS_TIMEOUT_SEC="${COLD_START_READINESS_TIMEOUT_SEC:-180}"

deployment_declared_replicas() {
  local deployment="$1"
  local namespace="${2:-hpa-eval}"
  kubectl get deployment "${deployment}" -n "${namespace}" -o jsonpath='{.spec.replicas}'
}

wait_pods_zero() {
  local selector="$1"
  local namespace="${2:-hpa-eval}"
  local timeout_sec="${3:-${COLD_START_PODS_ZERO_TIMEOUT_SEC}}"
  local attempt=0
  local count
  local deadline=$((SECONDS + timeout_sec))
  while true; do
    attempt=$((attempt + 1))
    count="$(kubectl get pods -n "${namespace}" -l "${selector}" --no-headers 2>/dev/null | wc -l | tr -d ' ')"
    echo "PODS_POLL attempt=${attempt} interval=${COLD_START_PODS_POLL_INTERVAL}s remaining=${count}"
    if [[ "${count}" == "0" ]]; then
      echo "PODS_AT_ZERO_CONFIRMED selector=${selector}"
      return 0
    fi
    if (( SECONDS >= deadline )); then
      echo "ERROR: PODS_ZERO_TIMEOUT selector=${selector} timeout_sec=${timeout_sec} remaining=${count}" >&2
      return 1
    fi
    sleep "${COLD_START_PODS_POLL_INTERVAL}"
  done
}

manifest_set_load_start_t0() {
  local manifest_path="$1"
  local arm="$2"
  local t0="$3"
  "${VENV_PYTHON}" - "${manifest_path}" "${arm}" "${t0}" <<'PY'
import json
import sys

path, arm, t0 = sys.argv[1:4]
with open(path, encoding="utf-8") as handle:
    data = json.load(handle)
arms = data.setdefault("arms", {})
arms.setdefault(arm, {})
arms[arm]["load_start_t0"] = t0
with open(path, "w", encoding="utf-8") as handle:
    json.dump(data, handle, indent=2)
    handle.write("\n")
PY
}

cold_start_arm() {
  local deployment="$1"
  local selector="$2"
  local namespace="${3:-hpa-eval}"
  local manifest_path="${4:-}"
  local arm_label="${5:-fixed}"
  local readiness_timeout="${6:-${COLD_START_READINESS_TIMEOUT_SEC}}"
  local emit_load_start="${7:-false}"

  local declared
  declared="$(deployment_declared_replicas "${deployment}" "${namespace}")"
  if [[ -z "${declared}" ]]; then
    echo "ERROR: COLD_START_DECLARED_REPLICAS_MISSING deployment=${deployment}" >&2
    return 1
  fi

  echo "SCALE_TO_ZERO_ISSUED deployment=${deployment} namespace=${namespace} previous_declared=${declared}"
  kubectl scale deployment "${deployment}" --replicas=0 -n "${namespace}"

  wait_pods_zero "${selector}" "${namespace}"

  echo "DEPLOY_SCALE_ISSUED deployment=${deployment} declared_replicas=${declared}"
  kubectl scale deployment "${deployment}" --replicas="${declared}" -n "${namespace}"

  if ! kubectl rollout status "deployment/${deployment}" -n "${namespace}" --timeout="${readiness_timeout}s"; then
    echo "ERROR: COLD_START_READINESS_TIMEOUT deployment=${deployment} declared=${declared} timeout_sec=${readiness_timeout}" >&2
    return 1
  fi

  local ready
  ready="$(kubectl get deployment "${deployment}" -n "${namespace}" -o jsonpath='{.status.readyReplicas}')"
  if [[ "${ready}" != "${declared}" ]]; then
    echo "ERROR: READY_REPLICAS_MATCH_DECLARED failed deployment=${deployment} declared=${declared} ready=${ready:-0}" >&2
    return 1
  fi
  echo "READY_REPLICAS_MATCH_DECLARED deployment=${deployment} declared=${declared} ready=${ready}"

  if [[ "${emit_load_start}" == "true" ]]; then
    local t0
    t0="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
    echo "LOAD_START t0=${t0}"
    if [[ -n "${manifest_path}" && -f "${manifest_path}" ]]; then
      manifest_set_load_start_t0 "${manifest_path}" "${arm_label}" "${t0}"
      echo "MANIFEST_T0_WRITTEN arm=${arm_label} path=${manifest_path}"
    fi
    echo "${t0}"
  fi
}
