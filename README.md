# k8s-hpa-benchmark

Personal benchmark project that evaluates Kubernetes Horizontal Pod Autoscaler (HPA) behavior on bursty traffic patterns.

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
- **HPA policy**: dynamic 1-10 replicas, CPU target 60%

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
- `sample_data/` captured metrics and generated figures
- `scripts/` local and GKE deployment + experiment orchestration

## Quick Start (Local, Minikube)

```bash
pip install locust numpy matplotlib
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
locust -f locust/locustfile.py --host "$HPA_HOST" --headless --run-time 18m
```

Collect and analyze metrics:

```bash
kubectl port-forward svc/prometheus 9090:9090 -n hpa-eval
python3 analysis/collect_metrics.py --mode fixed --prometheus-url http://localhost:9090
python3 analysis/collect_metrics.py --mode hpa --prometheus-url http://localhost:9090
python3 analysis/analyze_results.py
```

## Full GKE Run

```bash
bash scripts/deploy_gke.sh YOUR_PROJECT_ID us-central1
bash scripts/run_experiment.sh
```

## Results (Current)

From the benchmark runs in `sample_data/`:

- Failure rate improved from **51.7%** (fixed) to **0.97%** (HPA)
- Total requests served increased by **2.5x**
- Cost per 1k successful requests reduced by **~74%**

After running `analysis/analyze_results.py`, figures are written to:

- `sample_data/figures/latency_comparison.png`
- `sample_data/figures/throughput_comparison.png`
- `sample_data/figures/cpu_replicas.png`
- `sample_data/figures/cost_performance.png`

## Reproducible Demo Path

If you do not want to provision a cluster immediately, generate synthetic benchmark data and plots:

```bash
python3 analysis/simulate_results.py
python3 analysis/analyze_results.py
```
