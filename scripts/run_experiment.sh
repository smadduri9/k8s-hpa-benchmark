#!/usr/bin/env bash
# run_experiment.sh — Run both fixed and HPA experiments sequentially
# Usage: bash scripts/run_experiment.sh [HOST_IP]
#
# Runs fixed experiment → collects metrics → switches to HPA → runs again → analyzes

set -euo pipefail

NAMESPACE="hpa-eval"
HOST="${1:-}"
PROMETHEUS_URL="http://localhost:9090"
LOCUST_USERS=200
LOCUST_SPAWN_RATE=10
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
locust -f locust/locustfile.py \
    --host "${FIXED_HOST}" \
    --headless \
    --users "${LOCUST_USERS}" \
    --spawn-rate "${LOCUST_SPAWN_RATE}" \
    --run-time "${EXPERIMENT_DURATION}" \
    --csv=sample_data/locust_fixed \
    --logfile=sample_data/locust_fixed.log

log "Collecting metrics from Prometheus..."
sleep 5  # brief pause for final metrics to settle
python3 analysis/collect_metrics.py \
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
locust -f locust/locustfile.py \
    --host "${HPA_HOST}" \
    --headless \
    --users "${LOCUST_USERS}" \
    --spawn-rate "${LOCUST_SPAWN_RATE}" \
    --run-time "${EXPERIMENT_DURATION}" \
    --csv=sample_data/locust_hpa \
    --logfile=sample_data/locust_hpa.log

log "Collecting metrics from Prometheus..."
sleep 5
python3 analysis/collect_metrics.py \
    --mode hpa \
    --prometheus-url "${PROMETHEUS_URL}" \
    --duration-minutes 18

log "HPA experiment complete."

# ---------------------------------------------------------------------------
# Analysis
# ---------------------------------------------------------------------------
log "Running analysis and generating figures..."
python3 analysis/analyze_results.py

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------
kill "${PF_PID}" 2>/dev/null || true

log "========================================"
log "EXPERIMENTS COMPLETE"
log "Results in: sample_data/"
log "Figures in: sample_data/figures/"
log "========================================"
