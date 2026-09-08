"""
Trace-derived Locust load shape: wc98_ramp (ramp).

Unit-mean plateau vector from worldcup98 winner under shape_selection_rule_v2.
Absolute user amplitude is set at runtime via SHAPE_MEAN_USERS (default 45).

Do NOT modify locust/locustfile.py — published runs depend on byte-identical replay.

Run:
  SHAPE_MEAN_USERS=45 locust -f locustfile_wc98_ramp.py \
    --host http://<SERVICE_IP> --headless --run-time 18m
"""

import os

from locust import HttpUser, task, between, LoadTestShape

RUN_TIME_SEC = 1080
PLATEAU_SEC = 30
UNIT_MEAN_PLATEAUS = [
    0.53313463,
    0.54654683,
    0.58678340,
    0.59684255,
    0.62701998,
    0.66977134,
    0.67815396,
    0.72090532,
    0.77790714,
    0.83490896,
    0.70581661,
    0.78712802,
    0.82484981,
    0.82987938,
    0.95394216,
    0.89358730,
    0.96986914,
    1.09393191,
    0.97909002,
    1.02770922,
    1.01513529,
    1.06040143,
    1.18027290,
    1.08387277,
    1.11488846,
    1.18362595,
    1.26996694,
    1.31020351,
    1.33786616,
    1.33283659,
    1.33283659,
    1.37055838,
    1.52060727,
    1.35295487,
    1.43510455,
    1.46109067,
]


def _shape_mean_users() -> int:
    return int(os.environ.get("SHAPE_MEAN_USERS", "45"))


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


class TraceDerivedLoadShape(LoadTestShape):
    """wc98_ramp: 36 plateaus x {PLATEAU_SEC}s over {RUN_TIME_SEC}s."""

    def tick(self):
        users = target_users_at(self.get_run_time())
        if users is None:
            return None
        return (users, _spawn_rate())
