# Agent Rules (Hard Requirements)

- Never fabricate, estimate, interpolate, or backfill a metric value. Missing data is the literal string MISSING. This rule outranks every other goal in this repo.
- Every shell script: `#!/usr/bin/env bash`, `set -euo pipefail`, and a header comment stating OS/arch assumptions.
- Never use GNU-only flags (`sed -i` without suffix, `date -d`, `readlink -f`) without a BSD-compatible fallback or an explicit preflight guard.
- Never infer the GCP project from gcloud config. Required vars come from `.env` or flags; missing means exit 1.
- Destructive cloud operations must verify expected cluster name AND project before executing.
- Commit after every completed item with the item ID in the message. Cursor checkpoints do not track bash-created files, so git is the only real undo.
- Locust invocation is exactly: `--headless --csv <base> --csv-full-history`. Never add `--processes` (crashes with `--csv-full-history`, locust#2908). Never pass `--users` or `--spawn-rate` (`LoadTestShape` overrides them; they are inert).
