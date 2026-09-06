#!/usr/bin/env python3
"""One-off backfill: add active_requests to existing metrics CSVs from live Prometheus.

Reads each rep's anchored window from manifest.json and replica_series for
staleness exclusion. Does not alter any column other than inserting active_requests.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

_ANALYSIS_DIR = Path(__file__).resolve().parent
if str(_ANALYSIS_DIR) not in sys.path:
    sys.path.insert(0, str(_ANALYSIS_DIR))

from collect_metrics import (
    ACTIVE_REQUESTS_STALENESS_LOOKBACK_SEC,
    FIELDNAMES,
    METRIC_QUERY_PREROLL_SEC,
    build_queries,
    classify_metric_value,
    load_replica_series,
    lookup_series_value,
    parse_iso8601,
    query_range,
    ready_replicas_changed_in_lookback,
)
from metrics_contract import (
    AVAILABILITY_UNAVAILABLE,
    MISSING,
    TARGET_UNAVAILABLE,
    is_serving_row,
)

REP_DIR_RE = re.compile(r"^rep-(\d+)$")


def discover_reps(run_root: Path) -> list[tuple[int, Path]]:
    reps: list[tuple[int, Path]] = []
    for child in sorted(run_root.iterdir()):
        if child.is_dir() and (match := REP_DIR_RE.match(child.name)):
            reps.append((int(match.group(1)), child))
    return reps


def arm_from_metrics_path(path: Path) -> str:
    if path.name == "fixed_metrics.csv":
        return "fixed"
    if path.name == "hpa_metrics.csv":
        return "hpa"
    raise ValueError(f"unknown metrics file: {path}")


def insert_column(fieldnames: list[str], column: str, after: str) -> list[str]:
    if column in fieldnames:
        return fieldnames
    if after not in fieldnames:
        return fieldnames + [column]
    index = fieldnames.index(after) + 1
    return fieldnames[:index] + [column] + fieldnames[index:]


def backfill_metrics_csv(
    metrics_path: Path,
    replica_series_path: Path,
    prometheus_url: str,
    *,
    step: int = 15,
) -> dict[str, int]:
    if not metrics_path.is_file():
        raise SystemExit(f"METRICS_CSV_MISSING path={metrics_path}")

    arm = arm_from_metrics_path(metrics_path)
    mode = arm

    with metrics_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise SystemExit(f"METRICS_CSV_INVALID path={metrics_path} reason=no_header")
        rows = list(reader)
        original_fieldnames = list(reader.fieldnames)

    if not rows:
        raise SystemExit(f"METRICS_CSV_EMPTY path={metrics_path}")

    replica_samples = load_replica_series(replica_series_path)

    timestamps = [parse_iso8601(row["timestamp"]) for row in rows]
    publish_start_ts = min(timestamps)
    publish_end_ts = max(timestamps)
    query_start_ts = publish_start_ts - METRIC_QUERY_PREROLL_SEC

    promql = build_queries(mode, step)["active_requests"]
    series = query_range(
        prometheus_url,
        promql,
        query_start_ts,
        publish_end_ts,
        step,
        allow_empty=True,
    )

    stale_excluded = 0
    target_unavailable = 0
    populated = 0

    for row in rows:
        ts = parse_iso8601(row["timestamp"])
        availability = row.get("availability_state", "")
        serving_unavailable = availability == AVAILABILITY_UNAVAILABLE

        if serving_unavailable:
            row["active_requests"] = TARGET_UNAVAILABLE
            target_unavailable += 1
            continue

        if ready_replicas_changed_in_lookback(replica_samples, ts):
            row["active_requests"] = MISSING
            stale_excluded += 1
            continue

        raw_val = lookup_series_value(series, ts, step)
        classified = classify_metric_value(raw_val, target_unavailable=False)
        row["active_requests"] = classified
        if classified != MISSING:
            populated += 1

    out_fieldnames = insert_column(original_fieldnames, "active_requests", "rps")
    # Preserve column order from FIELDNAMES where possible, but never drop columns.
    for name in FIELDNAMES:
        if name not in out_fieldnames and name in rows[0]:
            out_fieldnames.append(name)

    with metrics_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=out_fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    serving_rows = sum(1 for row in rows if is_serving_row(row))
    return {
        "rows_total": len(rows),
        "serving_rows": serving_rows,
        "populated": populated,
        "stale_excluded": stale_excluded,
        "target_unavailable": target_unavailable,
        "promql": promql,
    }


def backfill_run(run_root: Path, prometheus_url: str) -> int:
    manifest_path = run_root / "manifest.json"
    if not manifest_path.is_file():
        print(f"BACKFILL_SKIP run={run_root.name} reason=missing_manifest", file=sys.stderr)
        return 1

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    run_id = manifest.get("run_id", run_root.name)
    print(f"BACKFILL_RUN_BEGIN run_id={run_id} path={run_root}")

    for rep_num, rep_dir in discover_reps(run_root):
        for metrics_name in ("fixed_metrics.csv", "hpa_metrics.csv"):
            metrics_path = rep_dir / metrics_name
            if not metrics_path.is_file():
                print(
                    f"BACKFILL_SKIP run_id={run_id} rep={rep_num} "
                    f"file={metrics_name} reason=missing"
                )
                continue
            arm = arm_from_metrics_path(metrics_path)
            replica_series = rep_dir / f"replica_series_{arm}.csv"
            stats = backfill_metrics_csv(
                metrics_path,
                replica_series,
                prometheus_url,
            )
            print(
                f"ACTIVE_REQUESTS_BACKFILL "
                f"run_id={run_id} rep={rep_num} arm={arm} "
                f"rows_total={stats['rows_total']} serving_rows={stats['serving_rows']} "
                f"populated={stats['populated']} "
                f"stale_excluded={stats['stale_excluded']} "
                f"target_unavailable={stats['target_unavailable']} "
                f"lookback_sec={ACTIVE_REQUESTS_STALENESS_LOOKBACK_SEC} "
                f"path={metrics_path}"
            )

    print(f"BACKFILL_RUN_DONE run_id={run_id}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-root",
        action="append",
        required=True,
        help="results/runs/<run_id> (repeatable)",
    )
    parser.add_argument(
        "--prometheus-url",
        default="http://localhost:9090",
    )
    args = parser.parse_args()

    rc = 0
    for run_root in args.run_root:
        if backfill_run(Path(run_root), args.prometheus_url) != 0:
            rc = 1
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
