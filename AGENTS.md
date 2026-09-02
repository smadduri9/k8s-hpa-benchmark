# Agent Rules (Hard Requirements)

- Never fabricate, estimate, interpolate, or backfill a metric value. Missing data is the literal string MISSING. This rule outranks every other goal in this repo.
- Every shell script: `#!/usr/bin/env bash`, `set -euo pipefail`, and a header comment stating OS/arch assumptions.
- Never use GNU-only flags (`sed -i` without suffix, `date -d`, `readlink -f`) without a BSD-compatible fallback or an explicit preflight guard.
- Never infer the GCP project from gcloud config. Required vars come from `.env` or flags; missing means exit 1.
- Destructive cloud operations must verify expected cluster name AND project before executing.
- Commit after every completed item with the item ID in the message. Cursor checkpoints do not track bash-created files, so git is the only real undo.
- Locust invocation is exactly: `--headless --csv <base> --csv-full-history`. Never add `--processes` (crashes with `--csv-full-history`, locust#2908). Never pass `--users` or `--spawn-rate` (`LoadTestShape` overrides them; they are inert).
- Never patch a container's args array wholesale. Vendor the full manifest or append. Replacing args silently drops upstream defaults — this caused the t1-0 gate failure.
- Minimum supported Python is **3.9**. Analysis modules use `from __future__ import annotations` when typing with PEP 604 unions (`str | None`). Preflight imports every `analysis/*.py` module plus `numpy` and `matplotlib` and fails on the first import error.
- Cold-start production readiness timeout defaults to **180s** (`COLD_START_READINESS_TIMEOUT_SEC` in `scripts/lib/cold_start.sh`). Smoke negative tests may override to 30s; that override must never be the production default.
- kubectl client/server minor-version skew: preflight **WARN** when skew > 1, **FAIL** when skew > 2 (Kubernetes supported skew is one minor version).
- On arm64 hosts, GKE image builds in `scripts/deploy_gke.sh` must pass `docker build --platform linux/amd64`; preflight fails if the flag is absent.
