#!/usr/bin/env bash
# OS/arch assumptions: macOS (darwin) or Linux, bash 4+, docker, kind, kubectl.
# Calibrate the watch-based cold-start collector on kind with a known init sleep of 8s.
# Usage: bash scripts/calibrate_cold_start_collector.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
require_venv

KIND_CLUSTER="${KIND_CLUSTER_NAME:-hpa-eval-smoke}"
CAL_NS="hpa-eval-calibrate"
IMAGE_NAME="hpa-eval-app:smoke"
OUT_DIR="${REPO_ROOT}/results/cold-start-calibration"
CACHED_JSONL="${OUT_DIR}/cached.jsonl"
UNCACHED_JSONL="${OUT_DIR}/uncached.jsonl"
TIMEOUT_SEC=180
EXPECTED_SLEEP=8
FLOOR_SEC=1

die_named() {
  echo "ERROR: $*" >&2
  exit 1
}

kind_node() {
  echo "${KIND_CLUSTER}-control-plane"
}

ensure_kind() {
  if ! kind get clusters 2>/dev/null | grep -qx "${KIND_CLUSTER}"; then
    kind create cluster --name "${KIND_CLUSTER}" --config "${REPO_ROOT}/kind/kind-config.yaml"
  fi
  kubectl config use-context "kind-${KIND_CLUSTER}" >/dev/null
}

load_images() {
  docker build -t "${IMAGE_NAME}" "${REPO_ROOT}/app"
  kind load docker-image "${IMAGE_NAME}" --name "${KIND_CLUSTER}"
  # kind load of multi-arch busybox fails on Docker attestations; pull on-node instead.
  local node
  for node in "$(kind_node)" "${KIND_CLUSTER}-worker"; do
    docker exec "${node}" crictl pull docker.io/library/busybox:1.36
  done
}

reset_namespace() {
  kubectl delete namespace "${CAL_NS}" --ignore-not-found --wait=true --timeout=60s || true
  kubectl apply -f "${REPO_ROOT}/k8s/smoke/calibrate-cold-start.yaml"
  # Uncached pod is applied too; delete it for the cached run so it does not steal the pull story.
  kubectl delete pod cold-start-uncached -n "${CAL_NS}" --ignore-not-found --wait=true --timeout=30s || true
  kubectl scale deployment/cold-start-calibrate -n "${CAL_NS}" --replicas=1
  kubectl rollout status deployment/cold-start-calibrate -n "${CAL_NS}" --timeout=120s
}

start_collector() {
  local selector="$1"
  local output="$2"
  local expect="$3"
  local init_name="$4"
  local container="$5"
  mkdir -p "${OUT_DIR}"
  "${VENV_PYTHON}" "${REPO_ROOT}/scripts/lib/cold_start_events.py" \
    --namespace "${CAL_NS}" \
    --selector "${selector}" \
    --output "${output}" \
    --timeout-sec "${TIMEOUT_SEC}" \
    --expect-pods "${expect}" \
    --init-name "${init_name}" \
    --container-name "${container}" \
    --first-request-timeout-sec 30 &
  COLLECTOR_PID=$!
  local waited=0
  while (( waited < 30 )); do
    if grep -q "COLD_START_WATCH_READY" "/dev/null" 2>/dev/null; then
      :
    fi
    if ! kill -0 "${COLLECTOR_PID}" 2>/dev/null; then
      die_named "COLD_START_COLLECTOR_EXITED pid=${COLLECTOR_PID}"
    fi
    # Watch-ready is printed to collector stdout; wait a fixed attach window.
    sleep 1
    waited=$((waited + 1))
    if (( waited >= 3 )); then
      return 0
    fi
  done
}

# Collector prints to stdout; capture in a log while waiting for WATCH_READY.
start_collector_logged() {
  local selector="$1"
  local output="$2"
  local expect="$3"
  local init_name="$4"
  local container="$5"
  local log_file="$6"
  mkdir -p "${OUT_DIR}"
  : > "${log_file}"
  "${VENV_PYTHON}" "${REPO_ROOT}/scripts/lib/cold_start_events.py" \
    --namespace "${CAL_NS}" \
    --selector "${selector}" \
    --output "${output}" \
    --timeout-sec "${TIMEOUT_SEC}" \
    --expect-pods "${expect}" \
    --init-name "${init_name}" \
    --container-name "${container}" \
    --first-request-timeout-sec 30 >"${log_file}" 2>&1 &
  COLLECTOR_PID=$!
  local waited=0
  while (( waited < 30 )); do
    if grep -q "COLD_START_WATCH_READY" "${log_file}" 2>/dev/null; then
      echo "COLLECTOR_ATTACHED pid=${COLLECTOR_PID}"
      return 0
    fi
    if ! kill -0 "${COLLECTOR_PID}" 2>/dev/null; then
      cat "${log_file}" >&2
      die_named "COLD_START_COLLECTOR_EXITED pid=${COLLECTOR_PID}"
    fi
    sleep 1
    waited=$((waited + 1))
  done
  cat "${log_file}" >&2
  die_named "COLD_START_WATCH_READY_TIMEOUT timeout_sec=30"
}

