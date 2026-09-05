"""
18-minute flash-crowd Locust load shape (RLScale-Bench "flash" pattern).

  0-420s    (0-7 min)    30 users   steady state, lets HPA settle before the spike
  420-600s  (7-10 min)   90 users   3x spike
  600-1080s (10-18 min)  30 users   recovery and settle

The 3x ratio is the paper's; the absolute level is not. At R=1039.8ms
(run-20260905T160157Z fixed arm) plus 2.0s mean think time one user delivers
0.329 req/s, so the paper's 80->240 req/min would need 4-12 users and would never
approach the 60% CPU target. A 60->180 curve was also rejected: 180 users demands
~63 RPS, roughly 12 pods against maxReplicas 10, which measures overload rather
than autoscaling. See RESULTS.md for the full derivation.

Spike suddenness: the transition is +60 users at spawn_rate 60/s = 1.0s, which
equals one tick() interval and is the floor imposed by tick granularity. A higher
spawn rate cannot make the observable transition faster. For contrast, hybrid ramps
the same +60 users at 20/s = 3.0s. A spike that ramps over a minute is not a flash.

Time-weighted mean user count: (420*30 + 180*90 + 480*30)/1080 = 40.0 users.

Duration is exactly 18 minutes, identical to every other shape. Unequal durations
make pod-hours incommensurable and break the cost-per-1k comparison.

Do NOT modify locust/locustfile.py — it must remain byte-identical for the runs
already published against it.

Run:
  locust -f locustfile_flash.py --host http://<SERVICE_IP> --headless --run-time 18m
"""

from locust import HttpUser, task, between, LoadTestShape

RUN_TIME_SEC = 1080
BASELINE_USERS = 30
SPIKE_USERS = 90  # 3x baseline, the paper's flash ratio
SPIKE_START_SEC = 420
SPIKE_END_SEC = 600
# 90 - 30 = 60 users at 60/s = 1.0s, one tick interval. The spike must be sudden.
SPAWN_RATE = 60

# (end_second, users) — each entry holds until its end_second, inclusive.
STAGES = [
    (SPIKE_START_SEC, BASELINE_USERS),
    (SPIKE_END_SEC, SPIKE_USERS),
    (RUN_TIME_SEC, BASELINE_USERS),
]


def target_users_at(elapsed_sec: float) -> int | None:
    """Intended user count at an elapsed offset, or None past the run.

    The shape's tick() calls this, and the smoke checker calls it to build the
    expected curve, so achieved-vs-target is compared against one definition
    rather than two that can drift apart.
    """
    for end_sec, users in STAGES:
        if elapsed_sec <= end_sec:
            return users
    return None


def time_weighted_mean_users() -> float:
    """Mean user count weighted by segment duration."""
    total = 0.0
    previous_end = 0
    for end_sec, users in STAGES:
        total += users * (end_sec - previous_end)
        previous_end = end_sec
    return total / RUN_TIME_SEC


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


class FlashLoadShape(LoadTestShape):
    """30 -> 90 -> 30 users with a 1.0s spike transition, 18 minutes."""

    def tick(self):
        users = target_users_at(self.get_run_time())
        if users is None:
            return None
        return (users, SPAWN_RATE)
