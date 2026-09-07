# k8s-hpa-benchmark

Personal benchmark project that evaluates Kubernetes Horizontal Pod Autoscaler (HPA) behavior on bursty traffic patterns.

## Calibrated results (minReplicas=3 both arms)

Both arms started at equal capacity (`minReplicas=3`). **Aggregate figures are pending** — per-rep charts are not published here. Full write-up: [RESULTS.md](RESULTS.md#calibrated-results-minreplicas3-both-arms). SLO and error-budget analysis: [RESULTS.md § SLO](RESULTS.md#slo-and-error-budget-calibrated-runs); incident write-up: [POSTMORTEM.md](POSTMORTEM.md); policy: [docs/error-budget-policy.md](docs/error-budget-policy.md).

### hybrid — `run-20260905T220046Z-hybrid` (n=6)

| Metric | Fixed (median) | HPA (median) | HPA slower | p (two-sided) |
|--------|---------------:|-------------:|-----------:|--------------:|
| client_p50_ms | 895 | 370 | 0/6 | 0.031250 |
| client_p95_ms | 3250 | 1950 | 0/6 | 0.062500 (n=5, one tie) |
| client_p99_ms | 4200 | 2800 | 1/6 | 0.062500 |
| failure_rate | 0.000124 | 0.000163 | — | 0.562500 (not significant) |
| pod_hours | 0.890417 | 2.493750 | — | 0.031250 |
| cost_per_1k | 0.000147246 | 0.000339106 | — | 0.031250 |

`P_FLOOR n=6 min_attainable_two_sided_p=0.031250` — p=0.031250 is the floor at n=6, not a finer significance claim.

### constant — `run-20260906T050515Z-constant` (n=3)

| Metric | Fixed (median) | HPA (median) | HPA slower | p (two-sided) |
|--------|---------------:|-------------:|-----------:|--------------:|
| client_p50_ms | 450 | 270 | 0/3 | 0.250000 |
| client_p95_ms | 1500 | 1100 | 0/3 | 0.250000 |
| client_p99_ms | 2100 | 1600 | 0/3 | 0.250000 |
| failure_rate | 0 | 0.00010008 | — | 0.250000 |
| pod_hours | 0.891667 | 2.723060 | — | 0.250000 |
| cost_per_1k | 0.000125359 | 0.000363458 | — | 0.250000 |

`P_FLOOR n=3 min_attainable_two_sided_p=0.250000` — every p-value in this table is the floor at n=3; no row can reach significance by construction.

### flash — `run-20260906T201803Z-flash` (n=2 of 3)

**STATUS: PARTIAL** — 2 of 3 repetitions executed; rep-3 never started. rep-2's Prometheus-derived cells are all `MISSING` because the TSDB was wiped before recovery. Locust data is valid. No aggregates published in this pass.

## Superseded — run-20260904T230444Z

This was the published headline. It is not a fair comparison: HPA ran at `minReplicas=1` while the fixed arm was declared at 3. Superseded by the calibrated minReplicas=3 runs above. Table and figures preserved verbatim.

**Status: PARTIAL** — fixed arm collapsed under burst; metrics gaps are measured, not hidden.

| Arm | Requests | Failures | Failure rate | Client p50 / p95 / p99 (ms) | Replicas | Source |
|-----|---------:|---------:|-------------:|----------------------------|----------|--------|
| **Fixed** (declared 3) | 10,193 | 1,230 | **12.07%** | **1,200 / 21,000 / 40,000** (12.07% failures) | ready hit **0** during collapse | `locust_fixed_stats.csv` |
| **HPA** (1–10) | 20,820 | 63 | **0.30%** | **310 / 1,300 / 2,200** (0.30% failures) | peak **spec=10**, peak **ready=10** | `locust_hpa_stats.csv` |

Client-observed response time includes queueing, connection setup, and failures (Locust). Prometheus in-handler service time (~239 ms mean p95) is a separate metric — see [RESULTS.md](RESULTS.md#latency--two-metrics-never-merged).

Fixed availability (73 rows): **14 UNAVAILABLE** / **40 DEGRADED** / **19 AVAILABLE**. HPA successful-request throughput **2.32×** fixed (20757 ÷ 8963). **Cost:** HPA **$0.000311** vs fixed **$0.000133** per 1k successful requests (**2.33×** premium) — reliability (0.30% vs 12.07% failures) at higher compute cost per success.

### Client-observed response time (Locust, run-level)

![Client-observed response time — run-level p50/p95/p99](docs/figures/run-20260904T230444Z/latency_client_run_level.png)

### Client-observed response time (10-second sliding window)

![Client-observed response time over time](docs/figures/run-20260904T230444Z/latency_client_window.png)

### Service time (in-handler, Prometheus)

![Service time — in-handler compute duration](docs/figures/run-20260904T230444Z/latency_comparison.png)

### Throughput (RPS)

![Throughput comparison](docs/figures/run-20260904T230444Z/throughput_comparison.png)

### CPU and replica count (HPA arm)

![CPU and replicas](docs/figures/run-20260904T230444Z/cpu_replicas.png)

### Cost vs performance

![Cost performance](docs/figures/run-20260904T230444Z/cost_performance.png)

Paths above are relative to repo root. Run artifacts live under `results/runs/run-20260904T230444Z/rep-1/` (gitignored locally).

---

## Quick Start

See [HANDOFF.md](HANDOFF.md) for the full reproducible runbook.

**Smoke validation (kind, not performance-comparable to GKE):**
```bash
bash scripts/smoke_test.sh --check harness
bash scripts/smoke_test.sh --full
```

**GKE benchmark:**
```bash
bash scripts/preflight.sh --env-file .env --require-gke
nohup bash scripts/run_benchmark.sh --env-file .env --repetitions 1 > results/latest.nohup.log 2>&1 &
```

Prior committed artifacts were superseded to `superseded/sample_data-2026-03/` (see `DATA_PROVENANCE.md`).

## Overview

This project compares two deployment strategies for the same FastAPI workload:

- **Fixed baseline**: static 3 replicas
- **HPA policy**: dynamic 3–10 replicas, CPU target 60%

The benchmark measures reliability, latency, throughput, scaling behavior, and cost efficiency.

## Stack

- Kubernetes + HPA (`autoscaling/v2`)
- FastAPI workload service
- Locust phased load test
- Prometheus metrics collection
- Python analysis pipeline (NumPy + Matplotlib)
- GKE and Minikube deployment scripts

## Repository Layout

- `app/` FastAPI service and Docker image definition
- `k8s/` namespace, deployments, services, HPA, Prometheus manifests
- `locust/` workload generator with phased traffic shape
- `analysis/` metric collection and report plotting scripts
- `docs/` published figures and investigation tables for completed runs
- `scripts/` local and GKE deployment + experiment orchestration

## Tooling setup (required)

All analysis and Locust commands use the repo virtualenv — do not install tooling globally.

```bash
python3 -m venv .venv
".venv/bin/python" -m pip install -r requirements-tooling.txt
bash scripts/preflight.sh
```

## Quick Start (Local, Minikube)

```bash
bash scripts/preflight.sh
bash scripts/deploy_local.sh
```

Get service endpoint:

```bash
MINIKUBE_IP=$(minikube ip)
HPA_PORT=$(kubectl get svc hpa-eval-hpa-svc -n hpa-eval -o jsonpath='{.spec.ports[0].nodePort}')
export HPA_HOST="http://${MINIKUBE_IP}:${HPA_PORT}"
```

Run phased load test:

```bash
".venv/bin/locust" -f locust/locustfile.py --host "$HPA_HOST" --headless --run-time 18m
```

Collect and analyze metrics:

```bash
kubectl port-forward svc/prometheus 9090:9090 -n hpa-eval
".venv/bin/python" analysis/collect_metrics.py --mode fixed --prometheus-url http://localhost:9090
".venv/bin/python" analysis/collect_metrics.py --mode hpa --prometheus-url http://localhost:9090
".venv/bin/python" analysis/analyze_results.py
```

## Full GKE Run

```bash
bash scripts/deploy_gke.sh YOUR_PROJECT_ID us-central1
bash scripts/run_experiment.sh
```

## Reproducible Demo Path

If you do not want to provision a cluster immediately, generate synthetic benchmark data and plots:

```bash
".venv/bin/python" synthetic/generate_synthetic_data.py
".venv/bin/python" analysis/analyze_results.py
```

Synthetic data is quarantined and not comparable to measured GKE runs — see `DATA_PROVENANCE.md`.
