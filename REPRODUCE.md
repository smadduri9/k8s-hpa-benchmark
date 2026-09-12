# Reproduce and verify

There are two separate paths: recompute the published findings offline, or collect a new benchmark on infrastructure you provision. Start with offline verification.

## Verify published results

**No cloud, credentials, original traces or private run folders are required.** The [v1.1 package](artifacts/v1.1/README.md) contains the retained inputs, explicit exclusions and canonical summaries.

From the repository root, use Python 3.14 to create the tooling environment:

```bash
export REPO_ROOT="$PWD"
python3 -m venv .venv
"${REPO_ROOT}/.venv/bin/python" -m pip install -r requirements-tooling.txt
"${REPO_ROOT}/.venv/bin/python" -m pip install pytest==9.1.1
```

The package verifier itself uses only the Python standard library. Dependency installation needs network access once; subsequent verification is offline. NumPy and Matplotlib are needed for the broader repository tooling and figures, not for the standalone verifier.

```bash
"${REPO_ROOT}/.venv/bin/python" -B artifacts/v1.1/verify.py
"${REPO_ROOT}/.venv/bin/python" -B -m pytest -q tests/test_publication.py
"${REPO_ROOT}/.venv/bin/python" -B scripts/generate_publication_text.py --check
"${REPO_ROOT}/.venv/bin/python" -B scripts/check_public_docs.py
```

| Check | What it verifies |
|---|---|
| `verify.py` | File inventory, byte sizes, hashes, required arms, completion extracts, exclusions and agreement between recomputed and committed summaries |
| Publication calculations | Locust run-level p95 medians, exact paired Wilcoxon tests, sampled ready-pod time, anchored replica peaks, calibration derivation and normalized workload plateaus |
| `tests/test_publication.py` | Hand-computed integral/rank cases, standalone operation, and rejection of altered summaries, policy changes, raw-data edits, extra arms, symlinks and secret-like inputs |
| Text check | Bounded metric tables/prose in README, RESULTS and the existing Pages document match canonical summaries |
| Documentation check | Local active links and heading anchors resolve to public tracked material; README images and the two engineering SVGs are valid |

Success is exit code zero and five `PASS` lines from the verifier, a passing pytest summary, `PUBLICATION_TEXT_PASS`, and `PUBLIC_DOCS_PASS`. These checks run without changing evidence. The same publication tests can also run without pytest:

```bash
"${REPO_ROOT}/.venv/bin/python" -B -m unittest discover -s tests -p test_publication.py
```

The verifier can run after copying `artifacts/v1.1/` alone into another directory. Repository tests and presentation checks additionally need this checkout. Hashes prove internal consistency; they do not independently attest the collection environment.

### Regenerate the measurement figures

```bash
"${REPO_ROOT}/.venv/bin/python" -B scripts/generate_public_figures.py
"${REPO_ROOT}/.venv/bin/python" -B scripts/generate_publication_text.py --check
```

The figure generator reads [canonical summaries](artifacts/v1.1/summary/) and writes SVGs, PNG fallbacks and a figure manifest under [docs/assets/figures/](docs/assets/figures/). Architecture and measurement-guards SVGs are separate, hand-maintained diagrams. Do not use the verifier's `--write` option to accept an unexplained mismatch.

## Re-run the benchmark

**The original cloud cluster was deleted.** A newly created GKE environment may exhibit deployment/hardware variability, so exact byte-for-byte performance reproduction is not guaranteed. The instructions below describe a new collection workflow. They were checked against the repository scripts, not executed against a new cloud cluster during publication cleanup.

### Environment and configuration

Use macOS or Linux with Bash 4+, Git, Python 3.14, Docker, `gcloud`, `kubectl`, and the repository tooling environment above. Kind is needed for local smoke checks. Supply a GCP project with GKE and Artifact Registry access, suitable permissions, and sufficient quota. Deployment creates billable resources.

```bash
cp .env.example .env
```

Fill **all** required targets: `PROJECT_ID`, `REGION`, `ZONE`, `CLUSTER_NAME` and `ARTIFACT_REGISTRY_REPO`. Use a dedicated benchmark cluster. Scripts read explicit environment-file values; the active gcloud project is not a substitute. Keep credentials and `.env` out of Git.

| Component | Repository implementation |
|---|---|
| GKE environment | [Deployment script](scripts/deploy_gke.sh), [node defaults](scripts/lib/gke_shape.sh): three `e2-standard-4` nodes and 50 GB boot disks |
| Application | [FastAPI app](app/main.py), [image build](app/Dockerfile), [fixed deployment](k8s/deployment-fixed.yaml) and [HPA deployment](k8s/deployment-hpa.yaml) |
| Capacity policy | Fixed 4; HPA 4–12, 60% CPU target; [tuned](k8s/hpa.yaml) and [stock](k8s/hpa-stock.yaml) manifests |
| Load | [Locust drivers](locust/), [matrix runner](scripts/run_phase5_matrix.sh), [warm-up](locust/locustfile_warmup.py) |
| Observation | metrics-server for HPA and calibration; [Prometheus](k8s/prometheus/), [collector](analysis/collect_metrics.py) and [replica sampler](scripts/lib/replica_sampler.sh) |
| Publication | [Evidence curation](scripts/build_public_evidence.py), [offline verifier](artifacts/v1.1/verify.py), [text](scripts/generate_publication_text.py) and [figure](scripts/generate_public_figures.py) generators |

