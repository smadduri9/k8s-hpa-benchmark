# Error budget policy

This is a single-maintainer benchmark repository. There is no review board; policy decisions are made by **Sriram Madduri** (sole maintainer).

## Service level objective

**SLO:** **99%** of valid **`GET /cpu?intensity=low`** requests complete with client-observed response time **faster than 500 ms**.

- **Allowed miss rate:** 1% over the compliance window.
- **Scope:** `/cpu` only. `GET /` is not instrumented in the service histogram and is excluded from this SLO.
- **Measurement on existing runs:** approximate Locust percentile-grid brackets (`analysis/sli_locust_grid.py`), labelled approximate and failure-inclusive. See [`RESULTS.md`](../RESULTS.md#slo-and-error-budget-calibrated-runs). Exact count-based SLI requires Phase 5 bucket columns — not stored today.
- **Benchmark run duration:** each experiment is an **18-minute measurement sample**. It is **not** the compliance window.

## Compliance window

**30 days.** Error-budget consumption percentages (50%, 90%, 100%) are defined against this rolling window — not against a single benchmark run.

Published run artifacts may report brackets from one 18-minute sample. Those brackets inform investigation; they do not by themselves define window consumption unless a continuous compliance series is in place.

## Decision-maker

**Sriram Madduri** (sole maintainer). Formal SLO target revisions are recorded in [`RESULTS.md`](../RESULTS.md) with rationale. No separate approval process exists beyond that record.

## Actions by budget consumption

Consumption is the fraction of the 30-day error budget spent on latency-SLO misses (allowed miss = 1% at the 99% target). On existing runs, use the miss-rate **brackets** from `sli_locust_grid.py`; do not interpolate a point estimate.

| Consumption | Action |
|-------------|--------|
| **50%** | Investigate root cause. **No change** to planned work priority. |
| **90%** | **Reliability work takes priority** over new measurement features. |
| **100%** | **Halt new benchmark features** until the SLO is met or the target is **formally revised** in `RESULTS.md` (with the revision recorded there). |

## Current status

**The SLO is not currently met.**

Measured on calibrated runs (hybrid n=6, constant n=3) using approximate Locust brackets at **500 ms** — see [`RESULTS.md`](../RESULTS.md#slo-and-error-budget-calibrated-runs): **neither arm reaches 99%** in any repetition. Fixed arms are **fewer than 50%** faster than 500 ms in all six hybrid reps (and all three constant reps). HPA arms do better on some reps but the best hybrid bracket observed is still only **>50% and <=66%**. At any constant miss rate in those brackets, the **30-day error budget would be exhausted immediately** (for example, miss **>= 50%** consumes **>= 5000%** of the allowance).

**Consequence under this policy:** the system is at the **100% consumption threshold today**. The prescribed response is to **halt new benchmark features** until either (1) the workload meets the SLO, or (2) the target is **formally revised** in `RESULTS.md`.

**Which response applies is deferred.** The maintainer will not choose between meeting the SLO and revising the target until **Phase 5** produces **exact count-based SLIs** from stored histogram bucket counts. The numbers above are **approximate brackets** from the Locust percentile grid; they are sufficient to show the SLO is missed by a wide margin, but not sufficient to lock a formal revision or a precise compliance series.

## What this policy does not cover

- **Multi-window burn-rate alerting** (e.g. 14.4× tiers for a 99.9% SLO over 30 days). An 18-minute benchmark has no compliance period; those constants do not apply. See [`RESULTS.md`](../RESULTS.md#slo-and-error-budget-calibrated-runs). **No alert rules are committed.**
- **Locust failure rate** (availability errors). That is a separate metric from latency-SLO misses; see Locust failure sections in `RESULTS.md`.

## Related documents

- [`RESULTS.md`](../RESULTS.md#slo-and-error-budget-calibrated-runs) — measured brackets, scaling events, headline finding
- [`POSTMORTEM.md`](../POSTMORTEM.md) — unequal `minReplicas` confound and detection
- [`docs/phase5-bucket-schema.md`](phase5-bucket-schema.md) — planned exact SLI schema (not implemented)
