#!/usr/bin/env python3
"""Build Step 7 unit-mean locustfiles, Hurst metadata, and provenance JSON.

OS/arch assumptions: macOS (darwin) or Linux, Python 3.14+ (repo venv).
Reads winning windows from docs/shape_candidates/*_top5.csv rank 1.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
DERIVED = REPO_ROOT / "traces" / "derived"
CANDIDATES_DIR = REPO_ROOT / "docs" / "shape_candidates"
LOCUST_DIR = REPO_ROOT / "locust"
PROVENANCE_DIR = REPO_ROOT / "docs" / "shape_provenance"

sys.path.insert(0, str(REPO_ROOT / "scripts" / "lib"))
from hurst_rs import hurst_or_missing  # noqa: E402
from select_shape_windows import (  # noqa: E402
    PERIODIC_SOURCE_SEC,
    PLATEAU_SEC,
    RUN_TIME_SEC,
    aggregate_native,
    aggregate_periodic,
    load_retailrocket_series,
    load_wc98_series,
)

RULE_VERSION = "shape_selection_rule_v2"
DEFAULT_SHAPE_MEAN_USERS = 45
PERIODIC_DILATION = PERIODIC_SOURCE_SEC // RUN_TIME_SEC

HURST_NOTE = (
    "hurst_native describes the source trace window we selected from, not the "
    "arrival process delivered to pods under Locust. No post-plateau Hurst value "
    "is published. The 36-plateau representation has too few points for an R/S "
    "estimate (minimum 64). The plateau representation destroys all sub-30s "
    "structure by construction."
)

ENVELOPE_CLAIM = (
    "These shapes derive the load ENVELOPE from a production trace. They do not "
    "replay its arrival process. Locust generates requests from N concurrent users "
    "with wait_time uniform(1,3)s; the superposition of independent renewal "
    "processes is approximately Poisson (Palm-Khintchine), so delivered traffic is "
    "near-Poisson within each plateau irrespective of the source. hurst_native is "
    "reported as a property of the SOURCE TRACE and is not a property of the "
    "generated load."
)

ARRIVAL_PROCESS_DEFERRED = (
    "Reproducing a self-similar or bursty arrival process from the source traces "
    "would require an open-loop generator issuing requests on a schedule rather "
    "than a closed-loop user model. That is a different load generator, not a "
    "parameter change. Deferred."
)
WC98_SERIES_DIR = DERIVED / "wc98" / "series"
RR_SERIES = DERIVED / "retailrocket" / "events_1s.csv"

SHAPE_SPECS = [
    {
        "shape_name": "wc98_flash",
        "candidate_csv": "wc98_flash_top5.csv",
        "locust_file": "locustfile_wc98_flash.py",
        "dataset": "worldcup98",
        "archetype": "flash",
        "periodic": False,
        "timezone": "france_fixed_plus0200",
        "citation": (
            "M. Arlitt and T. Jin, \"1998 World Cup Web Site Access Logs\", "
            "August 1998. https://ita.ee.lbl.gov/html/contrib/WorldCup.html"
        ),
        "hpa_no_scale_policy": "abort",
    },
    {
        "shape_name": "wc98_ramp",
        "candidate_csv": "wc98_ramp_top5.csv",
        "locust_file": "locustfile_wc98_ramp.py",
        "dataset": "worldcup98",
        "archetype": "ramp",
        "periodic": False,
        "timezone": "france_fixed_plus0200",
        "citation": (
            "M. Arlitt and T. Jin, \"1998 World Cup Web Site Access Logs\", "
            "August 1998. https://ita.ee.lbl.gov/html/contrib/WorldCup.html"
        ),
        "hpa_no_scale_policy": "abort",
    },
    {
        "shape_name": "wc98_constant",
        "candidate_csv": "wc98_constant_top5.csv",
        "locust_file": "locustfile_wc98_constant.py",
        "dataset": "worldcup98",
        "archetype": "constant",
        "periodic": False,
        "timezone": "france_fixed_plus0200",
        "citation": (
            "M. Arlitt and T. Jin, \"1998 World Cup Web Site Access Logs\", "
            "August 1998. https://ita.ee.lbl.gov/html/contrib/WorldCup.html"
        ),
        "hpa_no_scale_policy": "warn",
    },
    {
        "shape_name": "wc98_periodic",
        "candidate_csv": "wc98_periodic_top5.csv",
        "locust_file": "locustfile_wc98_periodic.py",
        "dataset": "worldcup98",
        "archetype": "periodic",
        "periodic": True,
        "timezone": "france_fixed_plus0200",
        "citation": (
            "M. Arlitt and T. Jin, \"1998 World Cup Web Site Access Logs\", "
            "August 1998. https://ita.ee.lbl.gov/html/contrib/WorldCup.html"
        ),
        "hpa_no_scale_policy": "abort",
    },
    {
        "shape_name": "rr_periodic",
        "candidate_csv": "retailrocket_periodic_top5.csv",
        "locust_file": "locustfile_rr_periodic.py",
        "dataset": "retailrocket",
        "archetype": "periodic",
        "periodic": True,
        "timezone": "UTC",
        "citation": "RetailRocket ecommerce dataset, Kaggle Version 2.",
        "hpa_no_scale_policy": "abort",
        "retailrocket_limitation": (
            "RetailRocket contributes periodic archetype only; constant was dropped "
            "after v2 scoring (zero eligible 1080s windows). Modern-provenance "
            "control on periodic, not a full archetype mirror of WorldCup98."
        ),
    },
]


@dataclass(frozen=True)
class WinnerRow:
    server_or_series_id: str
    local_date: str
    window_offset_sec: int
    rmse: float
    noise_floor_rmse: float
    peak_to_mean: float


def read_winner(csv_path: Path) -> WinnerRow:
    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        row = next(reader)
        return WinnerRow(
            server_or_series_id=row["server_or_series_id"],
            local_date=row["local_date"],
            window_offset_sec=int(row["window_offset_sec"]),
            rmse=float(row["RMSE"]),
            noise_floor_rmse=float(row["noise_floor_rmse"]),
            peak_to_mean=float(row["peak_to_mean"]),
        )


def extract_window_counts(
    spec: dict,
    winner: WinnerRow,
    rr_dense: np.ndarray | None,
    rr_min_ts: int | None,
) -> tuple[np.ndarray, np.ndarray]:
    if spec["dataset"] == "worldcup98":
        server = int(winner.server_or_series_id)
        path = WC98_SERIES_DIR / f"server_{server:03d}_{winner.local_date}.csv"
        _, _, day_counts = load_wc98_series(path)
        offset = winner.window_offset_sec
        if spec["periodic"]:
            window = day_counts[offset : offset + PERIODIC_SOURCE_SEC]
        else:
            window = day_counts[offset : offset + RUN_TIME_SEC]
    else:
        if rr_dense is None or rr_min_ts is None:
            raise RuntimeError("retailrocket dense series required")
        rel = winner.window_offset_sec - rr_min_ts
        window = rr_dense[rel : rel + PERIODIC_SOURCE_SEC]
    plateaus = aggregate_periodic(window) if spec["periodic"] else aggregate_native(window)
    return window.astype(np.float64), plateaus.astype(np.float64)


def unit_mean_plateaus(plateaus: np.ndarray) -> np.ndarray:
    mean = plateaus.mean()
    if mean <= 0:
        raise RuntimeError("plateau mean must be positive")
    return plateaus / mean


def compute_hurst_native(native_window: np.ndarray) -> dict[str, str | float]:
    return {
        "hurst_native": hurst_or_missing(native_window),
        "hurst_native_scope": (
            "source trace selection context only; not a property of the generated Locust load"
        ),
        "hurst_note": HURST_NOTE,
    }


def scaled_user_plateaus(unit_plateaus: np.ndarray, shape_mean_users: int) -> np.ndarray:
    return np.array([max(1, round(float(u) * shape_mean_users)) for u in unit_plateaus], dtype=np.float64)


def fastest_feature_duration_sec(
    unit_plateaus: np.ndarray,
    spec: dict,
    native_window: np.ndarray,
    shape_mean_users: int,
) -> float:
    scaled = scaled_user_plateaus(unit_plateaus, shape_mean_users)
    max_jump = max(abs(scaled[i + 1] - scaled[i]) for i in range(len(scaled) - 1))
    spawn_rate = 60 if max_jump >= 30 else 10
    spawn_sec = max_jump / spawn_rate if spawn_rate else 0.0

    if spec["periodic"]:
        source_bins = native_window.reshape(PERIODIC_SOURCE_SEC // PLATEAU_SEC, PLATEAU_SEC).sum(
            axis=1
        )
        best_rate = 0.0
        best_dur = float(PLATEAU_SEC)
        for start in range(len(source_bins)):
            for end in range(start + 1, len(source_bins)):
                rise = float(source_bins[end] - source_bins[start])
                dur = (end - start) * PLATEAU_SEC
                if rise > 0 and dur > 0:
                    rate = rise / dur
                    if rate > best_rate:
                        best_rate = rate
                        best_dur = dur
        feature_sec = best_dur / PERIODIC_DILATION
    else:
        feature_sec = float(PLATEAU_SEC)

    return round(feature_sec + spawn_sec, 3)


def format_plateau_list(values: np.ndarray) -> str:
    return ",\n    ".join(f"{v:.8f}" for v in values)


def render_locust(spec: dict, unit_plateaus: np.ndarray) -> str:
    plateau_literal = format_plateau_list(unit_plateaus)
    periodic_note = ""
    if spec["periodic"]:
        periodic_note = (
            f"\nSOURCE_WINDOW_SEC = {PERIODIC_SOURCE_SEC}\n"
            f"PERIODIC_DILATION = {PERIODIC_DILATION}\n"
        )
    return f'''"""
