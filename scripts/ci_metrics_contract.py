#!/usr/bin/env python3
"""Run the metrics CSV contract against preflight fixtures. No cluster."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "analysis"))

from analyze_results import guard_inputs  # noqa: E402


def main() -> int:
    fixtures = REPO_ROOT / "scripts" / "lib" / "fixtures"
    guard_inputs(
        str(fixtures / "preflight_fixed_metrics.csv"),
        str(fixtures / "preflight_hpa_metrics.csv"),
    )
    print("METRICS_CONTRACT_FIXTURES_PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
