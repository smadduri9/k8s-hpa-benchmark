#!/usr/bin/env bash
# OS/arch assumptions: macOS (darwin) or Linux, bash 4+, curl, optional kaggle CLI.
# Operator-only bulk trace download for Phase 4 shape extraction.
#
# Fetches:
#   - 249 WorldCup98 wc_day*.gz files (~9 GB compressed per HPL-1999-35R1) from ITA FTP
#   - RetailRocket events.csv via Kaggle CLI (dataset zip ~987.5 MB per Kaggle Version 2)
#
# Resumable: skips a wc_day file when it already exists and passes the Step 3 decoder
# self-check (size % 20, monotonic timestamps, collection range). Re-downloads on
# verify failure. curl -C - resumes partial transfers.
#
# WC98 day boundaries follow France local midnight (+0200) per ITA — not UTC midnight.
# Step 5 extraction (extract_trace_counts.py) must convert GMT epoch to +0200 before
# assigning calendar days and window offsets (C3). Example: ts=893971817 is 1998-04-30
# 21:30:17 UTC = 23:30 France local on ITA day 5, not UTC day 5 midnight.
#
# Usage:
#   bash scripts/download_traces.sh [--wc98-only] [--retailrocket-only]
#
# Does not vendor or execute HP decode tools.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/common.sh
source "${SCRIPT_DIR}/lib/common.sh"

WC98_ONLY=false
RETAILROCKET_ONLY=false
WC98_FTP_BASE="ftp://ita.ee.lbl.gov/traces/WorldCup"
WC98_DIR="${REPO_ROOT}/traces/wc98"
RR_DIR="${REPO_ROOT}/traces/retailrocket"
DECODE_PY="${SCRIPT_DIR}/lib/wc98_decode.py"
CURL_CONNECT_TIMEOUT_SEC=30
CURL_MAX_TIME_SEC=3600
FTP_LIST_TIMEOUT_SEC=120

usage() {
  cat <<'EOF'
Usage: bash scripts/download_traces.sh [--wc98-only] [--retailrocket-only]

Downloads WorldCup98 wc_day*.gz into traces/wc98/ and RetailRocket events.csv into
traces/retailrocket/. Each wc_day file is verified immediately after download with
scripts/lib/wc98_decode.py (Step 3 self-check).

Requires: curl, repo .venv, kaggle CLI for RetailRocket (unless --wc98-only).
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --wc98-only) WC98_ONLY=true; shift ;;
    --retailrocket-only) RETAILROCKET_ONLY=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown argument: $1" ;;
  esac
done

if [[ "${WC98_ONLY}" == "true" && "${RETAILROCKET_ONLY}" == "true" ]]; then
  die "choose at most one of --wc98-only and --retailrocket-only"
fi

require_venv
if [[ ! -f "${DECODE_PY}" ]]; then
  die "missing decoder: ${DECODE_PY}"
fi

mkdir -p "${WC98_DIR}" "${RR_DIR}"

bytes_fetched_this_run=0
wc98_files_verified=0
wc98_files_downloaded=0

verify_wc98_file() {
  local path="$1"
  local summary
  local rc
  set +e
  summary="$("${VENV_PYTHON}" "${DECODE_PY}" "${path}" --summary-only --show-first 0 2>&1)"
  rc=$?
  set -e
  if [[ "${rc}" -ne 0 ]]; then
    printf '%s\n' "${summary}" >&2
    return 1
  fi
  if ! printf '%s\n' "${summary}" | grep -q 'WC98_DECODE_SELF_CHECK=PASS'; then
    printf '%s\n' "${summary}" >&2
    return 1
  fi
  printf '%s\n' "${summary}"
  return 0
}

parse_decoded_count() {
  local summary="$1"
  local count
  count="$(printf '%s\n' "${summary}" | sed -n 's/.*decoded_count=\([0-9][0-9]*\).*/\1/p' | head -n 1)"
  if [[ -z "${count}" ]]; then
    die "WC98_SUMMARY_PARSE_FAILED could not read decoded_count from: ${summary}"
  fi
  printf '%s' "${count}"
}

