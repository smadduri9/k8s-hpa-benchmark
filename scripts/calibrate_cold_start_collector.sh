#!/usr/bin/env bash
# OS/arch assumptions: macOS (darwin) or Linux, bash 4+, docker, kind, kubectl.
# Calibrate the watch-based cold-start collector on kind:
#   HPA SuccessfulRescale + known init sleep 8s + FIRST_REQUEST_SERVED.
# Usage: bash scripts/calibrate_cold_start_collector.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
# shellcheck source=lib/cold_start.sh
source "${SCRIPT_DIR}/lib/cold_start.sh"
require_venv

KIND_CLUSTER="${KIND_CLUSTER_NAME:-hpa-eval-smoke}"
CAL_NS="hpa-eval-calibrate"
IMAGE_NAME="hpa-eval-app:smoke"
REGISTRY_NAME="cal-registry"
REGISTRY_HOST="cal-registry:5000"
CAL_IMAGE="${REGISTRY_HOST}/hpa-eval-app:calibrate"
OUT_DIR="${REPO_ROOT}/docs/cold-start-calibration"
CACHED_JSONL="${OUT_DIR}/cached.jsonl"
UNCACHED_JSONL="${OUT_DIR}/uncached.jsonl"
TIMEOUT_SEC=300
EXPECTED_SLEEP=8
FLOOR_SEC=1
LOAD_PID=""
PF_PID=""
COLLECTOR_PID=""
COLLECTOR_T0=""

die_named() {
  echo "ERROR: $*" >&2
  exit 1
}

cleanup_bg() {
  if [[ -n "${LOAD_PID}" ]] && kill -0 "${LOAD_PID}" 2>/dev/null; then
    kill "${LOAD_PID}" 2>/dev/null || true
    wait "${LOAD_PID}" 2>/dev/null || true
  fi
  if [[ -n "${PF_PID}" ]] && kill -0 "${PF_PID}" 2>/dev/null; then
    kill "${PF_PID}" 2>/dev/null || true
    wait "${PF_PID}" 2>/dev/null || true
  fi
}
trap cleanup_bg EXIT

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
  docker build --provenance=false --sbom=false -t "${IMAGE_NAME}" "${REPO_ROOT}/app"
  local node
  for node in "$(kind_node)" "${KIND_CLUSTER}-worker"; do
    docker exec "${node}" crictl pull docker.io/library/busybox:1.36
  done
}

pull_cal_image_on_nodes() {
  local node
  for node in "$(kind_node)" "${KIND_CLUSTER}-worker"; do
    docker exec "${node}" crictl pull "${CAL_IMAGE}"
  done
  echo "CAL_IMAGE_CACHED_ON_NODES image=${CAL_IMAGE}"
}

install_metrics_server() {
  if kubectl get deployment metrics-server -n kube-system >/dev/null 2>&1 \
    && kubectl get apiservice v1beta1.metrics.k8s.io -o jsonpath='{.status.conditions[?(@.type=="Available")].status}' 2>/dev/null | grep -q True; then
    echo "METRICS_SERVER_READY"
    return 0
  fi
  kubectl delete deployment metrics-server -n kube-system --ignore-not-found=true
  local waited=0
  while kubectl get pods -n kube-system -l k8s-app=metrics-server --no-headers 2>/dev/null | grep -q .; do
    if (( waited >= 60 )); then
      die_named "METRICS_SERVER_DELETE_TIMEOUT"
    fi
    sleep 2
    waited=$((waited + 2))
  done
  kubectl apply -f "${REPO_ROOT}/k8s/smoke/metrics-server.yaml"
  kubectl rollout status deployment/metrics-server -n kube-system --timeout=180s
  echo "METRICS_SERVER_READY"
}

reset_namespace() {
  kubectl delete namespace "${CAL_NS}" --ignore-not-found --wait=true --timeout=120s || true
  kubectl apply -f "${REPO_ROOT}/k8s/smoke/calibrate-cold-start.yaml"
  kubectl rollout status deployment/cold-start-calibrate -n "${CAL_NS}" --timeout=180s
}

