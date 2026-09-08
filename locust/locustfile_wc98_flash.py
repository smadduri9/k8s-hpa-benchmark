"""
Trace-derived Locust load shape: wc98_flash (flash).

Unit-mean plateau vector from worldcup98 winner under shape_selection_rule_v2.
Absolute user amplitude is set at runtime via SHAPE_MEAN_USERS (default 45).

Do NOT modify locust/locustfile.py — published runs depend on byte-identical replay.

Run:
  SHAPE_MEAN_USERS=45 locust -f locustfile_wc98_flash.py \
    --host http://<SERVICE_IP> --headless --run-time 18m
"""

import os

from locust import HttpUser, task, between, LoadTestShape

RUN_TIME_SEC = 1080
PLATEAU_SEC = 30
UNIT_MEAN_PLATEAUS = [
    0.59682845,
    0.86496877,
    0.66602595,
    0.84766939,
    0.75252283,
    0.57952907,
    0.55358001,
    0.64872657,
    0.69197501,
    0.80442095,
    1.27150408,
    1.20230658,
    1.20230658,
    1.44449784,
    1.81643441,
    1.95482941,
    2.01537722,
    1.69533878,
    1.71263815,
    1.25420471,
    0.99471408,
    0.89091783,
    0.89091783,
    0.76982220,
    0.94281595,
    1.04661221,
    1.03796252,
    0.78712158,
    0.87361845,
    0.95146564,
    0.97741470,
    0.80442095,
    0.89956752,
    0.58817876,
    0.51898126,
    0.44978376,
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
    """wc98_flash: 36 plateaus x {PLATEAU_SEC}s over {RUN_TIME_SEC}s."""

    def tick(self):
        users = target_users_at(self.get_run_time())
        if users is None:
            return None
        return (users, _spawn_rate())
