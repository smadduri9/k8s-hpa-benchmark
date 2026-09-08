#!/usr/bin/env python3
"""Score trace-derived shape candidate windows under shape_selection_rule_v2.

OS/arch assumptions: macOS (darwin) or Linux, Python 3.14+ (repo venv).
Reads extracted 1 s series from traces/derived/. Does not modify traces.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
DERIVED = REPO_ROOT / "traces" / "derived"
WC98_SERIES_DIR = DERIVED / "wc98" / "series"
WC98_SUMMARY = DERIVED / "wc98" / "extraction_summary.json"
RR_SERIES = DERIVED / "retailrocket" / "events_1s.csv"
OUT_DIR = REPO_ROOT / "docs" / "shape_candidates"

RULE_VERSION = "shape_selection_rule_v2"
PLATEAU_SEC = 30
PLATEAUS = 36
RUN_TIME_SEC = 1080
PERIODIC_SOURCE_SEC = 86400
WINDOW_STRIDE = 60
MIN_COUNTS_PER_PLATEAU = 100
SECONDS_PER_DAY = 86400

# ITA day 5 = 1998-04-30 France local; days 1–4 are empty log files.
ITA_DAY5_LOCAL_DATE = date(1998, 4, 30)
ITA_REJECTED_DATES = {
    (ITA_DAY5_LOCAL_DATE.fromordinal(ITA_DAY5_LOCAL_DATE.toordinal() - offset)).isoformat()
    for offset in range(4, 0, -1)
}

WC98_FILENAME_RE = re.compile(r"^server_(\d+)_(\d{4}-\d{2}-\d{2})\.csv$")

DATASET_ARCHETYPES: dict[str, list[str]] = {
    "wc98": ["flash", "ramp", "constant", "periodic"],
    "retailrocket": ["constant", "periodic"],
}


def template_constant() -> np.ndarray:
    return np.ones(PLATEAUS, dtype=np.float64)


def template_ramp() -> np.ndarray:
    return np.array([0.5 + (1.0 * i / 35.0) for i in range(PLATEAUS)], dtype=np.float64)


def template_flash() -> np.ndarray:
    return np.array([0.75] * 14 + [2.25] * 6 + [0.75] * 16, dtype=np.float64)


def template_periodic() -> np.ndarray:
    return np.array(
        [1.0 + 0.5 * math.sin(2.0 * math.pi * i / PLATEAUS) for i in range(PLATEAUS)],
        dtype=np.float64,
    )


TEMPLATES: dict[str, np.ndarray] = {
    "constant": template_constant(),
    "ramp": template_ramp(),
    "flash": template_flash(),
    "periodic": template_periodic(),
}


@dataclass(frozen=True)
class Candidate:
    dataset: str
    archetype: str
    server_or_series_id: str
    local_date: str
    window_offset_sec: int
    rmse: float
    noise_floor_rmse: float
    fit_below_noise_floor: bool
    peak_to_mean: float
    mean_counts_per_plateau: float


def aggregate_native(window_1s: np.ndarray) -> np.ndarray:
    return window_1s.reshape(PLATEAUS, PLATEAU_SEC).sum(axis=1)


def aggregate_periodic(window_1s: np.ndarray) -> np.ndarray:
    bin_size = PERIODIC_SOURCE_SEC // PLATEAUS
    return window_1s.reshape(PLATEAUS, bin_size).sum(axis=1)


def score_plateaus(plateaus: np.ndarray, template: np.ndarray) -> Candidate | None:
    mean_cpp = float(plateaus.mean())
    if mean_cpp < MIN_COUNTS_PER_PLATEAU:
        return None
    peak_to_mean = float(plateaus.max() / mean_cpp)
    noise_floor_rmse = 1.0 / math.sqrt(mean_cpp)
    x_norm = plateaus / mean_cpp
    rmse = float(np.sqrt(np.mean((x_norm - template) ** 2)))
    fit_below = rmse < noise_floor_rmse
    return Candidate(
        dataset="",
        archetype="",
        server_or_series_id="",
        local_date="",
        window_offset_sec=0,
        rmse=rmse,
        noise_floor_rmse=noise_floor_rmse,
        fit_below_noise_floor=fit_below,
        peak_to_mean=peak_to_mean,
        mean_counts_per_plateau=mean_cpp,
    )


def load_wc98_series(path: Path) -> tuple[int, str, np.ndarray]:
    match = WC98_FILENAME_RE.match(path.name)
    if not match:
        raise ValueError(f"WC98_SERIES_FILENAME_INVALID path={path}")
    server = int(match.group(1))
    local_date = match.group(2)
    counts = np.zeros(SECONDS_PER_DAY, dtype=np.int64)
    with path.open(newline="", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("#"):
                continue
            if line.startswith("second_offset"):
                continue
            second_str, count_str = line.strip().split(",")
            counts[int(second_str)] = int(count_str)
    return server, local_date, counts


def load_retailrocket_series(path: Path) -> tuple[np.ndarray, int, int]:
    timestamps: list[int] = []
    counts: list[int] = []
    with path.open(newline="", encoding="utf-8") as handle:
        for line in handle:
            if line.startswith("#") or line.startswith("unix_second"):
                continue
            ts_str, count_str = line.strip().split(",")
            timestamps.append(int(ts_str))
            counts.append(int(count_str))
    if not timestamps:
        raise SystemExit(f"RETAILROCKET_SERIES_EMPTY path={path}")
    min_ts = min(timestamps)
    max_ts = max(timestamps)
    span = max_ts - min_ts + 1
    dense = np.zeros(span, dtype=np.int64)
    for ts, count in zip(timestamps, counts, strict=True):
        dense[ts - min_ts] = count
    return dense, min_ts, max_ts


def enumerate_wc98_candidates(
    archetype: str,
    ineligible_server_absent: int,
) -> tuple[list[Candidate], int, int]:
    template = TEMPLATES[archetype]
    is_periodic = archetype == "periodic"
    window_len = PERIODIC_SOURCE_SEC if is_periodic else RUN_TIME_SEC
    offsets = [0] if is_periodic else list(range(0, SECONDS_PER_DAY - window_len + 1, WINDOW_STRIDE))

    candidates: list[Candidate] = []
    n_total = 0
    n_eligible = 0

    for path in sorted(WC98_SERIES_DIR.glob("server_*.csv")):
        server, local_date, day_counts = load_wc98_series(path)
        if local_date in ITA_REJECTED_DATES:
            continue
        for offset in offsets:
            n_total += 1
            window = day_counts[offset : offset + window_len]
            plateaus = aggregate_periodic(window) if is_periodic else aggregate_native(window)
            scored = score_plateaus(plateaus, template)
            if scored is None:
                continue
            n_eligible += 1
            candidates.append(
                Candidate(
                    dataset="wc98",
                    archetype=archetype,
                    server_or_series_id=str(server),
                    local_date=local_date,
                    window_offset_sec=offset,
                    rmse=scored.rmse,
                    noise_floor_rmse=scored.noise_floor_rmse,
                    fit_below_noise_floor=scored.fit_below_noise_floor,
                    peak_to_mean=scored.peak_to_mean,
                    mean_counts_per_plateau=scored.mean_counts_per_plateau,
                )
            )

    _ = ineligible_server_absent
    return candidates, n_total, n_eligible


def enumerate_rr_candidates(
    archetype: str,
    dense: np.ndarray,
    min_ts: int,
    max_ts: int,
) -> tuple[list[Candidate], int, int]:
    template = TEMPLATES[archetype]
    is_periodic = archetype == "periodic"
    window_len = PERIODIC_SOURCE_SEC if is_periodic else RUN_TIME_SEC
    last_start = max_ts - window_len + 1
    if last_start < min_ts:
        return [], 0, 0

    candidates: list[Candidate] = []
    n_total = 0
    n_eligible = 0

    for offset_ts in range(min_ts, last_start + 1, WINDOW_STRIDE):
        rel_start = offset_ts - min_ts
        n_total += 1
        window = dense[rel_start : rel_start + window_len]
        plateaus = aggregate_periodic(window) if is_periodic else aggregate_native(window)
        scored = score_plateaus(plateaus, template)
        if scored is None:
            continue
        n_eligible += 1
        local_date = datetime.fromtimestamp(offset_ts, tz=timezone.utc).strftime("%Y-%m-%d")
        candidates.append(
            Candidate(
                dataset="retailrocket",
                archetype=archetype,
                server_or_series_id="retailrocket",
                local_date=local_date,
                window_offset_sec=offset_ts,
                rmse=scored.rmse,
                noise_floor_rmse=scored.noise_floor_rmse,
                fit_below_noise_floor=scored.fit_below_noise_floor,
                peak_to_mean=scored.peak_to_mean,
                mean_counts_per_plateau=scored.mean_counts_per_plateau,
            )
        )

    return candidates, n_total, n_eligible


def candidate_sort_key(candidate: Candidate) -> tuple:
    day_ordinal = date.fromisoformat(candidate.local_date).toordinal()
    server_num = int(candidate.server_or_series_id) if candidate.dataset == "wc98" else 0
    return (
        candidate.rmse,
        candidate.window_offset_sec,
        server_num,
        day_ordinal,
    )


def rankable_candidates(candidates: list[Candidate]) -> list[Candidate]:
    return [c for c in candidates if not c.fit_below_noise_floor]


def write_top5_csv(
    path: Path,
    rows: list[Candidate],
    n_total: int,
    n_eligible: int,
    ineligible_server_absent: int,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "server_or_series_id",
        "local_date",
        "window_offset_sec",
        "RMSE",
        "noise_floor_rmse",
        "FIT_BELOW_NOISE_FLOOR",
        "peak_to_mean",
        "N_windows_total",
        "N_windows_eligible",
        "INELIGIBLE_SERVER_ABSENT",
        "rule",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    "server_or_series_id": row.server_or_series_id,
                    "local_date": row.local_date,
                    "window_offset_sec": row.window_offset_sec,
                    "RMSE": f"{row.rmse:.6f}",
                    "noise_floor_rmse": f"{row.noise_floor_rmse:.6f}",
                    "FIT_BELOW_NOISE_FLOOR": str(row.fit_below_noise_floor).lower(),
                    "peak_to_mean": f"{row.peak_to_mean:.6f}",
                    "N_windows_total": n_total,
                    "N_windows_eligible": n_eligible,
                    "INELIGIBLE_SERVER_ABSENT": ineligible_server_absent,
                    "rule": RULE_VERSION,
                }
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--derived-dir", type=Path, default=DERIVED)
    parser.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = parser.parse_args()

    wc98_series_dir = args.derived_dir / "wc98" / "series"
    wc98_summary_path = args.derived_dir / "wc98" / "extraction_summary.json"
    rr_series_path = args.derived_dir / "retailrocket" / "events_1s.csv"

    if not wc98_series_dir.is_dir():
        raise SystemExit(f"WC98_SERIES_MISSING dir={wc98_series_dir}")
    if not rr_series_path.is_file():
        raise SystemExit(f"RETAILROCKET_SERIES_MISSING path={rr_series_path}")

    ineligible_server_absent = 0
    if wc98_summary_path.is_file():
        summary = json.loads(wc98_summary_path.read_text(encoding="utf-8"))
        ineligible_server_absent = int(summary.get("ineligible_server_absent", 0))

    rr_dense, rr_min_ts, rr_max_ts = load_retailrocket_series(rr_series_path)
    print(
        "SHAPE_SCORE_START "
        f"rule={RULE_VERSION} "
        f"wc98_series={len(list(wc98_series_dir.glob('server_*.csv')))} "
        f"rr_span_sec={rr_max_ts - rr_min_ts + 1} "
        f"ineligible_server_absent={ineligible_server_absent}"
    )

    thin_pool = False
    results_summary: dict[str, dict[str, object]] = {}

    for dataset, archetypes in DATASET_ARCHETYPES.items():
        for archetype in archetypes:
            if dataset == "wc98":
                candidates, n_total, n_eligible = enumerate_wc98_candidates(
                    archetype,
                    ineligible_server_absent,
                )
                absent_count = ineligible_server_absent
            else:
                candidates, n_total, n_eligible = enumerate_rr_candidates(
                    archetype,
                    rr_dense,
                    rr_min_ts,
                    rr_max_ts,
                )
                absent_count = 0

            rankable = sorted(rankable_candidates(candidates), key=candidate_sort_key)
            print(
                f"SHAPE_ARCHETYPE_SUMMARY dataset={dataset} archetype={archetype} "
                f"N_windows_total={n_total} N_windows_eligible={n_eligible} "
                f"N_rankable={len(rankable)} "
                f"INELIGIBLE_SERVER_ABSENT={absent_count}"
            )

            if n_eligible < 5:
                print(
                    f"SHAPE_THIN_POOL_STOP dataset={dataset} archetype={archetype} "
                    f"N_windows_eligible={n_eligible} required=5"
                )
                thin_pool = True
                results_summary[f"{dataset}_{archetype}"] = {
                    "N_windows_total": n_total,
                    "N_windows_eligible": n_eligible,
                    "thin_pool": True,
                }
                continue

            top5 = rankable[:5]
            if len(top5) < 5:
                print(
                    f"SHAPE_THIN_POOL_STOP dataset={dataset} archetype={archetype} "
                    f"N_rankable={len(top5)} required=5 "
                    f"(eligible={n_eligible} but FIT_BELOW_NOISE_FLOOR excluded others)"
                )
                thin_pool = True
                results_summary[f"{dataset}_{archetype}"] = {
                    "N_windows_total": n_total,
                    "N_windows_eligible": n_eligible,
                    "N_rankable": len(rankable),
                    "thin_pool": True,
                }
                continue

            out_name = f"{dataset}_{archetype}_top5.csv"
            out_path = args.out_dir / out_name
            write_top5_csv(out_path, top5, n_total, n_eligible, absent_count)
            print(f"SHAPE_TOP5_PATH path={out_path}")

            for rank, row in enumerate(top5, start=1):
                print(
                    f"SHAPE_CANDIDATE rank={rank} dataset={dataset} archetype={archetype} "
                    f"server_or_series_id={row.server_or_series_id} "
                    f"local_date={row.local_date} window_offset_sec={row.window_offset_sec} "
                    f"RMSE={row.rmse:.6f} noise_floor_rmse={row.noise_floor_rmse:.6f} "
                    f"FIT_BELOW_NOISE_FLOOR={str(row.fit_below_noise_floor).lower()} "
                    f"peak_to_mean={row.peak_to_mean:.6f} "
                    f"N_windows_total={n_total} N_windows_eligible={n_eligible}"
                )

            results_summary[f"{dataset}_{archetype}"] = {
                "N_windows_total": n_total,
                "N_windows_eligible": n_eligible,
                "N_rankable": len(rankable),
                "thin_pool": False,
                "top5_path": str(out_path),
            }

    summary_path = args.out_dir / "scoring_summary.json"
    args.out_dir.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(
        json.dumps(
            {
                "rule": RULE_VERSION,
                "MIN_COUNTS_PER_PLATEAU": MIN_COUNTS_PER_PLATEAU,
                "results": results_summary,
                "thin_pool_stop": thin_pool,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"SHAPE_SCORE_SUMMARY_PATH {summary_path}")

    if thin_pool:
        print("SHAPE_SCORE_ABORT thin_pool=true")
        return 2

    print("SHAPE_SCORE_COMPLETE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
