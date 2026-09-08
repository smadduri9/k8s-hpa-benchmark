#!/usr/bin/env python3
"""Watch-based cold-start waterfall collector.

OS/arch assumptions: macOS (darwin) or Linux, Python 3.14+ (repo venv), kubectl.
Does not poll the API; reads kubectl --watch JSON streams. Missing stages stay MISSING.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MISSING = "MISSING"
LT_1S = "<1s"

STAGE_FIELDS = (
    "hpa_decision",
    "pod_created",
    "PodScheduled",
    "PodReadyToStartContainers",
    "image_pull_start",
    "image_pull_end",
    "image_pull_duration_ms",
    "container_started",
    "ContainersReady",
    "Ready",
    "first_request_served",
    "image_cached",
)

PULL_DURATION_RE = re.compile(
    r"Successfully pulled image .+ in ([0-9.]+)\s*(ms|s|m)\b",
    re.IGNORECASE,
)
ALREADY_PRESENT_RE = re.compile(
    r"already present on machine|Image is up to date",
    re.IGNORECASE,
)
FIRST_REQUEST_RE = re.compile(r"^FIRST_REQUEST_SERVED ts=(\S+) pod=(\S+)\s*$")


def parse_rfc3339(value: str | None) -> datetime | None:
    if not value or value == MISSING:
        return None
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def format_rfc3339(value: datetime | None) -> str:
    if value is None:
        return MISSING
    return value.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def duration_label(start: datetime | None, end: datetime | None) -> str:
    if start is None or end is None:
        return MISSING
    delta = (end - start).total_seconds()
    if delta < 0:
        return MISSING
    if delta < 1:
        return LT_1S
    return str(int(delta))


def iter_json_documents(stream) -> Any:
    decoder = json.JSONDecoder()
    buf = ""
    fd = stream.fileno()
    while True:
        chunk = os.read(fd, 65536)
        if not chunk:
            break
        buf += chunk.decode("utf-8")
        buf = buf.lstrip()
        while buf:
            try:
                obj, idx = decoder.raw_decode(buf)
            except json.JSONDecodeError:
                break
            yield obj
            buf = buf[idx:].lstrip()


def unwrap_watch(obj: dict[str, Any]) -> dict[str, Any] | None:
    if "object" in obj and isinstance(obj.get("object"), dict):
        return obj["object"]
    if "kind" in obj:
        return obj
    return None


def condition_time(pod: dict[str, Any], name: str) -> str:
    for cond in pod.get("status", {}).get("conditions") or []:
        if cond.get("type") == name and cond.get("status") == "True":
            ts = parse_rfc3339(cond.get("lastTransitionTime"))
            return format_rfc3339(ts) if ts else MISSING
    return MISSING


def main_container_started(pod: dict[str, Any], container_name: str) -> str:
    for status in pod.get("status", {}).get("containerStatuses") or []:
        if status.get("name") != container_name:
            continue
        running = (status.get("state") or {}).get("running") or {}
        ts = parse_rfc3339(running.get("startedAt"))
        if ts:
            return format_rfc3339(ts)
    return MISSING


def init_sleep_seconds(pod: dict[str, Any], init_name: str) -> int | None:
    for status in pod.get("status", {}).get("initContainerStatuses") or []:
        if status.get("name") != init_name:
            continue
        term = (status.get("state") or {}).get("terminated") or {}
        start = parse_rfc3339(term.get("startedAt"))
        end = parse_rfc3339(term.get("finishedAt"))
        if start is None or end is None:
            return None
        return int((end - start).total_seconds())
    return None


def parse_pull_message(message: str) -> tuple[str, str]:
    """Return (image_cached, image_pull_duration_ms)."""
    match = PULL_DURATION_RE.search(message)
    if match:
        raw = float(match.group(1))
        unit = match.group(2).lower()
        if unit == "s":
            ms = int(round(raw * 1000))
        elif unit == "m":
            ms = int(round(raw * 60_000))
        else:
            ms = int(round(raw))
        return "false", str(ms)
    if ALREADY_PRESENT_RE.search(message):
        return "true", MISSING
    return MISSING, MISSING


class ColdStartCollector:
    def __init__(
        self,
        namespace: str,
        selector: str,
        output_path: Path,
        timeout_sec: int,
        container_name: str,
        init_name: str,
        expect_pods: int,
        first_request_timeout_sec: int,
    ) -> None:
        self.namespace = namespace
        self.selector = selector
        self.output_path = output_path
        self.timeout_sec = timeout_sec
        self.container_name = container_name
        self.init_name = init_name
        self.expect_pods = expect_pods
        self.first_request_timeout_sec = first_request_timeout_sec
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._pods: dict[str, dict[str, Any]] = {}
        self._baseline_pods: set[str] = set()
        self._events: list[dict[str, Any]] = []
        self._hpa_decision: str = MISSING
        self._procs: list[subprocess.Popen[str]] = []
        self._log_threads: dict[str, threading.Thread] = {}
        self._first_request: dict[str, str] = {}

    def _kubectl_watch(self, args: list[str]) -> subprocess.Popen[str]:
        proc = subprocess.Popen(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
        )
        self._procs.append(proc)
        return proc

    def _watch_loop(self, args: list[str], kind: str) -> None:
        proc = self._kubectl_watch(args)
        assert proc.stdout is not None
        try:
            for obj in iter_json_documents(proc.stdout):
                if self._stop.is_set():
                    break
                resource = unwrap_watch(obj)
                if resource is None:
                    continue
                if kind == "pod":
                    name = resource.get("metadata", {}).get("name")
                    if not name:
                        continue
                    with self._lock:
                        self._pods[name] = resource
                    self._maybe_follow_logs(name)
                elif kind == "event":
                    with self._lock:
                        self._events.append(resource)
                    self._ingest_event(resource)
                elif kind == "hpa":
                    self._ingest_hpa(resource)
        except Exception as exc:
            print(f"WATCH_ERROR kind={kind} err={exc}", file=sys.stderr)
        finally:
            if proc.poll() is None:
                proc.kill()
            if kind != "hpa":
                print(f"WATCH_ENDED kind={kind} rc={proc.poll()}", file=sys.stderr)

    def _maybe_follow_logs(self, pod_name: str) -> None:
        if pod_name in self._baseline_pods:
            return
        if pod_name in self._log_threads:
            return
        thread = threading.Thread(
            target=self._follow_logs, args=(pod_name,), daemon=True
        )
        self._log_threads[pod_name] = thread
        thread.start()

    def _follow_logs(self, pod_name: str) -> None:
        deadline = time.time() + self.timeout_sec
        while time.time() < deadline and not self._stop.is_set():
            proc = subprocess.Popen(
                [
                    "kubectl",
                    "logs",
                    "-n",
                    self.namespace,
                    "-c",
                    self.container_name,
                    "-f",
                    "--tail=20",
                    pod_name,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            self._procs.append(proc)
            assert proc.stdout is not None
            try:
                for line in proc.stdout:
                    if self._stop.is_set():
                        return
                    match = FIRST_REQUEST_RE.match(line.strip())
                    if match:
                        with self._lock:
                            self._first_request[pod_name] = match.group(1)
                        return
            except Exception:
                pass
            finally:
                proc.kill()
            time.sleep(1)

    def _ingest_hpa(self, hpa: dict[str, Any]) -> None:
        last_scale = hpa.get("status", {}).get("lastScaleTime")
        ts = parse_rfc3339(last_scale)
        if ts is None:
            return
        with self._lock:
            if self._hpa_decision == MISSING:
                self._hpa_decision = format_rfc3339(ts)
            else:
                prev = parse_rfc3339(self._hpa_decision)
                if prev is None or ts > prev:
                    self._hpa_decision = format_rfc3339(ts)

    def _ingest_event(self, event: dict[str, Any]) -> None:
        reason = event.get("reason") or ""
        if reason == "SuccessfulRescale":
            ts = parse_rfc3339(
                event.get("eventTime")
                or event.get("lastTimestamp")
                or event.get("firstTimestamp")
            )
            if ts is not None:
                with self._lock:
                    self._hpa_decision = format_rfc3339(ts)

    def _event_for_pod(self, pod_name: str, reason: str) -> dict[str, Any] | None:
        matches = []
        for event in self._events:
            involved = event.get("involvedObject") or {}
            if involved.get("name") != pod_name:
                continue
            if event.get("reason") != reason:
                continue
            matches.append(event)
        if not matches:
            return None
        return matches[-1]

    def _row_for_pod(self, pod_name: str, pod: dict[str, Any]) -> dict[str, Any]:
        pulled = self._event_for_pod(pod_name, "Pulled")
        pulling = self._event_for_pod(pod_name, "Pulling")
        started_evt = self._event_for_pod(pod_name, "Started")
        image_cached = MISSING
        pull_ms = MISSING
        pull_end = MISSING
        if pulled is not None:
            image_cached, pull_ms = parse_pull_message(pulled.get("message") or "")
            pull_end = format_rfc3339(
                parse_rfc3339(
                    pulled.get("eventTime")
                    or pulled.get("lastTimestamp")
                    or pulled.get("firstTimestamp")
                )
            )
        pull_start = MISSING
        if pulling is not None:
            pull_start = format_rfc3339(
                parse_rfc3339(
                    pulling.get("eventTime")
                    or pulling.get("lastTimestamp")
                    or pulling.get("firstTimestamp")
                )
            )
        container_started = main_container_started(pod, self.container_name)
        if container_started == MISSING and started_evt is not None:
            container_started = format_rfc3339(
                parse_rfc3339(
                    started_evt.get("eventTime")
                    or started_evt.get("lastTimestamp")
                    or started_evt.get("firstTimestamp")
                )
            )
        created = parse_rfc3339(pod.get("metadata", {}).get("creationTimestamp"))
        init_sec = init_sleep_seconds(pod, self.init_name)
        row = {
            "pod": pod_name,
            "hpa_decision": self._hpa_decision,
            "pod_created": format_rfc3339(created),
            "PodScheduled": condition_time(pod, "PodScheduled"),
            "PodReadyToStartContainers": condition_time(
                pod, "PodReadyToStartContainers"
            ),
            "image_pull_start": pull_start,
            "image_pull_end": pull_end,
            "image_pull_duration_ms": pull_ms,
            "container_started": container_started,
            "ContainersReady": condition_time(pod, "ContainersReady"),
            "Ready": condition_time(pod, "Ready"),
            "first_request_served": self._first_request.get(pod_name, MISSING),
            "image_cached": image_cached,
            "init_sleep_observed_sec": MISSING if init_sec is None else str(init_sec),
        }
        return row

    def start_watches(self) -> None:
        baseline = subprocess.check_output(
            [
                "kubectl",
                "get",
                "pods",
                "-n",
                self.namespace,
                "-l",
                self.selector,
                "-o",
                "jsonpath={.items[*].metadata.name}",
            ],
            text=True,
        ).split()
        self._baseline_pods = set(baseline)
        watch_common = [
            "kubectl",
            "get",
            "-n",
            self.namespace,
            "--watch",
            "--output-watch-events",
            "-o",
            "json",
        ]
        threading.Thread(
            target=self._watch_loop,
            args=(watch_common + ["pods", "-l", self.selector], "pod"),
            daemon=True,
        ).start()
        threading.Thread(
            target=self._watch_loop,
            args=(watch_common + ["events"], "event"),
            daemon=True,
        ).start()
        hpa_args = watch_common + ["hpa"]
        threading.Thread(
            target=self._watch_loop,
            args=(hpa_args, "hpa"),
            daemon=True,
        ).start()

    def collect(self) -> list[dict[str, Any]]:
        deadline = time.time() + self.timeout_sec
        new_ready: list[str] = []
        while time.time() < deadline and not self._stop.is_set():
            with self._lock:
                candidates = [
                    name
                    for name, pod in self._pods.items()
                    if name not in self._baseline_pods
                    and condition_time(pod, "Ready") != MISSING
                ]
            for name in candidates:
                if name not in new_ready:
                    new_ready.append(name)
            if len(new_ready) >= self.expect_pods:
                # Wait briefly for first-request log after Ready.
                wait_until = time.time() + self.first_request_timeout_sec
                while time.time() < wait_until:
                    with self._lock:
                        if all(n in self._first_request for n in new_ready[: self.expect_pods]):
                            break
                    time.sleep(0.2)
                break
            time.sleep(0.2)
        else:
            if len(new_ready) < self.expect_pods:
                print(
                    f"ERROR: COLD_START_COLLECT_TIMEOUT timeout_sec={self.timeout_sec} "
                    f"ready_new={len(new_ready)} expected={self.expect_pods}",
                    file=sys.stderr,
                )
        self._stop.set()
        for proc in self._procs:
            proc.kill()
        rows = []
        with self._lock:
            for name in new_ready[: self.expect_pods]:
                rows.append(self._row_for_pod(name, self._pods[name]))
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        with self.output_path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, sort_keys=False) + "\n")
        return rows


def main() -> int:
    parser = argparse.ArgumentParser(description="Watch-based cold-start collector")
    parser.add_argument("--namespace", default="hpa-eval")
    parser.add_argument("--selector", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--timeout-sec", type=int, default=180)
    parser.add_argument("--container-name", default="hpa-eval-app")
    parser.add_argument("--init-name", default="delay")
    parser.add_argument("--expect-pods", type=int, default=1)
    parser.add_argument("--first-request-timeout-sec", type=int, default=30)
    args = parser.parse_args()
    collector = ColdStartCollector(
        namespace=args.namespace,
        selector=args.selector,
        output_path=Path(args.output),
        timeout_sec=args.timeout_sec,
        container_name=args.container_name,
        init_name=args.init_name,
        expect_pods=args.expect_pods,
        first_request_timeout_sec=args.first_request_timeout_sec,
    )
    collector.start_watches()
    # Let watches attach before the caller scales.
    print("COLD_START_WATCH_READY", flush=True)
    rows = collector.collect()
    print(f"COLD_START_EVENTS_WRITTEN path={args.output} rows={len(rows)}")
    return 0 if rows else 1


if __name__ == "__main__":
    raise SystemExit(main())