parse_uncompressed_size() {
  local summary="$1"
  local size
  size="$(printf '%s\n' "${summary}" | sed -n 's/.*file_size=\([0-9][0-9]*\).*/\1/p' | head -n 1)"
  if [[ -z "${size}" ]]; then
    die "WC98_SUMMARY_PARSE_FAILED could not read file_size from: ${summary}"
  fi
  printf '%s' "${size}"
}

download_wc98_file() {
  local name="$1"
  local dest="${WC98_DIR}/${name}"
  local url="${WC98_FTP_BASE}/${name}"
  local summary=""
  local compressed_bytes=0
  local decoded_count=0
  local uncompressed_bytes=0
  local before_bytes=0

  if [[ -f "${dest}" ]]; then
    before_bytes="$(wc -c < "${dest}" | tr -d ' ')"
    if summary="$(verify_wc98_file "${dest}")"; then
      decoded_count="$(parse_decoded_count "${summary}")"
      uncompressed_bytes="$(parse_uncompressed_size "${summary}")"
      wc98_files_verified=$((wc98_files_verified + 1))
      printf 'WC98_FILE_SKIP_VERIFY_PASS name=%s compressed_bytes=%s decoded_count=%s uncompressed_bytes=%s\n' \
        "${name}" "${before_bytes}" "${decoded_count}" "${uncompressed_bytes}"
      return 0
    fi
    printf 'WC98_FILE_VERIFY_FAILED_REDOWNLOAD name=%s compressed_bytes=%s\n' "${name}" "${before_bytes}" >&2
    rm -f "${dest}"
  fi

  printf 'WC98_FILE_DOWNLOAD_START name=%s url=%s\n' "${name}" "${url}"
  if ! curl -fsSL \
    --connect-timeout "${CURL_CONNECT_TIMEOUT_SEC}" \
    --max-time "${CURL_MAX_TIME_SEC}" \
    -C - \
    -o "${dest}" \
    "${url}"; then
    rm -f "${dest}"
    die "WC98_DOWNLOAD_FAILED name=${name}"
  fi

  compressed_bytes="$(wc -c < "${dest}" | tr -d ' ')"
  bytes_fetched_this_run=$((bytes_fetched_this_run + compressed_bytes))
  wc98_files_downloaded=$((wc98_files_downloaded + 1))

  if ! summary="$(verify_wc98_file "${dest}")"; then
    rm -f "${dest}"
    die "WC98_VERIFY_FAILED_AFTER_DOWNLOAD name=${name}"
  fi
  decoded_count="$(parse_decoded_count "${summary}")"
  uncompressed_bytes="$(parse_uncompressed_size "${summary}")"
  wc98_files_verified=$((wc98_files_verified + 1))
  printf 'WC98_FILE_DOWNLOAD_OK name=%s compressed_bytes=%s decoded_count=%s uncompressed_bytes=%s\n' \
    "${name}" "${compressed_bytes}" "${decoded_count}" "${uncompressed_bytes}"
}

