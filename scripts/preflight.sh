#!/usr/bin/env bash
# OS/arch assumptions: macOS (darwin) or Linux, bash 4+, kubectl, docker, gcloud.
# Strict preflight checks. Never infers GCP project from gcloud config.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"
# shellcheck source=lib/preflight_gke.sh
source "${SCRIPT_DIR}/lib/preflight_gke.sh"

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
  printf '%s\n' "ERROR: $*" >&2
  PREFLIGHT_FAILED=true
}

preflight_warn() {
  printf '%s\n' "WARNING: $*" >&2
}

run_repo_path_whitespace_audit() {
  local audit_py="${SCRIPT_DIR}/lib/audit_repo_path_quoting.py"
  local audit_python="${VENV_PYTHON}"
  if [[ ! -x "${audit_python}" ]]; then
    audit_python="$(command -v python3 || true)"
  fi
  if [[ -z "${audit_python}" || ! -f "${audit_py}" ]]; then
    preflight_fail "missing whitespace audit helper: ${audit_py}"
    return
  fi
  if ! "${audit_python}" "${audit_py}" "${REPO_ROOT}"; then
    preflight_fail "unquoted REPO_ROOT expansions found while repo path contains whitespace"
  fi
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
printf '%s\n' "PREFLIGHT_TABLE_BEGIN"
printf '%s\n' "os=${OS_NAME}"
printf '%s\n' "arch=${ARCH_NAME}"
printf '%s\n' "repo_root=\"${REPO_ROOT}\""

run_repo_path_whitespace_audit

if [[ "${OS_NAME}" != "Darwin" && "${OS_NAME}" != "Linux" ]]; then
  die "unsupported OS: ${OS_NAME}"
fi

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

for cmd in gcloud docker kubectl; do
  if ! command -v "${cmd}" >/dev/null 2>&1; then
    preflight_fail "missing required command: ${cmd}"
  else
    if [[ "${cmd}" == "kubectl" ]]; then
      version="$(kubectl version --client=true 2>&1 | awk '/Client Version:/{print $0; exit}')"
    else
      version="$(first_line "$(${cmd} --version 2>&1)")"
    fi
    echo "${cmd}=${version}"
  fi
done

if [[ ! -d "${VENV_DIR}" ]]; then
  preflight_fail "missing venv at ${VENV_DIR}"
  cat >&2 <<EOF
REMEDIATION:
  python3 -m venv "${VENV_DIR}"
  "${VENV_DIR}/bin/python" -m pip install -r "${REPO_ROOT}/requirements-tooling.txt"
EOF
elif [[ ! -x "${VENV_PYTHON}" ]]; then
  preflight_fail "venv exists but interpreter missing at ${VENV_PYTHON}"
else
  venv_python_path="$(cd "$(dirname "${VENV_PYTHON}")" && pwd)/$(basename "${VENV_PYTHON}")"
  venv_python_version="$("${VENV_PYTHON}" --version 2>&1)"
  echo "venv_python_path=${venv_python_path}"
  echo "venv_python_version=${venv_python_version}"
fi

if [[ -x "${VENV_LOCUST}" ]]; then
  locust_version_out="$("${VENV_LOCUST}" --version 2>&1)"
  echo "venv_locust_version=${locust_version_out}"
  if echo "${locust_version_out}" | grep -q ' from '; then
    locust_from="$(echo "${locust_version_out}" | sed -n 's/.* from \(.*\)$/\1/p')"
    case "${locust_from}" in
      "${VENV_DIR}"/*) echo "venv_locust_path_check=PASS from=${locust_from}" ;;
      *)
        preflight_fail "locust resolves outside venv: ${locust_from} (expected under ${VENV_DIR})"
        ;;
    esac
  else
    locust_real="$(cd "$(dirname "${VENV_LOCUST}")" && pwd)/$(basename "${VENV_LOCUST}")"
    echo "venv_locust_path=${locust_real}"
  fi
elif [[ -d "${VENV_DIR}" ]]; then
  preflight_fail "venv locust missing at ${VENV_LOCUST}"
  echo "REMEDIATION: \"${VENV_PYTHON}\" -m pip install -r \"${REPO_ROOT}/requirements-tooling.txt\"" >&2
fi

if command -v locust >/dev/null 2>&1; then
  path_locust="$(command -v locust)"
  case "${path_locust}" in
    "${VENV_DIR}"/*) ;;
    *)
      preflight_fail "PATH locust (${path_locust}) is outside ${VENV_DIR}; scripts use venv locust only"
      ;;
  esac
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
      printf '%s\n' "REMEDIATION: align kubectl client with cluster (e.g. gcloud components install kubectl, then ensure gcloud bin precedes brew on PATH)" >&2
    elif [[ "${skew}" -gt 1 ]]; then
      preflight_warn "kubectl version skew ${skew} exceeds recommended max of 1 minor version; client=${client_ver} server=${server_ver}"
      printf '%s\n' "REMEDIATION: align kubectl client with cluster (e.g. gcloud components install kubectl, then ensure gcloud bin precedes brew on PATH)" >&2
      printf '%s\n' "kubectl_skew_check=WARN"
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
    printf '%s\n' "REMEDIATION: add --platform linux/amd64 to the GKE image build command" >&2
  else
    printf '%s\n' "docker_platform_check=PASS build_script=${deploy_gke} platform=linux/amd64"
  fi
else
  echo "docker_platform_check=SKIPPED arch=${ARCH_NAME}"
fi

if [[ -x "${VENV_PYTHON}" ]]; then
  "${VENV_PYTHON}" "${SCRIPT_DIR}/lib/preflight_python.py" || PREFLIGHT_FAILED=true
fi

if [[ "${REQUIRE_GKE}" == "true" ]]; then
  preflight_require_gke
fi

if [[ "${PREFLIGHT_FAILED}" == "true" ]]; then
  printf '%s\n' "PREFLIGHT_FAIL" >&2
  exit 1
fi

printf '%s\n' "PREFLIGHT_PASS"
printf '%s\n' "PREFLIGHT_TABLE_END"