wait_hpa_metric() {
  local line=""
  local attempt
  for attempt in $(seq 1 24); do
    line="$(kubectl get hpa cold-start-calibrate -n "${CAL_NS}" --no-headers 2>/dev/null || true)"
    if [[ -n "${line}" ]] && ! echo "${line}" | grep -q '<unknown>'; then
      echo "HPA_UTILIZATION_PRESENT ${line}"
      return 0
    fi
    sleep 10
  done
  die_named "HPA_METRIC_UNKNOWN after 240s: ${line}"
}

start_collector_logged() {
  local selector="$1"
  local output="$2"
  local log_file="$3"
  mkdir -p "${OUT_DIR}"
  : > "${log_file}"
  "${VENV_PYTHON}" "${REPO_ROOT}/scripts/lib/cold_start_events.py" \
    --namespace "${CAL_NS}" \
    --selector "${selector}" \
    --output "${output}" \
    --timeout-sec "${TIMEOUT_SEC}" \
    --expect-pods 1 \
    --init-name "delay" \
    --container-name "hpa-eval-app" \
    --first-request-timeout-sec 45 >"${log_file}" 2>&1 &
  COLLECTOR_PID=$!
  COLLECTOR_T0="$(iso_now)"
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

start_cpu_load() {
  kubectl port-forward -n "${CAL_NS}" svc/cold-start-calibrate 18099:80 >/dev/null 2>&1 &
  PF_PID=$!
  local waited=0
  while (( waited < 30 )); do
    if curl -sf --max-time 2 "http://127.0.0.1:18099/health" >/dev/null 2>&1; then
      break
    fi
    sleep 1
    waited=$((waited + 1))
  done
  if (( waited >= 30 )); then
    die_named "CALIBRATE_PORTFORWARD_TIMEOUT"
  fi
  "${VENV_LOCUST}" \
    -f "${REPO_ROOT}/locust/locustfile_warmup.py" \
    --host "http://127.0.0.1:18099" \
    --headless \
    --users 12 \
    --spawn-rate 12 \
    --run-time 4m \
    --csv "${OUT_DIR}/calibrate-load" \
    --csv-full-history \
    --exit-code-on-error 0 \
    --logfile "${OUT_DIR}/calibrate-load.log" >>"${OUT_DIR}/calibrate-load.log" 2>&1 &
  LOAD_PID=$!
  echo "CALIBRATE_LOAD_STARTED pid=${LOAD_PID}"
}

stop_cpu_load() {
  cleanup_bg
  LOAD_PID=""
  PF_PID=""
}

curl_new_pod() {
  local selector="$1"
  local baseline="$2"
  local waited=0
  local candidate
  local any_ok=0
  while (( waited < TIMEOUT_SEC )); do
    while IFS= read -r candidate; do
      [[ -z "${candidate}" ]] && continue
      if [[ " ${baseline} " == *" ${candidate} "* ]]; then
        continue
      fi
      if kubectl exec -n "${CAL_NS}" "${candidate}" -c hpa-eval-app -- \
        python3 -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/')" >/dev/null 2>&1; then
        echo "FIRST_REQUEST_PROBE_OK pod=${candidate}"
        any_ok=1
      fi
    done < <(kubectl get pods -n "${CAL_NS}" -l "${selector}" --field-selector=status.phase=Running \
      -o jsonpath='{range .items[*]}{.metadata.name}{"\n"}{end}' 2>/dev/null || true)
    if (( any_ok == 1 )); then
      return 0
    fi
    sleep 1
    waited=$((waited + 1))
  done
  echo "FIRST_REQUEST_PROBE_TIMEOUT selector=${selector}" >&2
  return 0
}

evaluate_row() {
  local jsonl="$1"
  local want_cached="$2"
  local label="$3"
  if [[ ! -s "${jsonl}" ]]; then
    die_named "CALIBRATION_NO_ROWS path=${jsonl}"
  fi
  echo "=== ${label} JSONL row ==="
  cat "${jsonl}"
  "${VENV_PYTHON}" - "${jsonl}" "${EXPECTED_SLEEP}" "${FLOOR_SEC}" "${want_cached}" <<'PY'
import json
import sys
from datetime import datetime, timezone

path, expected, floor, want_cached = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), sys.argv[4]
row = json.loads(open(path, encoding="utf-8").readline())

def parse(ts):
    if not ts or ts == "MISSING":
        return None
    text = ts[:-1] + "+00:00" if ts.endswith("Z") else ts
    return datetime.fromisoformat(text)

decision = row.get("hpa_decision")
source = row.get("hpa_decision_source")
print(f"hpa_decision={decision}")
print(f"hpa_decision_source={source}")
if decision in (None, "MISSING", "") or source != "SuccessfulRescale":
    print("CALIBRATION_FAILED reason=hpa_decision_not_SuccessfulRescale")
    sys.exit(1)

created = parse(row.get("pod_created"))
dec = parse(decision)
if dec is None or created is None:
    print("HPA_SYNC_LAG_SEC=MISSING")
else:
    lag = (created - dec).total_seconds()
    if lag < 0:
        print(f"HPA_SYNC_LAG_SEC={lag} note=pod_created_before_event")
    elif lag < 1:
        print("HPA_SYNC_LAG_SEC=<1s")
    else:
        print(f"HPA_SYNC_LAG_SEC={int(lag)}")

raw = row.get("init_sleep_observed_sec")
print(f"init_sleep_observed_sec={raw}")
if raw in (None, "MISSING", ""):
    print("CALIBRATION_FAILED reason=init_sleep_MISSING")
    sys.exit(1)
observed = int(raw)
delta = abs(observed - expected)
print(f"CALIBRATION_RECOVERED expected_sec={expected} observed_sec={observed} abs_err_sec={delta} floor_sec={floor} hpa_decision_present=true")
if delta > floor:
    print("CALIBRATION_FAILED reason=outside_1s_floor")
    sys.exit(1)

frs = row.get("first_request_served")
print(f"first_request_served={frs}")
if frs in (None, "MISSING", ""):
    print("CALIBRATION_FAILED reason=first_request_served_MISSING")
    sys.exit(1)

cached = row.get("image_cached")
print(f"image_cached={cached}")
if cached != want_cached:
    print(f"CALIBRATION_FAILED reason=image_cached expected={want_cached} observed={cached}")
    sys.exit(1)
print("CALIBRATION_ROW_OK")
PY
}

