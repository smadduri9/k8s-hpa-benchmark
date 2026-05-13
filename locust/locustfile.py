"""
Locust load test with phased workload shape for Kubernetes HPA evaluation.

Phases:
  Ramp-up   (0–3 min):   1 → 20 users,  spawn rate 2/s
  Spike     (3–6 min):   20 → 80 users,  spawn rate 20/s
  Sustained (6–15 min):  hold at 60 users
  Recovery  (15–18 min): 60 → 5 users,  ramp down

Uses intensity=low (1000 primes, ~0.5s per request) to keep
requests completing successfully within CPU limits (200m per pod).

Run:
  locust -f locustfile.py --host http://<SERVICE_IP> --headless --run-time 18m
"""

from locust import HttpUser, task, between, LoadTestShape


class HPAEvalUser(HttpUser):
    """Simulates a user sending a mix of lightweight and CPU-heavy requests."""

    wait_time = between(1, 3)

    @task(1)
    def health_check(self):
        """Lightweight GET / — 20% of traffic."""
        with self.client.get("/", catch_response=True) as resp:
            if resp.status_code != 200:
                resp.failure(f"Unexpected status {resp.status_code}")

    @task(4)
    def cpu_load(self):
        """CPU-intensive GET /cpu — 80% of traffic."""
        with self.client.get(
            "/cpu?intensity=low", catch_response=True, name="/cpu?intensity=low"
        ) as resp:
            if resp.status_code == 200:
                resp.success()
            else:
                resp.failure(f"Unexpected status {resp.status_code}")


class PhasedLoadShape(LoadTestShape):
    """
    Defines a time-driven load shape that cycles through 4 phases.
    Each tuple: (end_second, target_users, spawn_rate)
    """

    stages = [
        (180,  20,  2),    # ramp-up:   0–3 min,   1→20 users
        (360,  80,  20),   # spike:     3–6 min,   20→80 users
        (900,  60,  5),    # sustained: 6–15 min,  hold ~60 users
        (1080, 5,   5),    # recovery:  15–18 min, ramp down
    ]

    def tick(self):
        run_time = self.get_run_time()

        for i, (end_time, users, spawn_rate) in enumerate(self.stages):
            if run_time <= end_time:
                return (users, spawn_rate)

        return None  # All phases done — stop the test
