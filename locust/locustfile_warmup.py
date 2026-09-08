"""Warm-up Locust file: no LoadTestShape so --users/--spawn-rate are honored.

Task mix matches locustfile.py / trace shapes. Not used for the 18m measurement window.
"""

from locust import HttpUser, task, between


class HPAEvalUser(HttpUser):
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
