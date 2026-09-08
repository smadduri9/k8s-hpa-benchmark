#!/usr/bin/env bash
# OS/arch assumptions: macOS (darwin) or Linux, bash 4+, kubectl, live cluster context.
# Verify HPA maxReplicas fits measured schedulable CPU (HPA_MAX_REPLICAS_SCHEDULABLE_GATE).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

NAMESPACE="${NAMESPACE:-hpa-eval}"

require_venv
"${VENV_PYTHON}" "${SCRIPT_DIR}/lib/check_allocatable_gate.py" \
  --namespace "${NAMESPACE}" \
  --hpa-manifest "${REPO_ROOT}/k8s/hpa.yaml" \
  --fixed-manifest "${REPO_ROOT}/k8s/deployment-fixed.yaml" \
  --prometheus-manifest "${REPO_ROOT}/k8s/prometheus/deployment-gke.yaml"
