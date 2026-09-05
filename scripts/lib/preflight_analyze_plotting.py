#!/usr/bin/env python3
"""Preflight: exercise analyze_results plotting on fixture CSVs (import-only is insufficient)."""

from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures"
FIXTURE_FIXED = FIXTURE_DIR / "preflight_fixed_metrics.csv"
FIXTURE_HPA = FIXTURE_DIR / "preflight_hpa_metrics.csv"
EXPECTED_FIGURES = (
    "latency_comparison.png",
    "throughput_comparison.png",
    "cpu_replicas.png",
    "cost_performance.png",
)


def _load_analyze_results():
    path = REPO_ROOT / "analysis" / "analyze_results.py"
    spec = importlib.util.spec_from_file_location("analyze_results", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"spec load failed for {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    for fixture in (FIXTURE_FIXED, FIXTURE_HPA):
        if not fixture.is_file():
            print(
                f"ERROR: analyze plotting fixture missing: {fixture}",
                file=sys.stderr,
            )
            return 1

    out_dir = Path(tempfile.mkdtemp(prefix="preflight-analyze-figures-"))
    missing: list[str] = []
    try:
        analyze = _load_analyze_results()
        analyze.guard_inputs(
            str(FIXTURE_FIXED),
            str(FIXTURE_HPA),
        )
        analyze._import_plot_deps()
        fixed = analyze.load_csv(str(FIXTURE_FIXED))
        hpa = analyze.load_csv(str(FIXTURE_HPA))
        analyze.fig_latency(fixed, hpa, out_dir)
        analyze.fig_throughput(fixed, hpa, out_dir)
        analyze.fig_cpu_replicas(hpa, out_dir)
        analyze.fig_cost_performance(fixed, hpa, out_dir)
        missing = [name for name in EXPECTED_FIGURES if not (out_dir / name).is_file()]
    except Exception as exc:  # noqa: BLE001 - surface plotting failure to operator
        print(f"ERROR: analyze_results plotting preflight failed: {exc}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(out_dir, ignore_errors=True)

    if missing:
        print(
            f"ERROR: analyze plotting preflight missing figures: {', '.join(missing)}",
            file=sys.stderr,
        )
        return 1

    print(f"analyze_plotting=PASS figures={len(EXPECTED_FIGURES)} fixture_dir={FIXTURE_FIXED.parent}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