patch_hpa_cpu_target() {
  local util="$1"
  kubectl patch hpa cold-start-calibrate -n "${CAL_NS}" --type merge -p "$(cat <<EOF
{"spec":{"metrics":[{"type":"Resource","resource":{"name":"cpu","target":{"type":"Utilization","averageUtilization":${util}}}}]}}
EOF
)"
}

wait_desired_replicas() {
  local want="$1"
  local waited=0
  while (( waited < 180 )); do
    local desired
    desired="$(kubectl get hpa cold-start-calibrate -n "${CAL_NS}" -o jsonpath='{.status.desiredReplicas}' 2>/dev/null || echo "")"
    if [[ "${desired}" == "${want}" ]]; then
      echo "HPA_DESIRED_REPLICAS=${desired}"
      return 0
    fi
    sleep 2
    waited=$((waited + 2))
  done
  die_named "HPA_SCALE_DOWN_TIMEOUT want=${want}"
}

ensure_registry() {
  docker rm -f "${REGISTRY_NAME}" >/dev/null 2>&1 || true
  docker run -d --name "${REGISTRY_NAME}" --network kind -p 5001:5000 registry:2
  docker tag "${IMAGE_NAME}" "localhost:5001/hpa-eval-app:calibrate"
  docker push "localhost:5001/hpa-eval-app:calibrate"
  local node
  for node in "$(kind_node)" "${KIND_CLUSTER}-worker"; do
    docker exec "${node}" mkdir -p "/etc/containerd/certs.d/${REGISTRY_HOST}"
    docker exec -i "${node}" tee "/etc/containerd/certs.d/${REGISTRY_HOST}/hosts.toml" >/dev/null <<EOF
server = "http://${REGISTRY_HOST}"

[host."http://${REGISTRY_HOST}"]
  capabilities = ["pull", "resolve"]
  skip_verify = true
EOF
  done
  echo "CAL_REGISTRY_READY host=${REGISTRY_HOST}"
}

rmi_on_node() {
  local node="$1"
  shift
  local img
  for img in "$@"; do
    docker exec "${node}" crictl rmi "${img}" >/dev/null 2>&1 || true
  done
}