Trace-derived Locust load shape: {spec["shape_name"]} ({spec["archetype"]}).

Unit-mean plateau vector from {spec["dataset"]} winner under {RULE_VERSION}.
Absolute user amplitude is set at runtime via SHAPE_MEAN_USERS (default {DEFAULT_SHAPE_MEAN_USERS}).

Do NOT modify locust/locustfile.py — published runs depend on byte-identical replay.

Run:
  SHAPE_MEAN_USERS={DEFAULT_SHAPE_MEAN_USERS} locust -f {spec["locust_file"]} \\
    --host http://<SERVICE_IP> --headless --run-time 18m
"""

import os

from locust import HttpUser, task, between, LoadTestShape

RUN_TIME_SEC = {RUN_TIME_SEC}
PLATEAU_SEC = {PLATEAU_SEC}
{periodic_note}UNIT_MEAN_PLATEAUS = [
    {plateau_literal},
]


def _shape_mean_users() -> int:
    return int(os.environ.get("SHAPE_MEAN_USERS", "{DEFAULT_SHAPE_MEAN_USERS}"))


def _scaled_plateau_users() -> list[int]:
    mean_users = _shape_mean_users()
    return [max(1, round(unit * mean_users)) for unit in UNIT_MEAN_PLATEAUS]


def _spawn_rate() -> int:
    mean_users = _shape_mean_users()
    max_jump = max(
        abs(UNIT_MEAN_PLATEAUS[i + 1] - UNIT_MEAN_PLATEAUS[i]) for i in range(len(UNIT_MEAN_PLATEAUS) - 1)
    )
    if max_jump * mean_users >= 30:
        return 60
    return 10


def target_users_at(elapsed_sec: float) -> int | None:
    if elapsed_sec > RUN_TIME_SEC:
        return None
    index = int(elapsed_sec // PLATEAU_SEC)
    if index >= len(UNIT_MEAN_PLATEAUS):
        index = len(UNIT_MEAN_PLATEAUS) - 1
    return _scaled_plateau_users()[index]


def time_weighted_mean_users() -> float:
    users = _scaled_plateau_users()
    return sum(users) / len(users)


def unit_mean_time_weighted() -> float:
    return 1.0


def min_users() -> int:
    return min(_scaled_plateau_users())


class HPAEvalUser(HttpUser):
    """Task mix identical to locustfile.py: cross-shape comparability requires it."""

    wait_time = between(1, 3)

    @task(1)
    def health_check(self):
        with self.client.get("/", catch_response=True) as resp:
            if resp.status_code != 200:
                resp.failure(f"Unexpected status {{resp.status_code}}")

    @task(4)
    def cpu_load(self):
        with self.client.get(
            "/cpu?intensity=low", catch_response=True, name="/cpu?intensity=low"
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Unexpected status {{resp.status_code}}")


class TraceDerivedLoadShape(LoadTestShape):
    """{spec["shape_name"]}: 36 plateaus x {{PLATEAU_SEC}}s over {{RUN_TIME_SEC}}s."""

    def tick(self):
        users = target_users_at(self.get_run_time())
        if users is None:
            return None
        return (users, _spawn_rate())
'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shape-mean-users", type=int, default=DEFAULT_SHAPE_MEAN_USERS)
    parser.add_argument(
        "--provenance-only",
        action="store_true",
        help="Write provenance JSON only; do not regenerate locustfiles",
    )
    args = parser.parse_args()

    rr_dense: np.ndarray | None = None
    rr_min_ts: int | None = None
    if RR_SERIES.is_file():
        rr_dense, rr_min_ts, _ = load_retailrocket_series(RR_SERIES)

    PROVENANCE_DIR.mkdir(parents=True, exist_ok=True)
    LOCUST_DIR.mkdir(parents=True, exist_ok=True)

    print(f"BUILD_SHAPE_ARTIFACTS_START rule={RULE_VERSION} shapes={len(SHAPE_SPECS)}")

    for spec in SHAPE_SPECS:
        winner = read_winner(CANDIDATES_DIR / spec["candidate_csv"])
        native_window, raw_plateaus = extract_window_counts(spec, winner, rr_dense, rr_min_ts)
        unit_plateaus = unit_mean_plateaus(raw_plateaus)
        hurst = compute_hurst_native(native_window)
        fastest = fastest_feature_duration_sec(
            unit_plateaus, spec, native_window, args.shape_mean_users
        )

        provenance = {
            "shape_name": spec["shape_name"],
            "dataset": spec["dataset"],
            "archetype": spec["archetype"],
            "rule": RULE_VERSION,
            "citation": spec["citation"],
            "server_or_series_id": winner.server_or_series_id,
            "local_date": winner.local_date,
            "window_offset_sec": winner.window_offset_sec,
            "timezone": spec["timezone"],
            "selection_rmse": winner.rmse,
            "selection_noise_floor_rmse": winner.noise_floor_rmse,
            "peak_to_mean": winner.peak_to_mean,
            "unit_mean_plateaus": [round(float(v), 8) for v in unit_plateaus],
            "unit_mean_time_weighted": 1.0,
            "dilation_factor": PERIODIC_DILATION if spec["periodic"] else 1,
            "source_window_sec": PERIODIC_SOURCE_SEC if spec["periodic"] else RUN_TIME_SEC,
            "playback_sec": RUN_TIME_SEC,
            "amplitude": "deployment_parameter",
            "SHAPE_MEAN_USERS_default": DEFAULT_SHAPE_MEAN_USERS,
            "hpa_no_scale_policy": spec["hpa_no_scale_policy"],
            "fastest_feature_duration_sec": fastest,
            "envelope_claim": ENVELOPE_CLAIM,
            "arrival_process_reproduction": ARRIVAL_PROCESS_DEFERRED,
            **hurst,
        }
        if "retailrocket_limitation" in spec:
            provenance["retailrocket_limitation"] = spec["retailrocket_limitation"]

        prov_path = PROVENANCE_DIR / f"{spec['shape_name']}.json"
        prov_path.write_text(json.dumps(provenance, indent=2) + "\n", encoding="utf-8")

        if not args.provenance_only:
            locust_path = LOCUST_DIR / spec["locust_file"]
            locust_path.write_text(render_locust(spec, unit_plateaus), encoding="utf-8")
        else:
            locust_path = LOCUST_DIR / spec["locust_file"]

        print(
            f"BUILD_SHAPE_OK name={spec['shape_name']} "
            f"server_or_series_id={winner.server_or_series_id} "
            f"local_date={winner.local_date} offset={winner.window_offset_sec} "
            f"hurst_native={hurst['hurst_native']} "
            f"fastest_feature_duration_sec={fastest} "
            f"provenance={prov_path} locust={locust_path}"
        )

    print("BUILD_SHAPE_ARTIFACTS_COMPLETE")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
