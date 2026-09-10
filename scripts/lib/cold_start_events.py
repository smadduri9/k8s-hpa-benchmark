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
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MISSING = "MISSING"
LT_1S = "<1s"

ASSOCIATION_VERIFIED = "verified"
ASSOCIATION_CONSISTENT = "consistent"
ASSOCIATION_MISSING = "MISSING"

REASON_HPA_DECISION_AFTER_POD_CREATED = "HPA_DECISION_AFTER_POD_CREATED"
REASON_HPA_DECISION_NO_SCALE_OUT_MATCH = "HPA_DECISION_NO_SCALE_OUT_MATCH"
REASON_SCALE_OUT_SEQUENCE_MISMATCH = "SCALE_OUT_SEQUENCE_MISMATCH"
REASON_NO_QUALIFYING_REQUEST_IN_WINDOW = "NO_QUALIFYING_REQUEST_IN_WINDOW"
REASON_LOG_FOLLOW_FAILED = "LOG_FOLLOW_FAILED"
REASON_POD_NOT_READY_BEFORE_COLLECT_END = "POD_NOT_READY_BEFORE_COLLECT_END"

SCALE_OUT_SEQUENCE_SAMPLE_LIMITATION = (
    "replica_series is 15s-sampled; comparison is consecutive spec_replicas "
    "values only, not event timing"
)

HPA_DECISION_COVERAGE_THRESHOLD = 0.90
HPA_DECISION_COVERAGE_RATIONALE = (
    "rows missing SuccessfulRescale are not a random sample. Kubernetes aggregates "
    "Events under load, so the missing rows are disproportionately from the busiest "
    "scale-out bursts — exactly the events with the longest expected decision lag. "
    "Publishing a distribution over the surviving subset would understate the tail."
)

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
RESCALE_NEW_RE = re.compile(r"New size:\s*(\d+)", re.IGNORECASE)
RESCALE_OLD_RE = re.compile(r"old size:\s*(\d+)", re.IGNORECASE)


@dataclass
class ScaleOutEvent:
    ts: datetime
    old_replicas: int
    new_replicas: int
    assigned: list[str] = field(default_factory=list)

    @property
    def capacity(self) -> int:
        return max(0, self.new_replicas - self.old_replicas)


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


def parse_successful_rescale(message: str) -> tuple[int, int | None]:
    new_match = RESCALE_NEW_RE.search(message)
    if not new_match:
        return 0, None
    new_replicas = int(new_match.group(1))
    old_match = RESCALE_OLD_RE.search(message)
    old_replicas = int(old_match.group(1)) if old_match else None
    return new_replicas, old_replicas


def event_timestamp(event: dict[str, Any]) -> datetime | None:
    # Occurrence time, not series start. eventTime / firstTimestamp are when
    # the Event object was created (cold-start 0→4 can own the series).
    series = event.get("series")
    last_observed = None
    if isinstance(series, dict):
        last_observed = series.get("lastObservedTime")
    return parse_rfc3339(
        last_observed
        or event.get("lastTimestamp")
        or event.get("eventTime")
        or event.get("firstTimestamp")
    )


def consecutive_unique(values: list[int]) -> list[int]:
    seq: list[int] = []
    for value in values:
        if not seq or seq[-1] != value:
            seq.append(value)
    return seq


def format_spec_sequence(values: list[int]) -> str:
    if not values:
        return "EMPTY"
    return ">".join(str(v) for v in values)


