#!/usr/bin/env python3
"""Extract 1-second request-count series from WorldCup98 and RetailRocket traces.

OS/arch assumptions: macOS (darwin) or Linux, Python 3.14+ (repo venv).
Extraction only — no candidate scoring (Step 6).

WorldCup98: one series per (server byte, France +0200 local day). No combined
all-server series. Re-validates every wc_day file while reading.

RetailRocket: site-wide 1 s series after the committed bot filter in
docs/SHAPE_SELECTION.md.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time

# Piped through tee when run from automation; line-buffer stdout for live progress.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(line_buffering=True)
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
WC98_DIR = REPO_ROOT / "traces" / "wc98"
RR_EVENTS = REPO_ROOT / "traces" / "retailrocket" / "events.csv"
DERIVED = REPO_ROOT / "traces" / "derived"
RR_KEPT_EVENT_TYPES = frozenset({"view", "addtocart", "transaction"})
WC98_INTRA_FILE_PROGRESS_INTERVAL = 2_000_000

# ITA: GMT epoch + 2 h = France local during collection (fixed +0200).
FRANCE_WC98_OFFSET = timedelta(hours=2)

# Worked example from ITA test log / Step 3 validation.
TIMEZONE_WORKED_EXAMPLE_TS = 893971817

sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))
from kaggle_auth import kaggle_credentials_error, kaggle_credentials_present  # noqa: E402
from wc98_decode import (  # noqa: E402
    RECORD_SIZE,
    iter_validated_records,
)


def france_local_from_gmt_epoch(ts_gmt: int) -> tuple[str, int]:
    """Return (YYYY-MM-DD, second_of_day) in France +0200 from GMT epoch seconds."""
    dt_gmt = datetime.fromtimestamp(ts_gmt, tz=timezone.utc)
    dt_fr = dt_gmt + FRANCE_WC98_OFFSET
    date_str = dt_fr.strftime("%Y-%m-%d")
    second_of_day = dt_fr.hour * 3600 + dt_fr.minute * 60 + dt_fr.second
    return date_str, second_of_day


def print_timezone_worked_example() -> None:
    ts = TIMEZONE_WORKED_EXAMPLE_TS
    dt_gmt = datetime.fromtimestamp(ts, tz=timezone.utc)
    dt_fr = dt_gmt + FRANCE_WC98_OFFSET
    print(
        "WC98_TIMEZONE_WORKED_EXAMPLE "
        f"ts_gmt={ts} "
        f"gmt={dt_gmt.strftime('%Y-%m-%d %H:%M:%S UTC')} "
        f"france_local_fixed_plus0200={dt_fr.strftime('%Y-%m-%d %H:%M:%S')} "
        f"local_date={dt_fr.strftime('%Y-%m-%d')} "
        f"second_of_day={dt_fr.hour * 3600 + dt_fr.minute * 60 + dt_fr.second}",
        flush=True,
    )


@dataclass
class WC98ExtractionSummary:
    files_processed: int = 0
    records_processed: int = 0
    servers_ever_seen: set[int] = field(default_factory=set)
    dates_with_site_traffic: set[str] = field(default_factory=set)
    series_produced: int = 0
    ineligible_server_absent: int = 0
    request_totals: list[int] = field(default_factory=list)


def histogram_bucket(total: int) -> str:
    if total == 0:
        return "0"
    if total < 1000:
        return "1-999"
    if total < 10_000:
        return "1000-9999"
    if total < 100_000:
        return "10000-99999"
    if total < 1_000_000:
        return "100000-999999"
    if total < 10_000_000:
        return "1000000-9999999"
    return "10000000+"


def extract_wc98(wc98_dir: Path, out_dir: Path) -> WC98ExtractionSummary:
    files = sorted(wc98_dir.glob("wc_day*.gz"))
    if not files:
        raise SystemExit(f"WC98_INPUT_MISSING dir={wc98_dir}")

    print(
        "WC98_EXTRACT_START "
        f"files={len(files)} "
        "expected_runtime_note=approximately_45_to_120_minutes_for_1.3B_records_depends_on_cpu_and_disk",
        flush=True,
    )
    print_timezone_worked_example()

    # (server, local_date) -> second_of_day -> count
    counts: dict[tuple[int, str], dict[int, int]] = defaultdict(lambda: defaultdict(int))
    summary = WC98ExtractionSummary()
    started = time.monotonic()

    for file_index, path in enumerate(files, start=1):
        file_started = time.monotonic()
        file_records = 0
        decoded_count = 0
        file_size = 0
        for record in iter_validated_records(path):
            local_date, second_of_day = france_local_from_gmt_epoch(record.timestamp)
            key = (record.server, local_date)
            counts[key][second_of_day] += 1
            summary.servers_ever_seen.add(record.server)
            summary.dates_with_site_traffic.add(local_date)
            file_records += 1
            summary.records_processed += 1
            decoded_count += 1
            file_size += RECORD_SIZE
            if decoded_count % WC98_INTRA_FILE_PROGRESS_INTERVAL == 0:
                print(
                    f"WC98_INTRA_FILE_PROGRESS index={file_index}/{len(files)} "
                    f"name={path.name} decoded_count={decoded_count} "
                    f"file_elapsed_sec={time.monotonic() - file_started:.1f} "
                    f"total_elapsed_sec={time.monotonic() - started:.1f}",
                    flush=True,
                )

        summary.files_processed += 1
        elapsed = time.monotonic() - file_started
        total_elapsed = time.monotonic() - started
        print(
            f"WC98_FILE_PROGRESS index={file_index}/{len(files)} "
            f"name={path.name} decoded_count={decoded_count} "
            f"file_size={file_size} records_binned={file_records} "
            f"file_elapsed_sec={elapsed:.1f} total_elapsed_sec={total_elapsed:.1f}",
            flush=True,
        )
        print(
            f"RECORD_SIZE={RECORD_SIZE} file_size={file_size} "
            f"expected_count={decoded_count} decoded_count={decoded_count}",
            flush=True,
        )
        print("WC98_DECODE_SELF_CHECK=PASS", flush=True)

    series_dir = out_dir / "wc98" / "series"
    series_dir.mkdir(parents=True, exist_ok=True)

    totals_by_key: dict[tuple[int, str], int] = {}
    for key, second_map in counts.items():
        total = sum(second_map.values())
        totals_by_key[key] = total
        if total == 0:
            continue
        server, local_date = key
        out_path = series_dir / f"server_{server:03d}_{local_date}.csv"
        with out_path.open("w", newline="", encoding="utf-8") as handle:
            handle.write(
                f"# server={server} local_date={local_date} "
                f"timezone=france_fixed_plus0200 total_requests={total}\n"
            )
            writer = csv.writer(handle)
            writer.writerow(["second_offset", "request_count"])
            for second in sorted(second_map):
                writer.writerow([second, second_map[second]])
        summary.series_produced += 1
        summary.request_totals.append(total)

    for local_date in sorted(summary.dates_with_site_traffic):
        for server in sorted(summary.servers_ever_seen):
            if totals_by_key.get((server, local_date), 0) == 0:
                summary.ineligible_server_absent += 1

    dist: dict[str, int] = defaultdict(int)
    for total in summary.request_totals:
        dist[histogram_bucket(total)] += 1

    summary_path = out_dir / "wc98" / "extraction_summary.json"
    payload = {
        "files_processed": summary.files_processed,
        "records_processed": summary.records_processed,
        "servers_seen": len(summary.servers_ever_seen),
        "servers_ever_seen": sorted(summary.servers_ever_seen),
        "dates_with_site_traffic": len(summary.dates_with_site_traffic),
        "series_produced": summary.series_produced,
        "ineligible_server_absent": summary.ineligible_server_absent,
        "request_count_distribution": dict(sorted(dist.items())),
        "timezone": "france_fixed_plus0200",
    }
    summary_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    total_elapsed = time.monotonic() - started
    print(
        "WC98_EXTRACT_COMPLETE "
        f"files_processed={summary.files_processed} "
        f"records_processed={summary.records_processed} "
        f"servers_seen={len(summary.servers_ever_seen)} "
        f"dates_with_site_traffic={len(summary.dates_with_site_traffic)} "
        f"series_produced={summary.series_produced} "
        f"ineligible_server_absent={summary.ineligible_server_absent} "
        f"elapsed_sec={total_elapsed:.1f}",
        flush=True,
    )
    print(f"WC98_REQUEST_COUNT_DISTRIBUTION {json.dumps(dict(sorted(dist.items())))}", flush=True)
    print(f"WC98_SUMMARY_PATH {summary_path}", flush=True)
    return summary


@dataclass
class RetailRocketExtractionSummary:
    visitors_total: int = 0
    visitors_dropped: int = 0
    events_dropped: int = 0
    events_kept: int = 0
    seconds_with_traffic: int = 0


def extract_retailrocket(events_path: Path, out_dir: Path) -> RetailRocketExtractionSummary:
    if not events_path.is_file():
        if not kaggle_credentials_present():
            raise SystemExit(
                f"RETAILROCKET_INPUT_MISSING path={events_path}; {kaggle_credentials_error()}"
            )
        raise SystemExit(
            f"RETAILROCKET_INPUT_MISSING path={events_path}; "
            "run: bash scripts/download_traces.sh --retailrocket-only"
        )

    print(
        "RETAILROCKET_EXTRACT_START "
        f"path={events_path} "
        "expected_runtime_note=approximately_5_to_20_minutes_for_2.7M_events"
    )

    visitor_event_count: dict[str, int] = defaultdict(int)
    visitor_min_ts: dict[str, int] = {}
    visitor_max_ts: dict[str, int] = {}
    visitor_second_counts: dict[str, dict[int, int]] = defaultdict(lambda: defaultdict(int))
    events_excluded_by_type = 0

    started = time.monotonic()
    rows_read = 0

    with events_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise SystemExit("RETAILROCKET_CSV_INVALID reason=missing_header")
        for row in reader:
            rows_read += 1
            if rows_read % 500_000 == 0:
                print(
                    f"RETAILROCKET_PASS1_PROGRESS rows_read={rows_read} "
                    f"elapsed_sec={time.monotonic() - started:.1f}",
                    flush=True,
                )
            if row["event"] not in RR_KEPT_EVENT_TYPES:
                events_excluded_by_type += 1
                continue
            visitor = row["visitorid"]
            ts_ms = int(row["timestamp"])
            ts_sec = ts_ms // 1000
            visitor_event_count[visitor] += 1
            visitor_min_ts[visitor] = min(visitor_min_ts.get(visitor, ts_sec), ts_sec)
            visitor_max_ts[visitor] = max(visitor_max_ts.get(visitor, ts_sec), ts_sec)
            visitor_second_counts[visitor][ts_sec] += 1

    summary = RetailRocketExtractionSummary()
    summary.visitors_total = len(visitor_event_count)

    dropped_visitors: set[str] = set()
    for visitor, count in visitor_event_count.items():
        span = max(visitor_max_ts[visitor] - visitor_min_ts[visitor] + 1, 1)
        if count / span > 1.0:
            dropped_visitors.add(visitor)
            continue
        if any(c > 5 for c in visitor_second_counts[visitor].values()):
            dropped_visitors.add(visitor)

    summary.visitors_dropped = len(dropped_visitors)

    site_counts: dict[int, int] = defaultdict(int)
    rows_read = 0
    with events_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            rows_read += 1
            if rows_read % 500_000 == 0:
                print(
                    f"RETAILROCKET_PASS2_PROGRESS rows_read={rows_read} "
                    f"elapsed_sec={time.monotonic() - started:.1f}",
                    flush=True,
                )
            if row["event"] not in RR_KEPT_EVENT_TYPES:
                continue
            visitor = row["visitorid"]
            if visitor in dropped_visitors:
                summary.events_dropped += 1
                continue
            ts_sec = int(row["timestamp"]) // 1000
            site_counts[ts_sec] += 1
            summary.events_kept += 1

    rr_dir = out_dir / "retailrocket"
    rr_dir.mkdir(parents=True, exist_ok=True)
    out_path = rr_dir / "events_1s.csv"
    with out_path.open("w", newline="", encoding="utf-8") as handle:
        handle.write("# series=retailrocket timezone=UTC bot_filter=shape_selection_rule_v2\n")
        writer = csv.writer(handle)
        writer.writerow(["unix_second", "request_count"])
        for ts_sec in sorted(site_counts):
            writer.writerow([ts_sec, site_counts[ts_sec]])

    summary.seconds_with_traffic = len(site_counts)
    summary_path = rr_dir / "extraction_summary.json"
    payload = {
        "visitors_total": summary.visitors_total,
        "visitors_dropped": summary.visitors_dropped,
        "events_dropped": summary.events_dropped,
        "events_kept": summary.events_kept,
        "events_excluded_by_type": events_excluded_by_type,
        "seconds_with_traffic": summary.seconds_with_traffic,
        "timezone": "UTC",
        "bot_filter": "shape_selection_rule_v2",
        "kept_event_types": sorted(RR_KEPT_EVENT_TYPES),
    }
    summary_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    elapsed = time.monotonic() - started
    print(
        "RETAILROCKET_EXTRACT_COMPLETE "
        f"visitors_total={summary.visitors_total} "
        f"visitors_dropped={summary.visitors_dropped} "
        f"events_dropped={summary.events_dropped} "
        f"events_kept={summary.events_kept} "
        f"seconds_with_traffic={summary.seconds_with_traffic} "
        f"elapsed_sec={elapsed:.1f}"
    )
    print(f"RETAILROCKET_SUMMARY_PATH {summary_path}")
    print(f"RETAILROCKET_SERIES_PATH {out_path}")
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wc98-only", action="store_true")
    parser.add_argument("--retailrocket-only", action="store_true")
    parser.add_argument("--wc98-dir", type=Path, default=WC98_DIR)
    parser.add_argument("--retailrocket-events", type=Path, default=RR_EVENTS)
    parser.add_argument("--out-dir", type=Path, default=DERIVED)
    args = parser.parse_args()

    if args.wc98_only and args.retailrocket_only:
        print("ERROR: choose at most one of --wc98-only and --retailrocket-only", file=sys.stderr)
        return 1

    args.out_dir.mkdir(parents=True, exist_ok=True)

    run_wc98 = not args.retailrocket_only
    run_rr = not args.wc98_only

    if run_wc98:
        extract_wc98(args.wc98_dir, args.out_dir)
    if run_rr:
        extract_retailrocket(args.retailrocket_events, args.out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