download_wc98_all() {
  local listing=""
  local -a files=()
  local name=""
  local total_compressed=0
  local file_count=0

  printf '%s\n' "WC98_TIMEZONE_NOTE france_local=+0200 day_boundaries_not_utc_midnight=true"
  printf '%s\n' "WC98_FTP_LIST_START url=${WC98_FTP_BASE}/"

  if ! listing="$(curl -fsSL --connect-timeout "${CURL_CONNECT_TIMEOUT_SEC}" --max-time "${FTP_LIST_TIMEOUT_SEC}" "${WC98_FTP_BASE}/")"; then
    die "WC98_FTP_LIST_FAILED url=${WC98_FTP_BASE}/"
  fi

  while IFS= read -r name; do
    [[ -n "${name}" ]] || continue
    files+=("${name}")
  done < <(printf '%s\n' "${listing}" | grep -E '^wc_day[0-9]+_[0-9]+\.gz$' | sort)

  file_count="${#files[@]}"
  if [[ "${file_count}" -eq 0 ]]; then
    die "WC98_FTP_LIST_EMPTY url=${WC98_FTP_BASE}/"
  fi
  printf 'WC98_FILE_LIST_COUNT=%s\n' "${file_count}"

  for name in "${files[@]}"; do
    download_wc98_file "${name}"
  done

  while IFS= read -r path; do
    [[ -n "${path}" ]] || continue
    total_compressed=$((total_compressed + $(wc -c < "${path}" | tr -d ' ')))
  done < <(find "${WC98_DIR}" -maxdepth 1 -name 'wc_day*.gz' -type f | sort)

  printf 'WC98_DOWNLOAD_SUMMARY files_listed=%s files_verified=%s files_downloaded_this_run=%s bytes_fetched_this_run=%s total_compressed_bytes_on_disk=%s\n' \
    "${file_count}" "${wc98_files_verified}" "${wc98_files_downloaded}" \
    "${bytes_fetched_this_run}" "${total_compressed}"
}

verify_retailrocket_events() {
  local path="${RR_DIR}/events.csv"
  local header=""
  local line_count=0
  if [[ ! -f "${path}" ]]; then
    return 1
  fi
  header="$(head -n 1 "${path}")"
  if [[ "${header}" != "timestamp,visitorid,event,itemid,transactionid" ]]; then
    printf 'RETAILROCKET_VERIFY_FAILED reason=bad_header header=%s\n' "${header}" >&2
    return 1
  fi
  line_count="$(wc -l < "${path}" | tr -d ' ')"
  printf 'RETAILROCKET_VERIFY_PASS path=%s bytes=%s lines=%s\n' \
    "${path}" "$(wc -c < "${path}" | tr -d ' ')" "${line_count}"
  return 0
}

download_retailrocket() {
  local events_path="${RR_DIR}/events.csv"
  local rr_bytes_fetched=0

  if verify_retailrocket_events; then
    printf 'RETAILROCKET_SKIP_VERIFY_PASS bytes=%s\n' "$(wc -c < "${events_path}" | tr -d ' ')"
    return 0
  fi

  if ! command -v kaggle >/dev/null 2>&1; then
    die "KAGGLE_CLI_MISSING install kaggle and configure ~/.kaggle/kaggle.json for RetailRocket download"
  fi

  printf '%s\n' "RETAILROCKET_DOWNLOAD_START dataset=retailrocket/ecommerce-dataset"
  rm -f "${events_path}" 2>/dev/null || true
  find "${RR_DIR}" -maxdepth 1 \( -name '*.csv' -o -name '*.zip' \) -delete 2>/dev/null || true

  if ! kaggle datasets download -d retailrocket/ecommerce-dataset -p "${RR_DIR}" --unzip; then
    die "RETAILROCKET_DOWNLOAD_FAILED dataset=retailrocket/ecommerce-dataset"
  fi

  if ! verify_retailrocket_events; then
    die "RETAILROCKET_VERIFY_FAILED_AFTER_DOWNLOAD"
  fi

  rr_bytes_fetched="$(wc -c < "${events_path}" | tr -d ' ')"
  bytes_fetched_this_run=$((bytes_fetched_this_run + rr_bytes_fetched))
  printf 'RETAILROCKET_DOWNLOAD_OK bytes=%s\n' "${rr_bytes_fetched}"
}

main() {
  printf '%s\n' "DOWNLOAD_TRACES_START repo_root=${REPO_ROOT}"

  if [[ "${RETAILROCKET_ONLY}" != "true" ]]; then
    download_wc98_all
  fi

  if [[ "${WC98_ONLY}" != "true" ]]; then
    download_retailrocket
  fi

  printf 'DOWNLOAD_TRACES_COMPLETE bytes_fetched_this_run=%s\n' "${bytes_fetched_this_run}"
}

main "$@"