run_cached_hpa() {
  local baseline
  baseline="$(kubectl get pods -n "${CAL_NS}" -l app=cold-start-calibrate -o jsonpath='{range .items[*]}{.metadata.name} {end}')"
  echo "BASELINE_PODS ${baseline}"
  start_collector_logged "app=cold-start-calibrate" "${CACHED_JSONL}" "${OUT_DIR}/cached-collector.log"
  start_cpu_load
  curl_new_pod "app=cold-start-calibrate" "${baseline}"
  wait_cold_start_collector \
    "${COLLECTOR_PID}" \
    "${COLLECTOR_T0}" \
    "${TIMEOUT_SEC}" \
    "${OUT_DIR}" \
    "${OUT_DIR}/cached-collector.log" \
    "${CACHED_JSONL}"
  echo "=== cached collector log ==="
  cat "${OUT_DIR}/cached-collector.log"
  evaluate_row "${CACHED_JSONL}" "true" "cached"
}

run_uncached_hpa() {
  stop_cpu_load
  # Idle CPU on 50m requests sat above the 10% target, so HPA never returned to min.
  # Raise the target only to drain; restore 10% before the uncached scale-out we measure.
  patch_hpa_cpu_target 80
  wait_desired_replicas 1
  kubectl wait --for=jsonpath='{.status.readyReplicas}'=1 deployment/cold-start-calibrate -n "${CAL_NS}" --timeout=120s
  ensure_registry
  kubectl patch deployment/cold-start-calibrate -n "${CAL_NS}" --type=json -p='[
    {"op":"replace","path":"/spec/template/spec/containers/0/imagePullPolicy","value":"Always"}
  ]'
  kubectl rollout status deployment/cold-start-calibrate -n "${CAL_NS}" --timeout=180s
  local waited=0
  local nrunning=""
  while (( waited < 60 )); do
    nrunning="$(kubectl get pods -n "${CAL_NS}" -l app=cold-start-calibrate --field-selector=status.phase=Running --no-headers 2>/dev/null | wc -l | tr -d ' ')"
    if [[ "${nrunning}" == "1" ]]; then
      break
    fi
    sleep 2
    waited=$((waited + 2))
  done
  if [[ "${nrunning}" != "1" ]]; then
    die_named "UNCACHED_NOT_SINGLE_REPLICA count=${nrunning}"
  fi
  local running_node
  running_node="$(kubectl get pods -n "${CAL_NS}" -l app=cold-start-calibrate -o jsonpath='{.items[0].spec.nodeName}')"
  local other
  if [[ "${running_node}" == *worker* ]]; then
    other="$(kind_node)"
  else
    other="${KIND_CLUSTER}-worker"
  fi
  echo "UNCACHED_CORDON node=${running_node} pull_node=${other}"
  kubectl cordon "${running_node}"
  rmi_on_node "${other}" "${CAL_IMAGE}" "localhost:5001/hpa-eval-app:calibrate" "hpa-eval-app:smoke"
  local baseline
  baseline="$(kubectl get pods -n "${CAL_NS}" -l app=cold-start-calibrate -o jsonpath='{range .items[*]}{.metadata.name} {end}')"
  echo "UNCACHED_BASELINE_PODS ${baseline}"
  patch_hpa_cpu_target 10
  start_collector_logged "app=cold-start-calibrate" "${UNCACHED_JSONL}" "${OUT_DIR}/uncached-collector.log"
  start_cpu_load
  curl_new_pod "app=cold-start-calibrate" "${baseline}"
  wait_cold_start_collector \
    "${COLLECTOR_PID}" \
    "${COLLECTOR_T0}" \
    "${TIMEOUT_SEC}" \
    "${OUT_DIR}" \
    "${OUT_DIR}/uncached-collector.log" \
    "${UNCACHED_JSONL}"
  kubectl uncordon "${running_node}" || true
  echo "=== uncached collector log ==="
  cat "${OUT_DIR}/uncached-collector.log"
  evaluate_row "${UNCACHED_JSONL}" "false" "uncached"
}

main() {
  mkdir -p "${OUT_DIR}"
  ensure_kind
  load_images
  ensure_registry
  pull_cal_image_on_nodes
  install_metrics_server
  reset_namespace
  wait_hpa_metric
  run_cached_hpa
  run_uncached_hpa
  echo "CALIBRATION_COMPLETE"
}

main "$@"
