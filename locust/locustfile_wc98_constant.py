"""
Trace-derived Locust load shape: wc98_constant (constant).

Unit-mean plateau vector from worldcup98 winner under shape_selection_rule_v2.
Absolute user amplitude is set at runtime via SHAPE_MEAN_USERS (default 45).

Do NOT modify locust/locustfile.py — published runs depend on byte-identical replay.

Run:
  SHAPE_MEAN_USERS=45 locust -f locustfile_wc98_constant.py \
    --host http://<SERVICE_IP> --headless --run-time 18m
"""

import os

from locust import HttpUser, task, between, LoadTestShape

RUN_TIME_SEC = 1080
PLATEAU_SEC = 30
REFERENCE_SHAPE_MEAN_USERS = 45
UNIT_MEAN_PLATEAUS = [
    0.99536306,
    1.01756117,
    1.00187451,
    0.96931728,
    0.98944357,
    1.01608129,
    0.97997238,
    0.95807024,
    1.01223362,
    0.97050118,
    0.98382005,
    1.01696922,
    0.99625099,
    1.00157853,
    0.95925414,
    1.00246646,
    1.03472770,
    1.02200079,
    0.99595501,
    1.03117601,
    0.96250987,
    1.03088003,
    0.95155880,
    0.97079716,
    0.98618785,
    1.01548934,
    0.99891476,
    0.97138911,
    1.05071034,
    0.99595501,
    1.03561563,
    0.98796369,
    1.01844909,
    1.01016180,
    1.03531965,
    1.02348066,
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
    """wc98_constant: 36 plateaus x {PLATEAU_SEC}s over {RUN_TIME_SEC}s."""

    def tick(self):
        users = target_users_at(self.get_run_time())
        if users is None:
            return None
        return (users, _spawn_rate())
