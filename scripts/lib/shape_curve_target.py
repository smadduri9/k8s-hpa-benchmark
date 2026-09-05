#!/usr/bin/env python3
"""Trivial HTTP target used only to validate Locust load-shape user curves.

The user curve is entirely client-side: Locust spawns users per tick() regardless of
how the target responds. Validating achieved-vs-target therefore needs only a
reachable endpoint, and a fast local one is a better surface than a cluster because
it adds no cold-start, CPU-contention or scheduling variance to the thing under test.

This is a CURVE FIXTURE, NOT A WORKLOAD. It does no CPU work. Response times measured
against it are meaningless and must never be compared to any benchmark run.

Answers 200 immediately on every path, including / and /health and /cpu.
"""

from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class InstantOK(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args) -> None:
        """Silence per-request logging: 45 users for 18 minutes is a lot of noise."""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    server = ThreadingHTTPServer(("127.0.0.1", args.port), InstantOK)
    server.daemon_threads = True
    print(f"SHAPE_CURVE_TARGET_LISTENING port={args.port}", flush=True)
    server.serve_forever()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