curl_new_pod() {
  local selector="$1"
  local baseline="$2"
  local pod=""
  local waited=0
  local candidate
  while (( waited < TIMEOUT_SEC )); do
    pod=""
    while IFS= read -r candidate; do
      [[ -z "${candidate}" ]] && continue
      if [[ " ${baseline} " == *" ${candidate} "* ]]; then
        continue
      fi
      pod="${candidate}"
      break
    done < <(kubectl get pods -n "${CAL_NS}" -l "${selector}" --field-selector=status.phase=Running \
      -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null || true)
    if [[ -n "${pod}" ]]; then
      if kubectl exec -n "${CAL_NS}" "${pod}" -c hpa-eval-app -- \
        python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/')" >/dev/null 2>&1; then
        echo "FIRST_REQUEST_PROBE_OK pod=${pod}"
        return 0
      fi
    fi
    sleep 1
    waited=$((waited + 1))
  done
  echo "FIRST_REQUEST_PROBE_TIMEOUT selector=${selector}" >&2
  return 0
}

evaluate_cached() {
  local row observed
  if [[ ! -s "${CACHED_JSONL}" ]]; then
    die_named "CALIBRATION_NO_ROWS path=${CACHED_JSONL}"
  fi
  echo "=== cached JSONL row ==="
  cat "${CACHED_JSONL}"
  row="$(head -1 "${CACHED_JSONL}")"
  observed="$("${VENV_PYTHON}" - "${row}" "${EXPECTED_SLEEP}" "${FLOOR_SEC}" <<'PY'
import json
import sys

row = json.loads(sys.argv[1])
expected = int(sys.argv[2])
floor = int(sys.argv[3])
raw = row.get("init_sleep_observed_sec")
print(f"init_sleep_observed_sec={raw}")
if raw in (None, "MISSING", ""):
    print("CALIBRATION_FAILED reason=init_sleep_MISSING")
    sys.exit(1)
observed = int(raw)
delta = abs(observed - expected)
print(f"CALIBRATION_RECOVERED expected_sec={expected} observed_sec={observed} abs_err_sec={delta} floor_sec={floor}")
if delta > floor:
    print("CALIBRATION_FAILED reason=outside_1s_floor")
    sys.exit(1)
PY
)"
  printf '%s\n' "${observed}"
}

run_uncached() {
  local log_file="${OUT_DIR}/uncached-collector.log"
  kubectl delete pod cold-start-uncached -n "${CAL_NS}" --ignore-not-found --wait=true --timeout=30s || true
  local node
  node="$(kind_node)"
  # Remove nginx from the kind node if present so the next create must pull.
  docker exec "${node}" crictl rmi docker.io/library/nginx:1.27-alpine >/dev/null 2>&1 || true
  docker exec "${node}" crictl rmi nginx:1.27-alpine >/dev/null 2>&1 || true
  start_collector_logged "app=cold-start-uncached" "${UNCACHED_JSONL}" 1 "" "pulled" "${log_file}"
  kubectl apply -f "${REPO_ROOT}/k8s/smoke/calibrate-cold-start.yaml"
  # Only the uncached pod is selected; deployment pods have a different label.
  wait "${COLLECTOR_PID}" || true
  echo "=== uncached collector log ==="
  cat "${log_file}"
  echo "=== uncached JSONL row ==="
  if [[ -s "${UNCACHED_JSONL}" ]]; then
    cat "${UNCACHED_JSONL}"
  else
    die_named "CALIBRATION_UNCACHED_NO_ROWS path=${UNCACHED_JSONL}"
  fi
  "${VENV_PYTHON}" - "${UNCACHED_JSONL}" <<'PY'
import json
import sys

path = sys.argv[1]
row = json.loads(open(path, encoding="utf-8").readline())
cached = row.get("image_cached")
print(f"image_cached={cached}")
if cached != "false":
    print(f"CALIBRATION_UNCACHED_FAIL expected image_cached=false observed={cached}")
    sys.exit(1)
for key, value in row.items():
    if value == "":
        print(f"CALIBRATION_UNCACHED_FAIL empty_field={key} (use MISSING)")
        sys.exit(1)
print("CALIBRATION_UNCACHED_OK image_cached=false")
PY
}

main() {
  mkdir -p "${OUT_DIR}"
  ensure_kind
  load_images
  reset_namespace
  local baseline
  baseline="$(kubectl get pods -n "${CAL_NS}" -l app=cold-start-calibrate -o jsonpath='{range .items[*]}{.metadata.name} {end}')"
  echo "BASELINE_PODS ${baseline}"
  start_collector_logged "app=cold-start-calibrate" "${CACHED_JSONL}" 1 "delay" "hpa-eval-app" "${OUT_DIR}/cached-collector.log"
  kubectl scale deployment/cold-start-calibrate -n "${CAL_NS}" --replicas=2
  curl_new_pod "app=cold-start-calibrate" "${baseline}"
  wait "${COLLECTOR_PID}" || true
  echo "=== cached collector log ==="
  cat "${OUT_DIR}/cached-collector.log"
  evaluate_cached
  run_uncached
  echo "CALIBRATION_COMPLETE"
}

main "$@"
