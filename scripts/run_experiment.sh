#!/usr/bin/env bash
# OS/arch assumptions: macOS (darwin) or Linux, bash 4+, kubectl, repo .venv tooling.
# run_experiment.sh — legacy wrapper; prefer scripts/run_benchmark.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
# shellcheck source=lib/cleanup.sh
source "${SCRIPT_DIR}/lib/cleanup.sh"
# shellcheck source=lib/locust_run.sh
source "${SCRIPT_DIR}/lib/locust_run.sh"
require_venv

cleanup_on_exit() {
  cleanup_background_jobs
}
trap cleanup_on_exit EXIT INT TERM

NAMESPACE="hpa-eval"
HOST="${1:-}"
PROMETHEUS_URL="http://localhost:9090"
EXPERIMENT_DURATION="18m"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
log() { echo "[$(date '+%H:%M:%S')] $*"; }

wait_for_pods() {
    local selector="$1"
    log "Waiting for pods with selector: ${selector}"
    kubectl wait --for=condition=Ready pod -l "${selector}" \
        -n "${NAMESPACE}" --timeout=120s
}

# ---------------------------------------------------------------------------
# Auto-detect service IP if not provided
# ---------------------------------------------------------------------------
if [[ -z "${HOST}" ]]; then
    if minikube status &>/dev/null; then
        MINIKUBE_IP=$(minikube ip)
        FIXED_PORT=$(kubectl get svc hpa-eval-fixed-svc -n "${NAMESPACE}" \
            -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null || echo "")
        HPA_PORT=$(kubectl get svc hpa-eval-hpa-svc -n "${NAMESPACE}" \
            -o jsonpath='{.spec.ports[0].nodePort}' 2>/dev/null || echo "")
        FIXED_HOST="http://${MINIKUBE_IP}:${FIXED_PORT}"
        HPA_HOST="http://${MINIKUBE_IP}:${HPA_PORT}"
    else
        FIXED_IP=$(kubectl get svc hpa-eval-fixed-svc -n "${NAMESPACE}" \
            -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
        HPA_IP=$(kubectl get svc hpa-eval-hpa-svc -n "${NAMESPACE}" \
            -o jsonpath='{.status.loadBalancer.ingress[0].ip}')
        FIXED_HOST="http://${FIXED_IP}"
        HPA_HOST="http://${HPA_IP}"
    fi
else
    FIXED_HOST="${HOST}"
    HPA_HOST="${HOST}"
fi

log "Fixed host: ${FIXED_HOST}"
log "HPA host:   ${HPA_HOST}"

# ---------------------------------------------------------------------------
# Start Prometheus port-forward in background
# ---------------------------------------------------------------------------
log "Starting Prometheus port-forward..."
kubectl port-forward svc/prometheus "${PROMETHEUS_URL##*:}:9090" \
    -n "${NAMESPACE}" &
PF_PID=$!
register_port_forward_pid "${PF_PID}"
sleep 3
log "Prometheus available at ${PROMETHEUS_URL}"

# ---------------------------------------------------------------------------
# Experiment 1: Fixed deployment
# ---------------------------------------------------------------------------
log "========================================"
log "EXPERIMENT 1: FIXED DEPLOYMENT (3 pods)"
log "========================================"

wait_for_pods "app=hpa-eval,experiment=fixed"

log "Starting Locust load test against fixed deployment..."
LOCUST_WALL_MARGIN_SEC=120 run_locust_bounded \
    "${REPO_ROOT}/locust/locustfile.py" \
    "${FIXED_HOST}" \
    "${EXPERIMENT_DURATION}" \
    "${REPO_ROOT}/results/legacy/locust_fixed" \
    "${REPO_ROOT}/results/legacy/locust_fixed.log" \
    ""

log "Collecting metrics from Prometheus..."
sleep 5  # brief pause for final metrics to settle
venv_python "${REPO_ROOT}/analysis/collect_metrics.py" \
    --mode fixed \
    --prometheus-url "${PROMETHEUS_URL}" \
    --duration-minutes 18

log "Fixed experiment complete."

# ---------------------------------------------------------------------------
# Experiment 2: HPA deployment
# ---------------------------------------------------------------------------
log "========================================"
log "EXPERIMENT 2: HPA DEPLOYMENT (1–10 pods)"
log "========================================"

# Reset HPA deployment to 1 replica
kubectl scale deployment hpa-eval-hpa --replicas=1 -n "${NAMESPACE}"
wait_for_pods "app=hpa-eval,experiment=hpa"
log "HPA deployment reset to 1 replica. Waiting 30s for stability..."
sleep 30

log "Starting Locust load test against HPA deployment..."
LOCUST_WALL_MARGIN_SEC=120 run_locust_bounded \
    "${REPO_ROOT}/locust/locustfile.py" \
    "${HPA_HOST}" \
    "${EXPERIMENT_DURATION}" \
    "${REPO_ROOT}/results/legacy/locust_hpa" \
    "${REPO_ROOT}/results/legacy/locust_hpa.log" \
    ""

log "Collecting metrics from Prometheus..."
sleep 5
venv_python "${REPO_ROOT}/analysis/collect_metrics.py" \
    --mode hpa \
    --prometheus-url "${PROMETHEUS_URL}" \
    --duration-minutes 18

log "HPA experiment complete."

# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
log "Running analysis and generating figures..."
venv_python "${REPO_ROOT}/analysis/analyze_results.py"

# ---------------------------------------------------------------------------
# Cleanup (trap also runs on exit)
# ---------------------------------------------------------------------------
log "========================================"
log "EXPERIMENTS COMPLETE"
log "Results in: sample_data/"
log "Figures in: sample_data/figures/"
log "========================================"
