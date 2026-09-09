# Cold-start waterfall

Watch-based collector: `scripts/lib/cold_start_events.py`. Kind calibration harness: `scripts/calibrate_cold_start_collector.sh`. Committed calibration rows: `docs/cold-start-calibration/`.

## Waterfall stages

| Stage | Field | Source |
|-------|-------|--------|
| 1 | `hpa_decision` | Kubernetes Event `reason=SuccessfulRescale` only (`eventTime` / `lastTimestamp` / `firstTimestamp`). `status.lastScaleTime` is **not** used. Each pod is associated with the scale-out event whose replica-count transition created it (`hpa_decision_association=verified`), or timestamp-consistent only (`consistent`). Causally impossible matches (`hpa_decision` after `pod_created`) are written as `MISSING` with `hpa_decision_reason`. |
| 2 | `pod_created` | Pod `metadata.creationTimestamp` |
| 3+ | Pod conditions, image pull, container start, `Ready`, `first_request_served` | Pod status, Events, app stderr (`FIRST_REQUEST_SERVED`) |

Missing stages remain the literal string `MISSING`. Never backfill.

## HPA_DECISION_COVERAGE

Committed before any GKE harvest data exists.

```
Coverage = rows_with_hpa_decision / rows_total
THRESHOLD = 0.90
```

A row counts toward `rows_with_hpa_decision` when `hpa_decision_association` is `verified` (slot-matched scale-out only; `consistent` does not count).

**Measured arms:** `run_benchmark.sh` passes `--capture-all-scale-out` so the collector runs the full Locust window (~18 minutes) and records every pod above baseline that reaches Ready, not only the first. Calibration keeps `--expect-pods 1`.

**`first_request_served`:** when `MISSING`, `first_request_served_reason` is one of `NO_QUALIFYING_REQUEST_IN_WINDOW` (no non-`/health` request reached this pod within the post-Ready window), `LOG_FOLLOW_FAILED` (`kubectl logs` error), or `POD_NOT_READY_BEFORE_COLLECT_END`. The timeout is not extended to force a hit.

**At or above 0.90:** publish the decision-to-serving distribution, stating the coverage figure.

**Below 0.90:** withhold decision-to-serving entirely. Publish pod-creation-to-serving over all rows instead, state the coverage figure, and state why stage 1 was withheld.

**Rationale (verbatim):** rows missing SuccessfulRescale are not a random sample. Kubernetes aggregates Events under load, so the missing rows are disproportionately from the busiest scale-out bursts — exactly the events with the longest expected decision lag. Publishing a distribution over the surviving subset would understate the tail.

The collector emits `HPA_DECISION_COVERAGE` after every write. Implementation: `hpa_decision_coverage_report()` in `scripts/lib/cold_start_events.py`.

## Kind calibration (Gate A)

Record: `docs/cold-start-calibration/calibration.json`. Rows: `cached.jsonl`, `uncached.jsonl`.

HPA-triggered scale-out (min 1, max 3, CPU target 10%) with init-container `sleep 8`. `hpa_decision` populated from `SuccessfulRescale` on both rows. `CALIBRATION_RECOVERED` passes with `hpa_decision` present.

**Uncached `image_pull_duration_ms`:** the uncached row reports `image_pull_duration_ms=15` from kind's local registry (`cal-registry:5000`). The **parser** is proven (`image_cached=false`, duration parsed from the Pulled message). The **realistic pull-duration range is untested** on kind. GKE pulls from Artifact Registry will take seconds. Do not cite 15ms as evidence about the metric's behaviour under real conditions.
