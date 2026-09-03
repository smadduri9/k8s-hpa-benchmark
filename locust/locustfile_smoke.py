"""
4-minute Locust load shape for kind smoke tests.

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
    """4-minute smoke profile: ramp, spike, cool-down."""

    stages = [
        (60, 10, 2),   # 0-1 min: ramp to 10 users
        (180, 20, 5),  # 1-3 min: hold/spike to 20 users
        (240, 5, 5),   # 3-4 min: ramp down
    ]

    def tick(self):
        run_time = self.get_run_time()
        for end_time, users, spawn_rate in self.stages:
            if run_time <= end_time:
                return (users, spawn_rate)
        if self.runner is not None:
            self.runner.quit()
        return None
