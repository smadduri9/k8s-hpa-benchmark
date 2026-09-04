"""
10-minute Locust load shape for kind smoke tests.

Burst onset is in the first 60–90s; sustained low load continues through RUN_TIME
so Prometheus rate() queries remain populated for the full published window.

Do NOT modify locust/locustfile.py — it must remain byte-identical for 18-minute GKE runs.
"""

from locust import HttpUser, task, between, LoadTestShape


class HPAEvalUser(HttpUser):
    wait_time = between(0.5, 1.5)

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


class SmokeLoadShape(LoadTestShape):
    """10-minute smoke profile: burst at onset, then sustained low load through RUN_TIME."""

    stages = [
        (60, 10, 2),   # 0-1 min: ramp (burst onset — HPA scaling window)
        (180, 20, 5),  # 1-3 min: spike/hold
        (240, 5, 5),   # 3-4 min: cool down from spike
        (600, 5, 1),   # 4-10 min: sustain low load for full-window metrics
    ]

    def tick(self):
        run_time = self.get_run_time()
        for end_time, users, spawn_rate in self.stages:
            if run_time <= end_time:
                return (users, spawn_rate)
        return None
