# Agent Rules (Hard Requirements)

- Never fabricate, estimate, interpolate, or backfill a metric value. Missing data is the literal string MISSING. This rule outranks every other goal in this repo.
- Every shell script: `#!/usr/bin/env bash`, `set -euo pipefail`, and a header comment stating OS/arch assumptions.
- Never use GNU-only flags (`sed -i` without suffix, `date -d`, `readlink -f`) without a BSD-compatible fallback or an explicit preflight guard.
- Never infer the GCP project from gcloud config. Required vars come from `.env` or flags; missing means exit 1.
- Destructive cloud operations must verify expected cluster name AND project before executing.
- Commit after every completed item with the item ID in the message. Cursor checkpoints do not track bash-created files, so git is the only real undo.
- Locust invocation is exactly: `--headless --csv <base> --csv-full-history --exit-code-on-error 0`. Never add `--processes` (crashes with `--csv-full-history`, locust#2908). Never pass `--users` or `--spawn-rate` (`LoadTestShape` overrides them; they are inert). Arm success is artifact-based (stats CSV + shape completion), not Locust exit code.
- Never patch a container's args array wholesale. Vendor the full manifest or append. Replacing args silently drops upstream defaults — this caused the t1-0 gate failure.
- All Python and Locust invocations use `"${REPO_ROOT}/.venv/bin/python"` and `"${REPO_ROOT}/.venv/bin/locust"`, always quoted. Never call bare `python3` or `locust` in repo scripts. The repo path contains a space; every path expansion must be quoted.
- Tooling runs in the repo venv at `.venv/` (Python **3.14**). Create with `python3 -m venv .venv` and install `requirements-tooling.txt`. Preflight fails if `.venv` is absent and prints creation commands. Preflight imports every `analysis/*.py` module plus `numpy` and `matplotlib` via the venv interpreter.
- Cold-start production readiness timeout defaults to **180s** (`COLD_START_READINESS_TIMEOUT_SEC` in `scripts/lib/cold_start.sh`). Smoke negative tests may override to 30s; that override must never be the production default.
- kubectl client/server minor-version skew: preflight **WARN** when skew > 1, **FAIL** when skew > 2 (Kubernetes supported skew is one minor version).
- On arm64 hosts, GKE image builds in `scripts/deploy_gke.sh` must pass `docker build --platform linux/amd64`; preflight fails if the flag is absent.
- HPA arm collection aborts with `HPA_NEVER_SCALED` if peak `spec_replicas` never exceeds `minReplicas` during the anchored window (from in-run `replica_series_<arm>.csv`, not live kubectl at collection time). `HPA_SCALE_FLOOR_CHECK` peak uses `spec_replicas` (HPA desired count); `ready_replicas` is recorded separately.
- Fixed arm aborts with `REPLICA_BELOW_DECLARED` if peak in-window `ready_replicas` never reaches declared count; mid-run dips below declared log `REPLICA_DIP_OBSERVED` and continue.
- Metrics cells are `TARGET_UNAVAILABLE` only on `UNAVAILABLE` rows (`ready_replicas == 0`). Serving rows (`DEGRADED` or `AVAILABLE`) use measured values or `MISSING`; a cell must never contradict its row's `availability_state`. Coverage (`METRICS_COLUMN_COVERAGE`) is over serving rows (`ready_replicas > 0`); rate columns exclude the first two serving rows (burst-onset). Report `TARGET_AVAILABILITY` with `rows_unavailable`, `rows_degraded`, and `rows_available` each out of `rows_total`. Never lower `MIN_COLUMN_COVERAGE_RATIO`.
- No operation may block indefinitely. Every wait has an explicit timeout and a named error on expiry.
- Locust invocations must pass explicit `--run-time`, verify load-target reachability before start (`LOAD_TARGET_UNREACHABLE` on failure), and run under a wall-clock guard (`LOCUST_TIMEOUT` on expiry). Never capture Locust stdout via command substitution.
- Never use command substitution to capture a function's return value. Functions return status via exit codes; diagnostic output goes to stderr or a log file, never stdout.
- Record observed symptoms as symptoms only. Attributing a cause requires evidence for that specific cause — exit code 137 is SIGKILL, not OOMKilled; OOMKilled requires container status, an event, or memory observation.
- Before tuning any parameter against a measurement, run the same measurement at least three times unchanged and confirm run-to-run variance is smaller than the effect being measured. A sweep whose noise exceeds its signal is uninformative regardless of how clean the table looks.

## Commit and code style
- Commit messages: imperative mood, one line under 72 chars, optional body.
  No trailers, no attribution lines, no emoji, no "Generated with".
- No decorative comment banners, no restating the obvious, no "# Step 1:" style
  narration. Comments explain WHY, not WHAT.
- Match the surrounding file's existing style. Do not reformat untouched code.
- No AI-assistant references anywhere in code, comments, docs, or messages.
