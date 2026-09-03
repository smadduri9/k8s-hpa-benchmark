#!/usr/bin/env python3
"""Preflight: enforce minimum Python and import all analysis dependencies."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

MIN_PYTHON = (3, 14)
REPO_ROOT = Path(__file__).resolve().parents[2]
ANALYSIS_MODULES = (
    "collect_metrics",
    "ingest_locust",
    "analyze_results",
    "fill_results",
)


def main() -> int:
    if sys.version_info < MIN_PYTHON:
        print(
            f"ERROR: Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]}+ required; "
            f"found {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            file=sys.stderr,
        )
        return 1

    print(
        f"python_version=PASS "
        f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    )

    for name in ANALYSIS_MODULES:
        path = REPO_ROOT / "analysis" / f"{name}.py"
        if not path.is_file():
            print(f"ERROR: missing analysis module: {path}", file=sys.stderr)
            return 1
        try:
            spec = importlib.util.spec_from_file_location(name, path)
            if spec is None or spec.loader is None:
                raise ImportError("spec load failed")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        except Exception as exc:  # noqa: BLE001 - report module name to operator
            print(f"ERROR: analysis import failed module={name}: {exc}", file=sys.stderr)
            return 1
        print(f"analysis_import=PASS module={name}")

    for pkg in ("numpy", "matplotlib"):
        try:
            mod = importlib.import_module(pkg)
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR: python package import failed package={pkg}: {exc}", file=sys.stderr)
            return 1
        version = getattr(mod, "__version__", "unknown")
        print(f"python_package=PASS package={pkg} version={version}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
