"""
18-minute steady-state Locust load shape (RLScale-Bench "constant" pattern).

Flat 45 users plus/minus 10% noise for the full 1080s. 45 is the time-weighted mean
user count of locust/locustfile.py, so constant and hybrid deliver comparable total
volume and any cross-shape difference is not a volume artifact.

Load level is derived from Little's law, not copied from the paper: at R=1039.8ms
(run-20260905T160157Z fixed arm) plus 2.0s mean think time, one user delivers
0.329 req/s, so the paper's absolute req/min figures would need 4-12 users and
would never approach the 60% CPU target. See RESULTS.md for the full derivation.

Duration is exactly 18 minutes, identical to every other shape. Unequal durations
make pod-hours incommensurable and break the cost-per-1k comparison.

Do NOT modify locust/locustfile.py — it must remain byte-identical for the runs
already published against it.

Run:
  locust -f locustfile_constant.py --host http://<SERVICE_IP> --headless --run-time 18m
"""

import random

from locust import HttpUser, task, between, LoadTestShape

RUN_TIME_SEC = 1080
BASE_USERS = 45
NOISE_FRACTION = 0.10
# Noise is resampled per plateau, not per tick: a target that moves every second
# would keep Locust spawning and despawning and never settle at any level.
PLATEAU_SEC = 30
# Fixed so the realized user curve is reproducible across runs and machines.
NOISE_SEED = 1729
# Largest plateau-to-plateau step is 2 * 4 = 8 users, so a transition completes in
# 0.8s, inside the ~1s tick() interval.
SPAWN_RATE = 10

_NOISE_DELTA = int(BASE_USERS * NOISE_FRACTION)  # 4 users at 45 and 10%
_PLATEAU_COUNT = RUN_TIME_SEC // PLATEAU_SEC
_rng = random.Random(NOISE_SEED)
_PLATEAU_USERS = [
    BASE_USERS + _rng.randint(-_NOISE_DELTA, _NOISE_DELTA)
    for _ in range(_PLATEAU_COUNT)
]


def target_users_at(elapsed_sec: float) -> int | None:
    """Intended user count at an elapsed offset, or None past the run.

    The shape's tick() calls this, and the smoke checker calls it to build the
    expected curve, so achieved-vs-target is compared against one definition
    rather than two that can drift apart.
    """
    if elapsed_sec > RUN_TIME_SEC:
        return None
    index = int(elapsed_sec // PLATEAU_SEC)
    if index >= _PLATEAU_COUNT:
        index = _PLATEAU_COUNT - 1
    return _PLATEAU_USERS[index]


def time_weighted_mean_users() -> float:
    """Mean user count weighted by plateau duration; every plateau is equal length."""
    return sum(_PLATEAU_USERS) / len(_PLATEAU_USERS)


class HPAEvalUser(HttpUser):
    """Task mix identical to locustfile.py: cross-shape comparability requires it."""

    wait_time = between(1, 3)

    @task(1)
    def health_check(self):
        with self.client.get("/", catch_response=True) as resp:
            if resp.status_code != 200:
                resp.failure(f"Unexpected status {resp.status_code}")

    @task(4)
    def cpu_load(self):
        with self.client.get(
            "/cpu?intensity=low", catch_response=True, name="/cpu?intensity=low"
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Unexpected status {resp.status_code}")


class ConstantLoadShape(LoadTestShape):
    """Steady 45 users with seeded plus/minus 10% noise, 18 minutes."""

    def tick(self):
        users = target_users_at(self.get_run_time())
        if users is None:
            return None
        return (users, SPAWN_RATE)
