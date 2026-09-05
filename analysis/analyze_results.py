"""
Reads fixed_metrics.csv and hpa_metrics.csv and generates publication-quality figures.

Outputs 4 PNG plots to sample_data/figures/:
  1. latency_comparison.png   — p50/p95/p99 over time, Fixed vs HPA
  2. throughput_comparison.png — RPS over time
  3. cpu_replicas.png          — CPU utilization + replica count (HPA only)
  4. cost_performance.png      — Pod-hours × cost bar chart

Also prints a statistical summary table.

Usage:
  python3 analysis/analyze_results.py
  # or with custom paths:
  python3 analysis/analyze_results.py --fixed path/to/fixed.csv --hpa path/to/hpa.csv
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
from pathlib import Path

_ANALYSIS_DIR = Path(__file__).resolve().parent
if str(_ANALYSIS_DIR) not in sys.path:
    sys.path.insert(0, str(_ANALYSIS_DIR))

from metrics_contract import (
    MISSING,
    REQUIRED_VALUE_COLUMNS,
    TARGET_UNAVAILABLE,
    assert_column_coverage,
    infer_declared_replicas,
)

# Plotting deps load after guard_inputs in main() so publication guards run without numpy/matplotlib.
np = None  # type: ignore[assignment]
plt = None  # type: ignore[assignment]
mpatches = None  # type: ignore[assignment]


def _import_plot_deps() -> None:
    global np, plt, mpatches
    if np is not None:
        return
    import numpy as _np
    import matplotlib as _matplotlib

    _matplotlib.use("Agg")
    import matplotlib.pyplot as _plt
    import matplotlib.patches as _mpatches

    np = _np
    plt = _plt
    mpatches = _mpatches

# ---------------------------------------------------------------------------
# Colors and style
# ---------------------------------------------------------------------------
COLORS = {
    "fixed_p50":  "#2166ac",
    "fixed_p95":  "#4393c3",
    "fixed_p99":  "#92c5de",
    "hpa_p50":    "#d6604d",
    "hpa_p95":    "#f4a582",
    "hpa_p99":    "#fddbc7",
    "hpa_cpu":    "#e08214",
    "hpa_rep":    "#d6604d",
    "users":      "#666666",
}

PHASE_BOUNDARIES = [0, 180, 360, 900, 1080]
PHASE_LABELS     = ["Ramp-up", "Spike", "Sustained", "Recovery"]
PHASE_COLORS     = ["#f7f7f7", "#fee8c8", "#edf8e9", "#deebf7"]

FIG_DPI = 150

# GKE e2-standard-2 on-demand list price (us-central1, 2026-03 public pricing).
# Pod requests 0.1 vCPU on a 2-vCPU node → proportional node-cost share.
GKE_E2_STANDARD_2_USD_PER_HOUR = 0.0535
POD_VCPU_REQUEST = 0.1
NODE_VCPU_CAPACITY = 2.0
LIST_PRICE_PER_POD_HOUR = GKE_E2_STANDARD_2_USD_PER_HOUR * (POD_VCPU_REQUEST / NODE_VCPU_CAPACITY)


def phase_bands(ax, max_y: float):
    """Draw colored phase background bands."""
    for i, (start, end) in enumerate(zip(PHASE_BOUNDARIES, PHASE_BOUNDARIES[1:])):
        ax.axvspan(start, end, alpha=0.15, color=PHASE_COLORS[i], zorder=0)
        mid = (start + end) / 2
        ax.text(mid, max_y * 0.97, PHASE_LABELS[i],
                ha="center", va="top", fontsize=7, color="#555555",
                style="italic")


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_csv(path: str) -> list[dict]:
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        rows = []
        for row in reader:
            converted = {}
            for k, v in row.items():
                try:
                    converted[k] = float(v) if v != "" else None
                except ValueError:
                    converted[k] = v
            rows.append(converted)
    return rows


def extract(rows: list[dict], key: str):
    t = np.array([r["elapsed_seconds"] for r in rows])
    values = []
    for r in rows:
        val = r.get(key)
        if val in (None, "", "MISSING", "TARGET_UNAVAILABLE"):
            values.append(np.nan)
        else:
            try:
                values.append(float(val))
            except (TypeError, ValueError):
                values.append(np.nan)
    v = np.array(values)
    return t, v


def finite_xy(t: np.ndarray, v: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    mask = np.isfinite(t) & np.isfinite(v)
    return t[mask], v[mask]


def safe_ymax(values: np.ndarray, default: float, *, pad: float = 1.15) -> float:
    finite = values[np.isfinite(values)]
    if finite.size == 0:
        return default
    return float(np.max(finite) * pad)


def save_figure(fig, path: Path) -> None:
    fig.savefig(path, dpi=FIG_DPI, facecolor="white")
    plt.close(fig)
    print(f"  Saved {path}")


def parse_iso8601(value: str) -> float:
    from datetime import datetime

    return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()


def pod_hours_from_replica_series(path: str) -> float:
    """Integrate ready_replicas over time: sum(ready_i * delta_t) / 3600."""
    samples: list[tuple[float, int]] = []
    with open(path, newline="") as handle:
        for row in csv.DictReader(handle):
            samples.append((parse_iso8601(row["timestamp"]), int(row["ready_replicas"])))
    if len(samples) < 2:
        raise RuntimeError(f"REPLICA_SERIES_TOO_SHORT path={path} samples={len(samples)}")
    samples.sort(key=lambda item: item[0])
    pod_seconds = 0.0
    for index in range(len(samples) - 1):
        ts, ready = samples[index]
        next_ts, _ = samples[index + 1]
        pod_seconds += ready * (next_ts - ts)
    return pod_seconds / 3600.0


def successful_requests_from_locust_stats(path: str) -> int:
    with open(path, newline="") as handle:
        for row in csv.DictReader(handle):
            if row.get("Name") != "Aggregated":
                continue
            request_count = int(row["Request Count"])
            failure_count = int(row["Failure Count"])
            return request_count - failure_count
    raise RuntimeError(f"LOCUST_STATS_MISSING_AGGREGATED path={path}")


# ---------------------------------------------------------------------------
# Figure 1 — Latency Comparison
# ---------------------------------------------------------------------------

def fig_latency(fixed: list[dict], hpa: list[dict], out_dir: Path):
    fig, axes = plt.subplots(1, 2, figsize=(14, 5), sharey=True)
    fig.suptitle("Response Latency Over Time: Fixed vs HPA", fontsize=13, fontweight="bold")

    max_y = 1100

    for ax, rows, title, prefix in [
        (axes[0], fixed, "Fixed Deployment (3 replicas)", "fixed"),
        (axes[1], hpa,   "HPA Deployment (1–10 replicas)", "hpa"),
    ]:
        t, p50 = extract(rows, "latency_p50_ms")
        _, p95  = extract(rows, "latency_p95_ms")
        _, p99  = extract(rows, "latency_p99_ms")

        phase_bands(ax, max_y)
        for series, lw, alpha, color, label in [
            (p99, 1.5, 0.6, COLORS[f"{prefix}_p99"], "p99"),
            (p95, 2.0, 0.8, COLORS[f"{prefix}_p95"], "p95"),
            (p50, 2.5, 1.0, COLORS[f"{prefix}_p50"], "p50"),
        ]:
            tx, vx = finite_xy(t, series)
            ax.plot(tx / 60, vx, lw=lw, alpha=alpha, color=color, label=label)

        ax.set_title(title, fontsize=11)
        ax.set_xlabel("Time (minutes)", fontsize=10)
        ax.set_ylabel("Latency (ms)", fontsize=10) if ax == axes[0] else None
        ax.legend(loc="upper left", fontsize=9)
        ax.set_xlim(0, 18)
        ax.set_ylim(0, max_y)
        ax.grid(True, alpha=0.3)

    plt.tight_layout()
    save_figure(fig, out_dir / "latency_comparison.png")


# ---------------------------------------------------------------------------
# Figure 2 — Throughput Comparison
# ---------------------------------------------------------------------------

def fig_throughput(fixed: list[dict], hpa: list[dict], out_dir: Path):
    fig, ax = plt.subplots(figsize=(12, 4))
    ax.set_title("Request Throughput (RPS) Over Time: Fixed vs HPA", fontsize=13, fontweight="bold")

    tf, rps_f = extract(fixed, "rps")
    th, rps_h = extract(hpa,   "rps")

    max_y = max(safe_ymax(rps_f, 10.0), safe_ymax(rps_h, 10.0))
    phase_bands(ax, max_y)

    tx, vx = finite_xy(tf, rps_f)
    ax.plot(tx / 60, vx, lw=2.5, color=COLORS["fixed_p50"], label="Fixed (3 replicas)")
    tx, vx = finite_xy(th, rps_h)
    ax.plot(tx / 60, vx, lw=2.5, color=COLORS["hpa_p50"],   label="HPA (1–10 replicas)")

    ax.set_xlabel("Time (minutes)", fontsize=10)
    ax.set_ylabel("Requests per Second", fontsize=10)
    ax.legend(fontsize=10)
    ax.set_xlim(0, 18)
    ax.set_ylim(0, max_y)
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    save_figure(fig, out_dir / "throughput_comparison.png")


# ---------------------------------------------------------------------------
# Figure 3 — CPU Utilization + Replica Count
# ---------------------------------------------------------------------------

def fig_cpu_replicas(hpa: list[dict], out_dir: Path):
    fig, ax1 = plt.subplots(figsize=(12, 5))
    ax2 = ax1.twinx()

    ax1.set_title("HPA Scaling Behavior: CPU Utilization vs Replica Count", fontsize=13, fontweight="bold")

    t, cpu  = extract(hpa, "cpu_utilization_pct")
    _, reps = extract(hpa, "replicas")

    max_cpu = 110
    phase_bands(ax1, max_cpu)

    tx, vx = finite_xy(t, cpu)
    ax1.plot(tx / 60, vx, lw=2.0, color=COLORS["hpa_cpu"], label="CPU Utilization (%)")
    ax1.axhline(60, color=COLORS["hpa_cpu"], lw=1.0, linestyle="--", alpha=0.6, label="HPA Target (60%)")
    tx, vx = finite_xy(t, reps)
    ax2.step(tx / 60, vx, lw=2.5, color=COLORS["hpa_rep"], where="post", label="Replica Count")

    ax1.set_xlabel("Time (minutes)", fontsize=10)
    ax1.set_ylabel("CPU Utilization (%)", color=COLORS["hpa_cpu"], fontsize=10)
    ax2.set_ylabel("Replica Count", color=COLORS["hpa_rep"], fontsize=10)
    ax1.set_xlim(0, 18)
    ax1.set_ylim(0, max_cpu)
    ax2.set_ylim(0, 12)
    ax1.grid(True, alpha=0.3)

    lines1, labels1 = ax1.get_legend_handles_labels()
    lines2, labels2 = ax2.get_legend_handles_labels()
    ax1.legend(lines1 + lines2, labels1 + labels2, loc="upper left", fontsize=9)

    plt.tight_layout()
    save_figure(fig, out_dir / "cpu_replicas.png")


# ---------------------------------------------------------------------------
# Figure 4 — Cost-Performance Bar Chart
# ---------------------------------------------------------------------------

def fig_cost_performance(
    fixed: list[dict],
    hpa: list[dict],
    out_dir: Path,
    *,
    replica_series_fixed: str,
    replica_series_hpa: str,
    locust_fixed_stats: str,
    locust_hpa_stats: str,
) -> dict[str, float]:
    fixed_pod_hours = pod_hours_from_replica_series(replica_series_fixed)
    hpa_pod_hours = pod_hours_from_replica_series(replica_series_hpa)
    fixed_successful = successful_requests_from_locust_stats(locust_fixed_stats)
    hpa_successful = successful_requests_from_locust_stats(locust_hpa_stats)

    fixed_total_cost = fixed_pod_hours * LIST_PRICE_PER_POD_HOUR
    hpa_total_cost = hpa_pod_hours * LIST_PRICE_PER_POD_HOUR
    cost_per_kreq_fixed = fixed_total_cost / (fixed_successful / 1000)
    cost_per_kreq_hpa = hpa_total_cost / (hpa_successful / 1000)

    _, p95_f = extract(fixed, "latency_p95_ms")
    _, p95_h = extract(hpa, "latency_p95_ms")
    mean_p95_fixed = float(np.nanmean(p95_f))
    mean_p95_hpa = float(np.nanmean(p95_h))

    print(
        "COST_PER_1K_SUCCESSFUL "
        f"formula=(pod_hours*list_price_per_pod_hour)/(successful_requests/1000) "
        f"list_price_per_pod_hour={LIST_PRICE_PER_POD_HOUR:.6f} "
        f"(GKE e2-standard-2 ${GKE_E2_STANDARD_2_USD_PER_HOUR}/hr * "
        f"{POD_VCPU_REQUEST}/{NODE_VCPU_CAPACITY} vCPU share)"
    )
    print(
        f"  fixed pod_hours={fixed_pod_hours:.4f} "
        f"successful={fixed_successful} "
        f"cost_per_1k=${cost_per_kreq_fixed:.6f} "
        f"source=replica_series+locust_fixed_stats"
    )
    print(
        f"  hpa pod_hours={hpa_pod_hours:.4f} "
        f"successful={hpa_successful} "
        f"cost_per_1k=${cost_per_kreq_hpa:.6f} "
        f"source=replica_series+locust_hpa_stats"
    )

    fig, axes = plt.subplots(1, 3, figsize=(13, 5))
    fig.suptitle(
        "Cost vs Performance Summary\n"
        "HPA: higher cost per successful request; lower failure rate (reliability premium)",
        fontsize=12,
        fontweight="bold",
    )

    bar_kw = dict(width=0.5, edgecolor="black", linewidth=0.7)
    experiments = ["Fixed", "HPA"]
    x = [0, 1]

    axes[0].bar(x, [fixed_pod_hours, hpa_pod_hours], color=[COLORS["fixed_p50"], COLORS["hpa_p50"]], **bar_kw)
    axes[0].set_xticks(x)
    axes[0].set_xticklabels(experiments)
    axes[0].set_title("Pod-Hours Used\n(∫ ready_replicas dt from replica_series)", fontsize=10)
    axes[0].set_ylabel("Pod-hours")
    axes[0].grid(True, axis="y", alpha=0.3)
    for xi, v in zip(x, [fixed_pod_hours, hpa_pod_hours]):
        axes[0].text(xi, v + 0.02, f"{v:.3f}", ha="center", fontsize=9)

    axes[1].bar(x, [mean_p95_fixed, mean_p95_hpa], color=[COLORS["fixed_p50"], COLORS["hpa_p50"]], **bar_kw)
    axes[1].set_xticks(x)
    axes[1].set_xticklabels(experiments)
    axes[1].set_title("Mean p95 Latency (ms)\n(Prometheus, populated rows only)", fontsize=10)
    axes[1].set_ylabel("Milliseconds")
    axes[1].grid(True, axis="y", alpha=0.3)
    for xi, v in zip(x, [mean_p95_fixed, mean_p95_hpa]):
        axes[1].text(xi, v + 5, f"{v:.0f}ms", ha="center", fontsize=9)

    axes[2].bar(x, [cost_per_kreq_fixed, cost_per_kreq_hpa], color=[COLORS["fixed_p50"], COLORS["hpa_p50"]], **bar_kw)
    axes[2].set_xticks(x)
    axes[2].set_xticklabels(experiments)
    axes[2].set_title("Cost per 1k Successful Requests ($)\n(Locust successes in denominator)", fontsize=10)
    axes[2].set_ylabel("USD")
    axes[2].grid(True, axis="y", alpha=0.3)
    for xi, v in zip(x, [cost_per_kreq_fixed, cost_per_kreq_hpa]):
        axes[2].text(xi, v * 1.05, f"${v:.5f}", ha="center", fontsize=9)

    plt.tight_layout(rect=(0, 0, 1, 0.92))
    save_figure(fig, out_dir / "cost_performance.png")

    return {
        "fixed_pod_hours": fixed_pod_hours,
        "hpa_pod_hours": hpa_pod_hours,
        "fixed_successful": float(fixed_successful),
        "hpa_successful": float(hpa_successful),
        "cost_per_kreq_fixed": cost_per_kreq_fixed,
        "cost_per_kreq_hpa": cost_per_kreq_hpa,
        "list_price_per_pod_hour": LIST_PRICE_PER_POD_HOUR,
    }


# ---------------------------------------------------------------------------
# Statistical summary table
# ---------------------------------------------------------------------------

def print_summary(fixed: list[dict], hpa: list[dict]):
    metrics = [
        ("latency_p50_ms", "Latency p50 (ms)"),
        ("latency_p95_ms", "Latency p95 (ms)"),
        ("latency_p99_ms", "Latency p99 (ms)"),
        ("rps",            "Throughput (RPS)"),
        ("cpu_utilization_pct", "CPU Util (%)"),
        ("replicas",       "Replica Count"),
        ("error_rate",     "Error Rate"),
    ]
    header = f"{'Metric':<25} {'Fixed Mean':>12} {'Fixed Std':>10} {'HPA Mean':>12} {'HPA Std':>10} {'Δ%':>8}"
    print("\n" + "=" * len(header))
    print("STATISTICAL SUMMARY")
    print("=" * len(header))
    print(header)
    print("-" * len(header))

    for key, label in metrics:
        _, vf = extract(fixed, key)
        _, vh = extract(hpa,   key)
        mf, sf = float(np.nanmean(vf)), float(np.nanstd(vf))
        mh, sh = float(np.nanmean(vh)), float(np.nanstd(vh))
        delta = (mh - mf) / mf * 100 if mf != 0 else 0
        sign = "+" if delta > 0 else ""
        print(f"{label:<25} {mf:>12.2f} {sf:>10.2f} {mh:>12.2f} {sh:>10.2f} {sign}{delta:>7.1f}%")
    print("=" * len(header))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def guard_inputs(
    fixed_path: str,
    hpa_path: str,
    locust_hpa_stats: str | None = None,
    allow_synthetic: bool = False,
    allow_partial_coverage: bool = False,
) -> None:
    for path in [fixed_path, hpa_path]:
        if not os.path.exists(path):
            print(f"ERROR: required metrics file missing: {path}", file=sys.stderr)
            sys.exit(1)

    if locust_hpa_stats and not os.path.exists(locust_hpa_stats):
        print("ASSERTION FAILED: publication blocked; locust_hpa_stats.csv is absent", file=sys.stderr)
        sys.exit(1)

    for path in [fixed_path, hpa_path]:
        with open(path, newline="") as handle:
            reader = csv.DictReader(handle)
            rows = list(reader)
            if not rows:
                print(f"ERROR: empty metrics file: {path}", file=sys.stderr)
                sys.exit(1)
            if not allow_synthetic and "data_source" in rows[0] and any(r.get("data_source") == "SYNTHETIC" for r in rows):
                print("ERROR: synthetic data detected; pass --allow-synthetic to analyze", file=sys.stderr)
                sys.exit(1)
            if allow_partial_coverage:
                continue
            try:
                declared = infer_declared_replicas(rows)
                assert_column_coverage(rows, label=f"path={path}", declared_replicas=declared)
            except RuntimeError as exc:
                print(str(exc), file=sys.stderr)
                sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="Analyze HPA evaluation results")
    base_dir = Path(__file__).parent.parent / "sample_data"
    parser.add_argument("--fixed", default=str(base_dir / "fixed_metrics.csv"))
    parser.add_argument("--hpa",   default=str(base_dir / "hpa_metrics.csv"))
    parser.add_argument("--output-dir", default=None)
    parser.add_argument("--locust-hpa-stats", default=None)
    parser.add_argument("--locust-fixed-stats", default=None)
    parser.add_argument("--replica-series-fixed", default=None)
    parser.add_argument("--replica-series-hpa", default=None)
    parser.add_argument("--allow-synthetic", action="store_true")
    parser.add_argument("--allow-partial-coverage", action="store_true")
    args = parser.parse_args()

    guard_inputs(
        args.fixed,
        args.hpa,
        args.locust_hpa_stats,
        args.allow_synthetic,
        args.allow_partial_coverage,
    )
    _import_plot_deps()

    fixed = load_csv(args.fixed)
    hpa   = load_csv(args.hpa)
    print(f"Loaded: {len(fixed)} fixed rows, {len(hpa)} HPA rows")

    fixed_dir = Path(args.fixed).parent
    replica_series_fixed = args.replica_series_fixed or str(fixed_dir / "replica_series_fixed.csv")
    replica_series_hpa = args.replica_series_hpa or str(Path(args.hpa).parent / "replica_series_hpa.csv")
    locust_fixed_stats = args.locust_fixed_stats or str(fixed_dir / "locust_fixed_stats.csv")
    locust_hpa_stats = args.locust_hpa_stats or str(Path(args.hpa).parent / "locust_hpa_stats.csv")
    for path in [replica_series_fixed, replica_series_hpa, locust_fixed_stats, locust_hpa_stats]:
        if not os.path.exists(path):
            print(f"ERROR: required cost input missing: {path}", file=sys.stderr)
            sys.exit(1)

    out_dir = Path(args.output_dir) if args.output_dir else fixed_dir / "figures"
    out_dir.mkdir(parents=True, exist_ok=True)

    print("Generating figures...")
    fig_latency(fixed, hpa, out_dir)
    fig_throughput(fixed, hpa, out_dir)
    fig_cpu_replicas(hpa, out_dir)
    fig_cost_performance(
        fixed,
        hpa,
        out_dir,
        replica_series_fixed=replica_series_fixed,
        replica_series_hpa=replica_series_hpa,
        locust_fixed_stats=locust_fixed_stats,
        locust_hpa_stats=locust_hpa_stats,
    )

    print_summary(fixed, hpa)
    print(f"\nAll figures saved to {out_dir}/")


if __name__ == "__main__":
    main()
