#!/usr/bin/env bash
# OS/arch assumptions: macOS (darwin) or Linux, bash 4+, kubectl.
# Cold-start helpers: scale to zero, poll until pods gone, restore declared replicas, verify ready.

set -euo pipefail

# Poll interval while waiting for pods to terminate (seconds). Not a readiness sleep.
COLD_START_PODS_POLL_INTERVAL="${COLD_START_PODS_POLL_INTERVAL:-2}"
COLD_START_PODS_ZERO_TIMEOUT_SEC="${COLD_START_PODS_ZERO_TIMEOUT_SEC:-300}"
COLD_START_READINESS_TIMEOUT_SEC="${COLD_START_READINESS_TIMEOUT_SEC:-180}"
# Arm-side backstop: collector must not outlive t0 + run_time + this grace.
# Internal collector timeout is run_time; this bound exists so a hung collector
# cannot block Locust-complete arms (the ramp hang waited on wait $collector_pid).
COLD_START_COLLECTOR_GRACE_SEC="${COLD_START_COLLECTOR_GRACE_SEC:-60}"

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

_mark_cold_start_incomplete() {
  local dir="$1"
  local reason="$2"
  mkdir -p "${dir}"
  printf '%s\n' "${reason}" >> "${dir}/COLD_START_INCOMPLETE"
  echo "COLD_START_INCOMPLETE reason=${reason}"
}

_kill_pid_and_children() {
  local pid="$1"
  local child children waited
  children="$(pgrep -P "${pid}" 2>/dev/null || true)"
  kill "${pid}" 2>/dev/null || true
  for child in ${children}; do
    kill "${child}" 2>/dev/null || true
  done
  waited=0
  while (( waited < 2 )) && kill -0 "${pid}" 2>/dev/null; do
    sleep 1
    waited=$((waited + 1))
  done
  children="${children} $(pgrep -P "${pid}" 2>/dev/null || true)"
  kill -9 "${pid}" 2>/dev/null || true
  for child in ${children}; do
    [[ -n "${child}" ]] || continue
    kill -9 "${child}" 2>/dev/null || true
  done
}

# Wait for the cold-start collector until t0 + run_secs + grace, then kill it
# and proceed. Locust artifacts stay authoritative; missing/truncated jsonl is
# recorded, never a reason to hold the arm.
# Optional: log_file and jsonl_file override the arm defaults under dir/.
wait_cold_start_collector() {
  local pid="$1"
  local t0_iso="$2"
  local run_secs="$3"
  local dir="$4"
  local log_file="${5:-${dir}/collector.log}"
  local jsonl_file="${6:-${dir}/cold_start_events.jsonl}"
  local grace_sec="${COLD_START_COLLECTOR_GRACE_SEC:-60}"
  local t0_epoch deadline now_epoch
  t0_epoch="$(_iso_to_epoch "${t0_iso}")"
  deadline=$((t0_epoch + run_secs + grace_sec))
  echo "COLD_START_COLLECTOR_WAIT pid=${pid} t0=${t0_iso} run_time_sec=${run_secs} grace_sec=${grace_sec} deadline_epoch=${deadline}"

  while kill -0 "${pid}" 2>/dev/null; do
    now_epoch="$(_iso_to_epoch "$(iso_now)")"
    if (( now_epoch >= deadline )); then
      echo "ERROR: COLD_START_COLLECTOR_TIMEOUT pid=${pid} t0=${t0_iso} run_time_sec=${run_secs} grace_sec=${grace_sec}" >&2
      _kill_pid_and_children "${pid}"
      wait "${pid}" 2>/dev/null || true
      _mark_cold_start_incomplete "${dir}" \
        "COLD_START_COLLECTOR_TIMEOUT t0=${t0_iso} run_time_sec=${run_secs} grace_sec=${grace_sec}"
      break
    fi
    sleep 1
  done
  wait "${pid}" 2>/dev/null || true

  if [[ -f "${log_file}" ]] && grep -q "COLD_START_WATCHES_DIED" "${log_file}" 2>/dev/null; then
    _mark_cold_start_incomplete "${dir}" "COLD_START_WATCHES_DIED"
  fi
  if [[ ! -f "${jsonl_file}" ]]; then
    _mark_cold_start_incomplete "${dir}" "COLD_START_EVENTS_JSONL_MISSING"
  fi
}
