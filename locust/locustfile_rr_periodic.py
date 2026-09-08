"""
Trace-derived Locust load shape: rr_periodic (periodic).

Unit-mean plateau vector from retailrocket winner under shape_selection_rule_v2.
Absolute user amplitude is set at runtime via SHAPE_MEAN_USERS (default 45).

Do NOT modify locust/locustfile.py — published runs depend on byte-identical replay.

Run:
  SHAPE_MEAN_USERS=45 locust -f locustfile_rr_periodic.py \
    --host http://<SERVICE_IP> --headless --run-time 18m
"""

import os

from locust import HttpUser, task, between, LoadTestShape

RUN_TIME_SEC = 1080
PLATEAU_SEC = 30
REFERENCE_SHAPE_MEAN_USERS = 45

SOURCE_WINDOW_SEC = 86400
PERIODIC_DILATION = 80
UNIT_MEAN_PLATEAUS = [
    1.15911959,
    1.32903407,
    1.48044301,
    1.31725782,
    1.15239030,
    1.28865835,
    1.38623300,
    1.35426889,
    1.37109211,
    1.47371372,
    1.49726623,
    1.38791532,
    1.34753960,
    1.23818870,
    1.18603673,
    1.50904248,
    1.24828263,
    1.05986261,
    1.45857283,
    1.08677976,
    1.01107528,
    0.95387635,
    0.68806954,
    0.48450862,
    0.28599467,
    0.38020468,
    0.37011075,
    0.32468807,
    0.23216038,
    0.29440628,
    0.59049488,
    0.49964952,
    0.59554185,
    0.91181831,
    0.92022992,
    1.12547315,
]


def _shape_mean_users() -> int:
    return int(os.environ.get("SHAPE_MEAN_USERS", "45"))


def _scaled_plateau_users() -> list[int]:
    mean_users = _shape_mean_users()
    return [max(1, round(unit * mean_users)) for unit in UNIT_MEAN_PLATEAUS]


def _spawn_rate() -> int:
    scaled = _scaled_plateau_users()
    max_jump = max(
        abs(scaled[i + 1] - scaled[i]) for i in range(len(scaled) - 1)
    )
    ref_scaled = [
        max(1, round(unit * REFERENCE_SHAPE_MEAN_USERS)) for unit in UNIT_MEAN_PLATEAUS
    ]
    ref_jump = max(
        abs(ref_scaled[i + 1] - ref_scaled[i]) for i in range(len(ref_scaled) - 1)
    )
    if ref_jump <= 0:
        return 10
    ref_rate = 60 if ref_jump >= 30 else 10
    ref_duration = ref_jump / ref_rate
    return max(1, round(max_jump / ref_duration))


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
    """rr_periodic: 36 plateaus x {PLATEAU_SEC}s over {RUN_TIME_SEC}s."""

    def tick(self):
        users = target_users_at(self.get_run_time())
        if users is None:
            return None
        return (users, _spawn_rate())
