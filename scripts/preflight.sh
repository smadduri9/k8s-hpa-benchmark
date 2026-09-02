#!/usr/bin/env bash
# OS/arch assumptions: macOS (darwin) or Linux, bash 4+, kubectl, docker, gcloud, locust, python3.
# Strict preflight checks. Never infers GCP project from gcloud config.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

ENV_FILE=""
REQUIRE_GKE=false
PREFLIGHT_FAILED=false

usage() {
  cat <<'EOF'
Usage: bash scripts/preflight.sh [--env-file .env] [--require-gke]

Auto-detects and hard-fails on missing/incompatible toolchain.
Requires explicit PROJECT_ID, REGION, CLUSTER_NAME, ARTIFACT_REGISTRY_REPO when --require-gke is set.
EOF
}

preflight_fail() {
  echo "ERROR: $*" >&2
  PREFLIGHT_FAILED=true
}

preflight_warn() {
  echo "WARNING: $*" >&2
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file)
      ENV_FILE="$2"
      shift 2
      ;;
    --require-gke)
      REQUIRE_GKE=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

load_env_file "${ENV_FILE}"

OS_NAME="$(uname -s)"
ARCH_NAME="$(uname -m)"
echo "PREFLIGHT_TABLE_BEGIN"
echo "os=${OS_NAME}"
echo "arch=${ARCH_NAME}"

if [[ "${OS_NAME}" != "Darwin" && "${OS_NAME}" != "Linux" ]]; then
  die "unsupported OS: ${OS_NAME}"
fi

# GNU vs BSD tool checks
if sed --version >/dev/null 2>&1; then
  echo "sed=GNU"
else
  echo "sed=BSD"
fi

if date -d '@0' >/dev/null 2>&1; then
  echo "date=GNU"
else
  echo "date=BSD"
fi

first_line() {
  local text="$1"
  printf '%s' "${text%%$'\n'*}"
}

parse_k8s_minor() {
  local version="$1"
  version="${version#v}"
  version="${version#*.}"
  echo "${version%%.*}"
}

for cmd in gcloud docker python3; do
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    preflight_fail "missing required command: ${cmd}"
  else
    version="$(first_line "$(${cmd} --version 2>&1)")"
    echo "${cmd}=${version}"
  fi
done

if command -v kubectl >/dev/null 2>&1; then
  echo "kubectl=$(kubectl version --client=true 2>&1 | awk '/Client Version:/{print $0; exit}')"
else
  preflight_fail "missing required command: kubectl"
fi

if command -v locust >/dev/null 2>&1; then
  echo "locust=$(first_line "$(locust --version 2>&1)")"
elif python3 -m locust --version >/dev/null 2>&1; then
  echo "locust=$(first_line "$(python3 -m locust --version 2>&1)") [python -m]"
else
  preflight_fail "missing required command: locust (or python3 -m locust)"
fi

client_ver="$(kubectl version --client=true -o yaml 2>/dev/null | awk '/gitVersion:/{print $2; exit}')"
server_ver=""
if kubectl cluster-info >/dev/null 2>&1; then
  server_ver="$(kubectl version --output=yaml 2>/dev/null | awk '/serverVersion:/{f=1} f&&/gitVersion:/{print $2; exit}')"
  echo "kubectl_client=${client_ver}"
  echo "kubectl_server=${server_ver}"

  client_minor="$(parse_k8s_minor "${client_ver}")"
  server_minor="$(parse_k8s_minor "${server_ver}")"
  if [[ "${client_minor}" =~ ^[0-9]+$ && "${server_minor}" =~ ^[0-9]+$ ]]; then
    skew=$(( client_minor > server_minor ? client_minor - server_minor : server_minor - client_minor ))
    echo "kubectl_minor_skew=${skew}"
    if [[ "${skew}" -gt 2 ]]; then
      preflight_fail "kubectl version skew ${skew} exceeds policy (max 2 minor versions); client=${client_ver} server=${server_ver}"
      echo "REMEDIATION: brew upgrade kubectl  OR  gcloud components update kubectl" >&2
    elif [[ "${skew}" -gt 1 ]]; then
      preflight_warn "kubectl version skew ${skew} exceeds recommended max of 1 minor version; client=${client_ver} server=${server_ver}"
      echo "REMEDIATION: brew upgrade kubectl  OR  gcloud components update kubectl" >&2
    else
      echo "kubectl_skew_check=PASS"
    fi
  else
    preflight_warn "could not parse kubectl minor versions client=${client_ver} server=${server_ver}"
  fi
else
  echo "kubectl_client=${client_ver}"
  echo "kubectl_server=UNAVAILABLE"
  echo "kubectl_skew_check=SKIPPED_NO_CLUSTER"
fi

deploy_gke="${REPO_ROOT}/scripts/deploy_gke.sh"
if [[ "${ARCH_NAME}" == "arm64" || "${ARCH_NAME}" == "aarch64" ]]; then
  if [[ ! -f "${deploy_gke}" ]]; then
    preflight_fail "missing GKE deploy script for platform check: ${deploy_gke}"
  elif ! grep -qE 'docker[[:space:]]+build.*--platform[[:space:]]+linux/amd64' "${deploy_gke}"; then
    preflight_fail "host arch ${ARCH_NAME} requires explicit docker build --platform linux/amd64 in ${deploy_gke}"
    echo "REMEDIATION: add --platform linux/amd64 to the GKE image build command" >&2
  else
    echo "docker_platform_check=PASS build_script=${deploy_gke} platform=linux/amd64"
  fi
else
  echo "docker_platform_check=SKIPPED arch=${ARCH_NAME}"
fi

python3 "${SCRIPT_DIR}/lib/preflight_python.py" || PREFLIGHT_FAILED=true

if [[ "${REQUIRE_GKE}" == "true" ]]; then
  require_env PROJECT_ID
  require_env REGION
  require_env CLUSTER_NAME
  require_env ARTIFACT_REGISTRY_REPO
  echo "PROJECT_ID=${PROJECT_ID}"
  echo "REGION=${REGION}"
  echo "CLUSTER_NAME=${CLUSTER_NAME}"
  echo "ARTIFACT_REGISTRY_REPO=${ARTIFACT_REGISTRY_REPO}"
  echo "PROJECT_CLUSTER_VERIFICATION_REQUIRED"
fi

if [[ "${PREFLIGHT_FAILED}" == "true" ]]; then
  echo "PREFLIGHT_FAIL" >&2
  exit 1
fi

echo "PREFLIGHT_PASS"
echo "PREFLIGHT_TABLE_END"