def sampled_spec_sequence(replica_series_path: Path) -> list[int]:
    import csv

    values: list[int] = []
    with replica_series_path.open(encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            raw = (row.get("spec_replicas") or "").strip()
            if raw == "":
                continue
            values.append(int(raw))
    return consecutive_unique(values)


def event_spec_sequence(seed: int | None, chain: list[dict[str, Any]]) -> list[int]:
    values: list[int] = []
    if seed is not None:
        values.append(seed)
    for step in chain:
        values.append(int(step["new"]))
    return consecutive_unique(values)


def invalidate_hpa_decision_rows(
    rows: list[dict[str, Any]], reason: str
) -> list[dict[str, Any]]:
    cleared = []
    for row in rows:
        updated = dict(row)
        updated["hpa_decision"] = MISSING
        updated["hpa_decision_association"] = ASSOCIATION_MISSING
        updated["hpa_decision_source"] = MISSING
        updated["hpa_decision_reason"] = reason
        cleared.append(updated)
    return cleared


def apply_scale_out_sequence_guard(
    jsonl_path: Path,
    sequence_path: Path,
    replica_series_path: Path,
) -> int:
    """Compare event chain to sampled spec_replicas. Rewrite jsonl on mismatch.

    Returns 0 always: a mismatch withholds associations; it does not fail the arm.
    """
    if not jsonl_path.is_file():
        print(
            "SCALE_OUT_SEQUENCE_SKIPPED reason=jsonl_missing "
            f"path={jsonl_path}",
            flush=True,
        )
        return 0
    if not sequence_path.is_file():
        print(
            "SCALE_OUT_SEQUENCE_SKIPPED reason=sequence_sidecar_missing "
            f"path={sequence_path}",
            flush=True,
        )
        return 0
    if not replica_series_path.is_file():
        print(
            "SCALE_OUT_SEQUENCE_SKIPPED reason=replica_series_missing "
            f"path={replica_series_path}",
            flush=True,
        )
        return 0

    payload = json.loads(sequence_path.read_text(encoding="utf-8"))
    seed = payload.get("seed")
    chain = payload.get("chain") or []
    if seed is None:
        print("SCALE_OUT_SEQUENCE_SKIPPED reason=no_seed", flush=True)
        return 0

    event_seq = event_spec_sequence(int(seed), chain)
    sampled_seq = sampled_spec_sequence(replica_series_path)
    event_text = format_spec_sequence(event_seq)
    sampled_text = format_spec_sequence(sampled_seq)
    if event_seq == sampled_seq:
        print(
            f"SCALE_OUT_SEQUENCE_OK event={event_text} sampled={sampled_text} "
            f"limitation={SCALE_OUT_SEQUENCE_SAMPLE_LIMITATION}",
            flush=True,
        )
        return 0

    print(
        f"ERROR: SCALE_OUT_SEQUENCE_MISMATCH event={event_text} "
        f"sampled={sampled_text} "
        f"limitation={SCALE_OUT_SEQUENCE_SAMPLE_LIMITATION}",
        file=sys.stderr,
        flush=True,
    )
    rows = []
    with jsonl_path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    cleared = invalidate_hpa_decision_rows(rows, REASON_SCALE_OUT_SEQUENCE_MISMATCH)
    tmp_path = jsonl_path.with_suffix(jsonl_path.suffix + ".tmp")
    with tmp_path.open("w", encoding="utf-8") as handle:
        for row in cleared:
            handle.write(json.dumps(row, sort_keys=False) + "\n")
    tmp_path.replace(jsonl_path)
    if cleared:
        emit_hpa_decision_coverage(cleared)
    return 0


def associate_hpa_decision(
    pod_name: str,
    pod_created: datetime,
    scale_outs: list[ScaleOutEvent],
) -> tuple[str, str, str, str]:
    """Return hpa_decision, source, association, reason."""
    verified_event: ScaleOutEvent | None = None
    for event in reversed(scale_outs):
        if event.ts > pod_created:
            continue
        if event.capacity <= 0:
            continue
        if len(event.assigned) >= event.capacity:
            continue
        verified_event = event
        break

    if verified_event is not None:
        verified_event.assigned.append(pod_name)
        decision = format_rfc3339(verified_event.ts)
        if verified_event.ts > pod_created:
            return (
                MISSING,
                MISSING,
                ASSOCIATION_MISSING,
                REASON_HPA_DECISION_AFTER_POD_CREATED,
            )
        return decision, "SuccessfulRescale", ASSOCIATION_VERIFIED, MISSING

    consistent_candidates = [
        event
        for event in scale_outs
        if event.capacity > 0 and event.ts <= pod_created
    ]
    if consistent_candidates:
        event = consistent_candidates[-1]
        if event.ts > pod_created:
            return (
                MISSING,
                MISSING,
                ASSOCIATION_MISSING,
                REASON_HPA_DECISION_AFTER_POD_CREATED,
            )
        return format_rfc3339(event.ts), "SuccessfulRescale", ASSOCIATION_CONSISTENT, MISSING

    future_scale_outs = [
        event
        for event in scale_outs
        if event.capacity > 0 and event.ts > pod_created
    ]
    if future_scale_outs:
        return (
            MISSING,
            MISSING,
            ASSOCIATION_MISSING,
            REASON_HPA_DECISION_AFTER_POD_CREATED,
        )
    return (
        MISSING,
        MISSING,
        ASSOCIATION_MISSING,
        REASON_HPA_DECISION_NO_SCALE_OUT_MATCH,
    )


def row_has_hpa_decision(row: dict[str, Any]) -> bool:
    decision = row.get("hpa_decision")
    if decision in (None, "", MISSING):
        return False
    return row.get("hpa_decision_association") == ASSOCIATION_VERIFIED


def hpa_decision_coverage_report(rows: list[dict[str, Any]]) -> dict[str, Any]:
    rows_total = len(rows)
    rows_with = sum(1 for row in rows if row_has_hpa_decision(row))
    coverage = (rows_with / rows_total) if rows_total else 0.0
    publish_decision = coverage >= HPA_DECISION_COVERAGE_THRESHOLD
    return {
        "rows_total": rows_total,
        "rows_with_hpa_decision": rows_with,
        "coverage": coverage,
        "threshold": HPA_DECISION_COVERAGE_THRESHOLD,
        "publish_decision_to_serving": publish_decision,
        "publish_pod_creation_to_serving": True,
        "withheld": "decision-to-serving" if not publish_decision else None,
        "rationale": HPA_DECISION_COVERAGE_RATIONALE if not publish_decision else None,
    }


def format_hpa_decision_coverage_line(report: dict[str, Any]) -> str:
    coverage = report["coverage"]
    coverage_text = f"{coverage:.2f}" if report["rows_total"] else "MISSING"
    publish = (
        "decision-to-serving"
        if report["publish_decision_to_serving"]
        else "pod-creation-to-serving"
    )
    parts = [
        "HPA_DECISION_COVERAGE",
        f"rows_total={report['rows_total']}",
        f"rows_with_hpa_decision={report['rows_with_hpa_decision']}",
        f"coverage={coverage_text}",
        f"threshold={report['threshold']:.2f}",
        f"publish={publish}",
    ]
    if report["withheld"]:
        parts.append(f"withheld={report['withheld']}")
    return " ".join(parts)


def emit_hpa_decision_coverage(rows: list[dict[str, Any]]) -> None:
    report = hpa_decision_coverage_report(rows)
    print(format_hpa_decision_coverage_line(report), flush=True)
    if report["withheld"]:
        print(f"HPA_DECISION_COVERAGE_RATIONALE {report['rationale']}", flush=True)


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
        capture_all_scale_out: bool,
    ) -> None:
        self.namespace = namespace
        self.selector = selector
        self.output_path = output_path
        self.timeout_sec = timeout_sec
        self.container_name = container_name
        self.init_name = init_name
        self.expect_pods = expect_pods
        self.first_request_timeout_sec = first_request_timeout_sec
        self.capture_all_scale_out = capture_all_scale_out
        # RLock: _emit_rows holds this while _row_for_pod → _first_request_fields
        # acquires it again. A non-reentrant Lock deadlocks there, which is what
        # hung the ramp arm after shutdown (jsonl never written; wait unbounded).
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._pods: dict[str, dict[str, Any]] = {}
        self._baseline_pods: set[str] = set()
        self._events: list[dict[str, Any]] = []
        self._scale_outs: list[ScaleOutEvent] = []
        self._current_replicas: int | None = None
        self._scale_out_seed: int | None = None
        self._scale_out_chain: list[dict[str, Any]] = []
        self._collection_started_at: datetime | None = None
        self._procs: list[subprocess.Popen[str]] = []
        self._log_threads: dict[str, threading.Thread] = {}
        self._first_request: dict[str, str] = {}
        self._first_request_reason: dict[str, str] = {}
        self._log_follow_failed: dict[str, bool] = {}
        self._ready_at: dict[str, datetime] = {}
        self._first_request_deadline: dict[str, float] = {}
        self._watch_rc: dict[str, int | None] = {}
        self.truncated_reason: str | None = None

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
                        ready_ts = parse_rfc3339(
                            next(
                                (
                                    cond.get("lastTransitionTime")
                                    for cond in resource.get("status", {}).get("conditions") or []
                                    if cond.get("type") == "Ready"
                                    and cond.get("status") == "True"
                                ),
                                None,
                            )
                        )
                        if (
                            name not in self._baseline_pods
                            and ready_ts is not None
                            and name not in self._ready_at
                        ):
                            self._ready_at[name] = ready_ts
                            self._first_request_deadline[name] = (
                                time.time() + self.first_request_timeout_sec
                            )
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
            rc = proc.poll()
            if rc is None and not self._stop.is_set():
                # Stream ended while kubectl still ran. SIGTERM so an unexpected
                # SIGKILL (rc=-9) is distinguishable from this path.
                proc.terminate()
                try:
                    rc = proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    rc = proc.poll()
            if rc is None:
                rc = proc.poll()
            with self._lock:
                self._watch_rc[kind] = rc
            if kind in ("pod", "event"):
                print(
                    f"WATCH_ENDED kind={kind} rc={rc} stop={self._stop.is_set()}",
                    file=sys.stderr,
                    flush=True,
                )

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

    def _scan_log_text(self, pod_name: str, text: str) -> bool:
        for line in text.splitlines():
            match = FIRST_REQUEST_RE.match(line.strip())
            if match:
                with self._lock:
                    self._first_request[pod_name] = match.group(1)
                return True
        return False

    def _follow_logs(self, pod_name: str) -> None:
        deadline = time.time() + self.timeout_sec
        while time.time() < deadline and not self._stop.is_set():
            try:
                initial = subprocess.run(
                    [
                        "kubectl",
                        "logs",
                        "-n",
                        self.namespace,
                        "-c",
                        self.container_name,
                        pod_name,
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    check=False,
                )
            except (subprocess.SubprocessError, OSError):
                with self._lock:
                    self._log_follow_failed[pod_name] = True
                time.sleep(1)
                continue

            if initial.returncode != 0:
                with self._lock:
                    self._log_follow_failed[pod_name] = True
                time.sleep(1)
                continue

            if self._scan_log_text(pod_name, initial.stdout):
                return

            proc = subprocess.Popen(
                [
                    "kubectl",
                    "logs",
                    "-n",
                    self.namespace,
                    "-c",
                    self.container_name,
                    "-f",
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
                    if self._scan_log_text(pod_name, line):
                        return
            except Exception:
                with self._lock:
                    self._log_follow_failed[pod_name] = True
            finally:
                proc.kill()
            time.sleep(1)

    def _ingest_hpa(self, hpa: dict[str, Any]) -> None:
        # lastScaleTime is not the SuccessfulRescale event. Do not use it for hpa_decision.
        _ = hpa

    def _log_rescale(self, action: str, **fields: Any) -> None:
        parts = [f"SUCCESSFUL_RESCALE {action}"]
        for key, value in fields.items():
            parts.append(f"{key}={value}")
        print(" ".join(parts), file=sys.stderr, flush=True)

    def _ingest_event(self, event: dict[str, Any]) -> None:
        reason = event.get("reason") or ""
        if reason != "SuccessfulRescale":
            return
        ts = event_timestamp(event)
        ts_text = format_rfc3339(ts) if ts is not None else MISSING
        message = event.get("message") or ""
        new_replicas, old_replicas = parse_successful_rescale(message)
        if ts is None:
            self._log_rescale(
                "dropped",
                ts=MISSING,
                old=old_replicas if old_replicas is not None else MISSING,
                new=new_replicas if new_replicas > 0 else MISSING,
                reason="unparseable_timestamp",
            )
            return
        if (
            self._collection_started_at is not None
            and ts < self._collection_started_at
        ):
            self._log_rescale(
                "dropped",
                ts=ts_text,
                old=old_replicas if old_replicas is not None else MISSING,
                new=new_replicas if new_replicas > 0 else MISSING,
                reason="before_collection_started_at",
            )
            return
        if new_replicas <= 0:
            self._log_rescale(
                "dropped",
                ts=ts_text,
                old=old_replicas if old_replicas is not None else MISSING,
                new=MISSING,
                reason="unparseable_new_size",
            )
            return
        with self._lock:
            if old_replicas is None:
                old_replicas = self._current_replicas
            if old_replicas is None:
                self._log_rescale(
                    "dropped",
                    ts=ts_text,
                    old=MISSING,
                    new=new_replicas,
                    reason="no_old_replicas",
                )
                return
            step = {
                "ts": ts_text,
                "old": old_replicas,
                "new": new_replicas,
            }
            if new_replicas > old_replicas:
                self._scale_outs.append(
                    ScaleOutEvent(ts, old_replicas, new_replicas)
                )
                step["kind"] = "scale_out"
                self._scale_out_chain.append(step)
                self._log_rescale(
                    "accepted",
                    ts=ts_text,
                    old=old_replicas,
                    new=new_replicas,
                )
            else:
                step["kind"] = "not_scale_out"
                self._scale_out_chain.append(step)
                self._log_rescale(
                    "chain",
                    ts=ts_text,
                    old=old_replicas,
                    new=new_replicas,
                    reason="not_scale_out",
                )
            self._current_replicas = new_replicas

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

    def _first_request_fields(self, pod_name: str) -> tuple[str, str]:
        with self._lock:
            if pod_name in self._first_request:
                return self._first_request[pod_name], MISSING
            if self._log_follow_failed.get(pod_name):
                return MISSING, REASON_LOG_FOLLOW_FAILED
            if pod_name not in self._ready_at:
                return MISSING, REASON_POD_NOT_READY_BEFORE_COLLECT_END
            return MISSING, REASON_NO_QUALIFYING_REQUEST_IN_WINDOW

    def _row_for_pod(self, pod_name: str, pod: dict[str, Any]) -> dict[str, Any]:
        pulled = self._event_for_pod(pod_name, "Pulled")
        pulling = self._event_for_pod(pod_name, "Pulling")
        started_evt = self._event_for_pod(pod_name, "Started")
        image_cached = MISSING
        pull_ms = MISSING
        pull_end = MISSING
        if pulled is not None:
            image_cached, pull_ms = parse_pull_message(pulled.get("message") or "")
            pull_end = format_rfc3339(event_timestamp(pulled))
        pull_start = MISSING
        if pulling is not None:
            pull_start = format_rfc3339(event_timestamp(pulling))
        container_started = main_container_started(pod, self.container_name)
        if container_started == MISSING and started_evt is not None:
            container_started = format_rfc3339(event_timestamp(started_evt))
        created = parse_rfc3339(pod.get("metadata", {}).get("creationTimestamp"))
        init_sec = init_sleep_seconds(pod, self.init_name)
        decision, source, association, decision_reason = associate_hpa_decision(
            pod_name,
            created if created is not None else datetime.min.replace(tzinfo=timezone.utc),
            self._scale_outs,
        )
        first_request, first_request_reason = self._first_request_fields(pod_name)
        row = {
            "pod": pod_name,
            "hpa_decision": decision,
            "hpa_decision_association": association,
            "hpa_decision_reason": decision_reason,
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
            "first_request_served": first_request,
            "first_request_served_reason": first_request_reason,
            "image_cached": image_cached,
            "init_sleep_observed_sec": MISSING if init_sec is None else str(init_sec),
            "hpa_decision_source": source,
        }
        return row

    def _seed_current_replicas(self) -> None:
        raw = subprocess.check_output(
            [
                "kubectl",
                "get",
                "deploy",
                "-n",
                self.namespace,
                "-l",
                self.selector,
                "-o",
                "jsonpath={.items[*].spec.replicas}",
            ],
            text=True,
        ).split()
        if len(raw) != 1:
            print(
                f"ERROR: SCALE_OUT_SEED_FAILED count={len(raw)} "
                f"selector={self.selector}",
                file=sys.stderr,
                flush=True,
            )
            raise SystemExit("SCALE_OUT_SEED_FAILED")
        try:
            seed = int(raw[0])
        except ValueError:
            print(
                f"ERROR: SCALE_OUT_SEED_FAILED value={raw[0]!r} "
                f"selector={self.selector}",
                file=sys.stderr,
                flush=True,
            )
            raise SystemExit("SCALE_OUT_SEED_FAILED")
        self._current_replicas = seed
        self._scale_out_seed = seed
        print(f"SCALE_OUT_SEED spec_replicas={seed}", flush=True)

    def _write_scale_out_sequence(self) -> None:
        with self._lock:
            payload = {
                "seed": self._scale_out_seed,
                "chain": list(self._scale_out_chain),
            }
        path = self.output_path.with_name("scale_out_sequence.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(
            f"SCALE_OUT_SEQUENCE_WRITTEN path={path} "
            f"seed={self._scale_out_seed} steps={len(payload['chain'])}",
            flush=True,
        )

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
        self._seed_current_replicas()
        self._collection_started_at = datetime.now(timezone.utc)
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

    def _data_watches_ended(self) -> bool:
        with self._lock:
            return "pod" in self._watch_rc and "event" in self._watch_rc

    def _watch_death_line(self) -> str:
        with self._lock:
            pod_rc = self._watch_rc.get("pod")
            event_rc = self._watch_rc.get("event")
        return (
            f"COLD_START_WATCHES_DIED pod_rc={pod_rc} event_rc={event_rc} "
            "collection=truncated collector cannot outlive its data sources"
        )

    def _reap_procs(self) -> None:
        # Terminate, then SIGKILL stragglers. Do not block indefinitely on wait.
        for proc in list(self._procs):
            if proc.poll() is None:
                proc.terminate()
        deadline = time.time() + 2.0
        for proc in list(self._procs):
            if proc.poll() is not None:
                continue
            remaining = deadline - time.time()
            if remaining > 0:
                try:
                    proc.wait(timeout=remaining)
                    continue
                except subprocess.TimeoutExpired:
                    pass
            proc.kill()
            try:
                proc.wait(timeout=1)
            except subprocess.TimeoutExpired:
                print(
                    f"WATCH_REAP_TIMEOUT pid={proc.pid}",
                    file=sys.stderr,
                    flush=True,
                )

    def _emit_rows(self, new_ready: list[str]) -> list[dict[str, Any]]:
        if self.capture_all_scale_out:
            tracked = list(new_ready)
        else:
            tracked = list(new_ready[: self.expect_pods])
        with self._lock:
            tracked.sort(
                key=lambda name: parse_rfc3339(
                    (self._pods.get(name) or {})
                    .get("metadata", {})
                    .get("creationTimestamp")
                )
                or datetime.min.replace(tzinfo=timezone.utc)
            )
            rows = []
            for name in tracked:
                pod = self._pods.get(name)
                if pod is None:
                    continue
                rows.append(self._row_for_pod(name, pod))
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        with self.output_path.open("w", encoding="utf-8") as handle:
            for row in rows:
                handle.write(json.dumps(row, sort_keys=False) + "\n")
        return rows

    def _mark_watches_died(self) -> None:
        if self.truncated_reason == "COLD_START_WATCHES_DIED":
            return
        self.truncated_reason = "COLD_START_WATCHES_DIED"
        print(f"ERROR: {self._watch_death_line()}", file=sys.stderr, flush=True)

    def collect(self) -> list[dict[str, Any]]:
        deadline = time.time() + self.timeout_sec
        new_ready: list[str] = []
        try:
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
                if self._data_watches_ended():
                    self._mark_watches_died()
                    break
                if (
                    not self.capture_all_scale_out
                    and len(new_ready) >= self.expect_pods
                ):
                    wait_until = time.time() + self.first_request_timeout_sec
                    while time.time() < wait_until:
                        if self._data_watches_ended():
                            self._mark_watches_died()
                            break
                        with self._lock:
                            if all(
                                n in self._first_request
                                for n in new_ready[: self.expect_pods]
                            ):
                                break
                        time.sleep(0.2)
                    break
                time.sleep(0.2)
            else:
                if (
                    not self.capture_all_scale_out
                    and len(new_ready) < self.expect_pods
                ):
                    print(
                        f"ERROR: COLD_START_COLLECT_TIMEOUT timeout_sec={self.timeout_sec} "
                        f"ready_new={len(new_ready)} expected={self.expect_pods}",
                        file=sys.stderr,
                    )
            return self._emit_rows(new_ready)
        finally:
            self._stop.set()
            self._reap_procs()
            self._write_scale_out_sequence()


def main() -> int:
    parser = argparse.ArgumentParser(description="Watch-based cold-start collector")
    parser.add_argument("--namespace", default="hpa-eval")
    parser.add_argument("--selector", required=False)
    parser.add_argument("--output", required=True)
    parser.add_argument("--timeout-sec", type=int, default=180)
    parser.add_argument("--container-name", default="hpa-eval-app")
    parser.add_argument("--init-name", default="delay")
    parser.add_argument("--expect-pods", type=int, default=1)
    parser.add_argument("--first-request-timeout-sec", type=int, default=30)
    parser.add_argument(
        "--capture-all-scale-out",
        action="store_true",
        help="Record every baseline-above pod Ready during timeout_sec (measured arm).",
    )
    parser.add_argument(
        "--validate-sequence",
        action="store_true",
        help="Compare event chain to replica_series spec_replicas; rewrite jsonl on mismatch.",
    )
    parser.add_argument("--replica-series")
    parser.add_argument("--sequence-file")
    args = parser.parse_args()
    if args.validate_sequence:
        if not args.replica_series:
            print(
                "ERROR: SCALE_OUT_SEQUENCE_UNREADABLE reason=replica_series_required",
                file=sys.stderr,
            )
            return 1
        jsonl_path = Path(args.output)
        sequence_path = (
            Path(args.sequence_file)
            if args.sequence_file
            else jsonl_path.with_name("scale_out_sequence.json")
        )
        return apply_scale_out_sequence_guard(
            jsonl_path, sequence_path, Path(args.replica_series)
        )
    if not args.selector:
        parser.error("--selector is required unless --validate-sequence")
    collector = ColdStartCollector(
        namespace=args.namespace,
        selector=args.selector,
        output_path=Path(args.output),
        timeout_sec=args.timeout_sec,
        container_name=args.container_name,
        init_name=args.init_name,
        expect_pods=args.expect_pods,
        first_request_timeout_sec=args.first_request_timeout_sec,
        capture_all_scale_out=args.capture_all_scale_out,
    )
    collector.start_watches()
    print("COLD_START_WATCH_READY", flush=True)
    rows = collector.collect()
    print(f"COLD_START_EVENTS_WRITTEN path={args.output} rows={len(rows)}")
    if rows:
        emit_hpa_decision_coverage(rows)
    if collector.truncated_reason:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