The deployment script builds `linux/amd64` images explicitly, including on arm64 hosts. The measured load generator was external to the cluster and reached the application through GKE LoadBalancer Services. Record the client location and network path for a new experiment. Keep the machine awake through the matrix.

### Validate and deploy

```bash
bash scripts/smoke_test.sh --check reproduce-docs
bash scripts/smoke_test.sh --check harness
bash scripts/smoke_test.sh --full --env-file .env
bash scripts/deploy_gke.sh --env-file .env
bash scripts/preflight.sh --env-file .env --require-gke
```

The `reproduce-docs` check validates runbook references; `handoff-docs` remains a compatibility alias. Kind results validate the harness; they are not performance-comparable to GKE. Preflight checks tooling, node resources, metrics-server access and quota; it needs the deployed environment for its live checks. Review explicit project and cluster targets before deployment. Existing runner-VM quota guards also apply if that optional VM exists.

### Prepare trace-derived workloads

The five committed `locustfile_wc98_*.py` and `locustfile_rr_periodic.py` drivers already contain the selected plateau envelopes. They can be used without downloading the original datasets. The offline verifier independently derives their publication plateau values from [bundled aggregate windows](artifacts/v1.1/workloads/).

To repeat the full selection workflow, obtain the original WorldCup98 logs and RetailRocket events under their [source terms](artifacts/v1.1/SOURCE_NOTICES.md). RetailRocket download requires your own Kaggle credentials and the Kaggle CLI installed in the repository venv. Original trace inputs and intermediate extraction outputs use the gitignored `traces/` directory; it is an operator workspace, not a public evidence source.

```bash
bash scripts/download_traces.sh
"${REPO_ROOT}/.venv/bin/python" scripts/extract_trace_counts.py
"${REPO_ROOT}/.venv/bin/python" scripts/select_shape_windows.py
"${REPO_ROOT}/.venv/bin/python" scripts/build_shape_artifacts.py
```

Run selection in a fresh checkout or preserve existing outputs first: these commands regenerate candidate tables, provenance and drivers. Read the frozen [selection rule](docs/SHAPE_SELECTION.md) before rescoring. The public package reproduces the five selected windows, not the entire source-dataset search. The generator's default amplitude is a development default; the measured matrix requires the capacity-probe derivation below.

### Calibrate, then collect

```bash
bash scripts/run_capacity_probe.sh --env-file .env
```

This writes local operator output to `results/capacity_probe/`, including `steps.csv`, `probe.log` and `derivation.json`. The matrix refuses to start without the derivation. The original selected amplitude, 69, is a model-derived parameter, not measured maximum capacity.

Before tuning against a new measurement, repeat the same measurement at least three times unchanged and confirm that variance is smaller than the effect being measured. Preserve each probe directory before another invocation because the script reuses its output path. The publication retains only one original sweep, so it does not demonstrate calibration repeatability. Do not adjust parameters simply to recover the old headline.

Start with one flash repetition, then resume to complete the flash scope:

```bash
bash scripts/run_phase5_matrix.sh --env-file .env --shapes wc98_flash --max-reps 1
bash scripts/run_phase5_matrix.sh --env-file .env --shapes wc98_flash --resume
```

For the remaining scope actually retained in v1.1:

```bash
HPA_NO_SCALE_POLICY=warn bash scripts/run_phase5_matrix.sh --env-file .env \
  --shapes wc98_ramp --max-reps 2
bash scripts/run_phase5_matrix.sh --env-file .env \
  --shapes wc98_constant,wc98_periodic,rr_periodic --max-reps 1
```

The ramp warning policy allows a non-scaling arm to remain a descriptive observation and is recorded as an override. Without it, the default ramp policy aborts a non-scaling HPA arm. The default full matrix requests six flash repetitions and three for every other shape, which exceeds the retained publication scope. The arm order is tuned, stock, fixed; it is not randomized.

The runner owns load-target reachability checks, explicit run times, wall-clock guards, per-arm warm-up and artifact checks. Measured Locust invocations use `--headless --csv <base> --csv-full-history --exit-code-on-error 0`; they do not take `--users`, `--spawn-rate` or `--processes`. Stats plus shape completion determine measured-arm success. Warm-up uses a constant minimum load and validates nonzero request counts without a shape-completion requirement.

### Validate new output and retain evidence

New output goes under gitignored `results/runs/`. Inspect each arm's `STATUS`, measured-shape completion, Locust stats, anchored replica series and collector coverage/isolation output. Use the [guard map](docs/MEASUREMENT_GUARDS.md). A status marker alone is insufficient proof of a publishable comparison. Preserve logs and deployment metadata before tearing down resources.

The general [aggregator](analysis/aggregate_runs.py) supports experiment analysis but does not apply the v1.1 publication exclusions. The [curation script](scripts/build_public_evidence.py) is specific to the retained publication and its local source inventory. It is not a generic importer for arbitrary new runs. A new measurement set needs its own reviewed inventory, exclusions and publication scope; do not overwrite the frozen v1.1 evidence.

Plan cleanup after saving output. Explicitly verify the expected project and cluster before any destructive cloud command, and inspect disks, addresses and load-balancer resources afterward. The helper named `destructive_gke_teardown` validates identity and logs authorization; it does not delete cloud resources for you.
