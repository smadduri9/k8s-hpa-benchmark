"""
FastAPI application with CPU-intensive endpoints for Kubernetes HPA evaluation.
Exposes Prometheus metrics for monitoring and auto-scaling decisions.
"""

import os
import time
import socket
import math
from typing import Literal

from fastapi import FastAPI, Query
from fastapi.responses import PlainTextResponse
import psutil
from prometheus_client import (
    Counter,
    Histogram,
    Gauge,
    generate_latest,
    CONTENT_TYPE_LATEST,
)

app = FastAPI(title="HPA Evaluation App", version="1.0.0")

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------
REQUEST_COUNT = Counter(
    "app_requests_total",
    "Total number of requests",
    ["method", "endpoint", "status_code"],
)

REQUEST_LATENCY = Histogram(
    "app_request_latency_seconds",
    "Request latency in seconds",
    ["endpoint"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0],
)

ACTIVE_REQUESTS = Gauge(
    "app_active_requests",
    "Number of requests currently being processed",
)

CPU_USAGE = Gauge(
    "app_cpu_usage_percent",
    "Current CPU usage percentage of this process",
)


# ---------------------------------------------------------------------------
# Helper — compute N prime numbers (CPU-bound work)
# ---------------------------------------------------------------------------

INTENSITY_MAP: dict[str, int] = {
    "low": 1_000,
    "medium": 5_000,
    "high": 20_000,
}


def compute_primes(n: int) -> list[int]:
    """Return a list of the first *n* prime numbers using trial division."""
    primes: list[int] = []
    candidate = 2
    while len(primes) < n:
        is_prime = all(candidate % p != 0 for p in primes if p <= math.isqrt(candidate))
        if is_prime:
            primes.append(candidate)
        candidate += 1
    return primes


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
async def root():
    """Lightweight health-check endpoint — returns status and pod hostname."""
    CPU_USAGE.set(psutil.cpu_percent(interval=None))
    return {
        "status": "ok",
        "hostname": socket.gethostname(),
        "service": "hpa-eval-app",
        "version": "1.0.0",
    }


@app.get("/cpu")
async def cpu_load(
    intensity: Literal["low", "medium", "high"] = Query(
        "medium",
        description="Workload intensity: low=1k primes, medium=5k primes, high=20k primes",
    )
):
    """CPU-intensive endpoint that computes prime numbers."""
    n = INTENSITY_MAP[intensity]
    endpoint = "/cpu"

    ACTIVE_REQUESTS.inc()
    start = time.perf_counter()
    try:
        primes = compute_primes(n)
        elapsed = time.perf_counter() - start
        REQUEST_COUNT.labels(method="GET", endpoint=endpoint, status_code=200).inc()
        REQUEST_LATENCY.labels(endpoint=endpoint).observe(elapsed)
        CPU_USAGE.set(psutil.cpu_percent(interval=None))
        return {
            "intensity": intensity,
            "primes_computed": n,
            "largest_prime": primes[-1],
            "elapsed_seconds": round(elapsed, 4),
            "hostname": socket.gethostname(),
        }
    except Exception as exc:
        REQUEST_COUNT.labels(method="GET", endpoint=endpoint, status_code=500).inc()
        raise exc
    finally:
        ACTIVE_REQUESTS.dec()


@app.get("/health")
async def health():
    """Kubernetes liveness and readiness probe endpoint."""
    return {"status": "healthy", "hostname": socket.gethostname()}


@app.get("/metrics", response_class=PlainTextResponse)
async def metrics():
    """Prometheus text exposition endpoint."""
    CPU_USAGE.set(psutil.cpu_percent(interval=None))
    return PlainTextResponse(
        content=generate_latest().decode("utf-8"),
        media_type=CONTENT_TYPE_LATEST,
    )
