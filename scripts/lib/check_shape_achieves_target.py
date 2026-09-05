#!/usr/bin/env python3
"""Validate that a Locust run's achieved user count tracked the shape's intended curve.

tick() returns (users, spawn_rate) roughly once per second, and Locust can lag the
target when spawn_rate is too low, so a shape that is correct on paper can still
under-deliver. This compares the User Count column of a Locust stats-history CSV
against the shape's own target definition.

The expected curve comes from the shape module itself (target_users_at for the new
shapes, PhasedLoadShape.stages for the untouched locustfile.py) rather than being
reimplemented here, so target and achieved cannot drift apart.

Samples inside a transition window after each target change are excluded: spawning
is not instantaneous and asserting there would measure spawn rate, not the curve.
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

SHAPE_FILES = {
    "hybrid": "locust/locustfile.py",
    "constant": "locust/locustfile_constant.py",
    "flash": "locust/locustfile_flash.py",
}

# Locust needs time to spawn or despawn after a target change; those samples measure
# spawn rate rather than curve correctness.
DEFAULT_TRANSITION_WINDOW_SEC = 12
# Allowance on settled samples. A spawn_rate of 10/s or 60/s settles well inside the
# window, so a settled sample off by more than this is a real curve failure.
DEFAULT_TOLERANCE_USERS = 2


def load_shape_module(shape: str):
    rel = SHAPE_FILES[shape]
    path = REPO_ROOT / rel
    if not path.is_file():
        raise SystemExit(f"SHAPE_MODULE_MISSING shape={shape} path={path}")
    name = f"_shape_{shape}"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise SystemExit(f"SHAPE_MODULE_UNLOADABLE shape={shape} path={path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def target_curve(shape: str, module):
    """Return (target_users_at, spawn_rate_at, run_time_sec) for the shape.

    Every shape is a STEP function of time, not a linear ramp: tick() returns a
    target and Locust rate-limits spawning toward it. locustfile.py's docstring
    says "1 -> 20 users", but the shape jumps to 20 and takes 20/2 = 10s to spawn.
    Time-weighted means must therefore use step levels, not segment midpoints.
    """
    if hasattr(module, "target_users_at"):
        spawn_rate = int(module.SPAWN_RATE)
        return (
            module.target_users_at,
            lambda _elapsed: spawn_rate,
            int(module.RUN_TIME_SEC),
        )

    # locustfile.py is byte-frozen and exposes only PhasedLoadShape.stages.
    stages = list(module.PhasedLoadShape.stages)
    run_time_sec = int(stages[-1][0])

    def hybrid_target(elapsed_sec: float) -> int | None:
        for end_sec, users, _spawn_rate in stages:
            if elapsed_sec <= end_sec:
                return int(users)
        return None

    def hybrid_spawn_rate(elapsed_sec: float) -> int | str:
        for end_sec, _users, spawn_rate in stages:
            if elapsed_sec <= end_sec:
                return int(spawn_rate)
        return "MISSING"

    return hybrid_target, hybrid_spawn_rate, run_time_sec


def transition_seconds(target_at, run_time_sec: int) -> list[int]:
    """Elapsed offsets at which the target changes."""
    changes = []
    previous = target_at(0)
    for sec in range(1, run_time_sec + 1):
        current = target_at(sec)
        if current != previous:
            changes.append(sec)
        previous = current
    return changes


def read_user_counts(history_csv: Path) -> list[tuple[int, int]]:
    """(unix_timestamp, user_count) from Aggregated rows, in file order."""
    if not history_csv.is_file():
        raise SystemExit(f"HISTORY_CSV_MISSING path={history_csv}")
    samples: list[tuple[int, int]] = []
    with history_csv.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None or "User Count" not in reader.fieldnames:
            raise SystemExit(
                f"HISTORY_CSV_INVALID path={history_csv} reason=missing_user_count_column"
            )
        for row in reader:
            if row.get("Name") != "Aggregated":
                continue
            raw_ts = (row.get("Timestamp") or "").strip()
            raw_users = (row.get("User Count") or "").strip()
            if not raw_ts or not raw_users:
                continue
            samples.append((int(raw_ts), int(float(raw_users))))
    if not samples:
        raise SystemExit(f"HISTORY_CSV_EMPTY path={history_csv}")
    return samples


def time_weighted_mean(pairs: list[tuple[int, int]]) -> float:
    """Mean user count weighted by the interval each sample represents."""
    if len(pairs) < 2:
        return float(pairs[0][1]) if pairs else 0.0
    total_weighted = 0.0
    total_span = 0.0
    for index in range(len(pairs) - 1):
        elapsed, users = pairs[index]
        next_elapsed, _ = pairs[index + 1]
        span = next_elapsed - elapsed
        total_weighted += users * span
        total_span += span
    if total_span == 0:
        return float(pairs[0][1])
    return total_weighted / total_span


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shape", required=True, choices=sorted(SHAPE_FILES))
    parser.add_argument("--history-csv", required=True)
    parser.add_argument(
        "--transition-window-sec", type=int, default=DEFAULT_TRANSITION_WINDOW_SEC
    )
    parser.add_argument("--tolerance-users", type=int, default=DEFAULT_TOLERANCE_USERS)
    args = parser.parse_args()

    module = load_shape_module(args.shape)
    target_at, spawn_rate_at, run_time_sec = target_curve(args.shape, module)
    changes = transition_seconds(target_at, run_time_sec)
    # t=0 is a transition too: Locust ramps from zero to the opening target.
    excluded_from = [0] + changes

    samples = read_user_counts(Path(args.history_csv))
    t0 = samples[0][0]
    elapsed_pairs = [(ts - t0, users) for ts, users in samples]

    settled: list[tuple[int, int, int]] = []  # (elapsed, target, achieved)
    max_abs_err = 0
    worst: tuple[int, int, int] | None = None
    for elapsed, achieved in elapsed_pairs:
        if elapsed > run_time_sec:
            continue
        target = target_at(elapsed)
        if target is None:
            continue
        in_transition = any(
            change <= elapsed < change + args.transition_window_sec
            for change in excluded_from
        )
        if in_transition:
            continue
        err = abs(achieved - target)
        settled.append((elapsed, target, achieved))
        if err > max_abs_err:
            max_abs_err = err
            worst = (elapsed, target, achieved)

    if not settled:
        print(
            f"SHAPE_TARGET_NOT_ACHIEVED shape={args.shape} reason=no_settled_samples "
            f"samples={len(elapsed_pairs)} transition_window_sec={args.transition_window_sec}",
            file=sys.stderr,
        )
        return 1

    achieved_mean = time_weighted_mean([(e, a) for e, _t, a in settled])
    if hasattr(module, "time_weighted_mean_users"):
        target_mean = module.time_weighted_mean_users()
    else:
        target_mean = time_weighted_mean([(e, t) for e, t, _a in settled])

    peak_target = max(t for _e, t, _a in settled)
    peak_achieved = max(a for _e, _t, a in settled)

    print(
        f"SHAPE_TIME_WEIGHTED_MEAN_USERS shape={args.shape} "
        f"target={target_mean:.4f} achieved={achieved_mean:.4f}"
    )
    print(
        f"SHAPE_PEAK_ACHIEVED shape={args.shape} "
        f"peak_target={peak_target} peak_observed={peak_achieved}"
    )

    # Only the steepest increase is reported. It is the one that tests suddenness,
    # and constant's 35 small plateau steps would otherwise bury it in noise.
    steepest = None
    for change in changes:
        before = target_at(change - 1)
        after = target_at(change)
        if after is None or before is None or after <= before:
            continue
        if steepest is None or (after - before) > (steepest[1] - steepest[0]):
            steepest = (before, after, change)
    if steepest is None:
        print(f"SHAPE_TRANSITION_SECONDS shape={args.shape} observed_sec=MISSING reason=no_increase")
    else:
        before, after, change = steepest
        observed = observed_transition_seconds(elapsed_pairs, change, after)
        print(
            f"SHAPE_TRANSITION_SECONDS shape={args.shape} from={before} to={after} "
            f"delta={after - before} at_sec={change} spawn_rate={spawn_rate_at(change)} "
            f"observed_sec={observed}"
        )

    if max_abs_err > args.tolerance_users and worst is not None:
        elapsed, target, achieved = worst
        print(
            f"SHAPE_TARGET_NOT_ACHIEVED shape={args.shape} at_sec={elapsed} "
            f"target={target} achieved={achieved} abs_err={max_abs_err} "
            f"tolerance_users={args.tolerance_users}",
            file=sys.stderr,
        )
        return 1

    print(
        f"SHAPE_ACHIEVES_TARGET shape={args.shape} samples={len(settled)} "
        f"max_abs_err_users={max_abs_err} tolerance_users={args.tolerance_users} "
        f"transitions_excluded={len(excluded_from)} "
        f"transition_window_sec={args.transition_window_sec} "
        f"run_time_sec={run_time_sec}"
    )
    return 0


def observed_transition_seconds(
    elapsed_pairs: list[tuple[int, int]], change_sec: int, target_users: int
) -> int | str:
    """Seconds from a target increase until the achieved count first reaches it.

    MISSING when no sample after the change reaches the target, which is itself the
    signal that the transition never completed.
    """
    for elapsed, achieved in elapsed_pairs:
        if elapsed >= change_sec and achieved >= target_users:
            return elapsed - change_sec
    return "MISSING"


if __name__ == "__main__":
    raise SystemExit(main())
