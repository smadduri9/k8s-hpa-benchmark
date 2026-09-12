# Error budget policy

This is a single-maintainer benchmark repository. There is no review board; policy decisions are made by **Sriram Madduri** (sole maintainer).

## Service level objective

**SLO:** **99%** of valid **`GET /cpu?intensity=low`** requests complete with client-observed response time **faster than 500 ms**.

- **Allowed miss rate:** 1% over the compliance window.
- **Scope:** `/cpu` only. `GET /` is not instrumented in the service histogram and is excluded from this SLO.
- **Measurement:** historical Locust percentile-grid brackets are approximate and failure-inclusive. Phase 5 server-side histogram columns are implemented, but they cannot establish this client-observed SLO. The public v1.1 package publishes no SLO compliance result.
- **Benchmark run duration:** each experiment is an **18-minute measurement sample**. It is **not** the compliance window.

## Compliance window

**30 days.** Error-budget consumption percentages (50%, 90%, 100%) are defined against this rolling window — not against a single benchmark run.

Published run artifacts may report brackets from one 18-minute sample. Those brackets inform investigation; they do not by themselves define window consumption unless a continuous compliance series is in place.

## Decision-maker

**Sriram Madduri** (sole maintainer). Formal SLO target revisions are recorded in [`RESULTS.md`](https://github.com/smadduri9/k8s-hpa-benchmark/blob/main/RESULTS.md) with rationale. No separate approval process exists beyond that record.

## Actions by budget consumption

Consumption is the fraction of the 30-day error budget spent on latency-SLO misses (allowed miss = 1% at the 99% target). Apply these actions only with a measured compliance series. A short benchmark sample does not establish actual 30-day budget consumption.

| Consumption | Action |
|-------------|--------|
| **50%** | Investigate root cause. **No change** to planned work priority. |
| **90%** | **Reliability work takes priority** over new measurement features. |
| **100%** | **Halt new benchmark features** until the SLO is met or the target is **formally revised** in `RESULTS.md` (with the revision recorded there). |

## Current status

**Current 30-day compliance: `MISSING`.** There is no continuous compliance series in this repository. Historical short-run percentile brackets were evidence about those benchmark samples, not evidence that a 30-day budget had actually been exhausted. The previous claim of being at the 100% threshold today is withdrawn.

The proposed client SLO is not adjudicated by the Phase 5 bundle. Stored server histogram bucket increases cover overlapping lookback windows and a different latency scope; they cannot establish exact client compliance. No meet-versus-revise decision is asserted here.

## What this policy does not cover

- **Multi-window burn-rate alerting** (e.g. 14.4× tiers for a 99.9% SLO over 30 days). An 18-minute benchmark has no compliance period; those constants do not apply. See [`RESULTS.md`](https://github.com/smadduri9/k8s-hpa-benchmark/blob/main/RESULTS.md#slo-and-error-budget-calibrated-runs). **No alert rules are committed.**
- **Locust failure rate** (availability errors). That is a separate metric from latency-SLO misses; see per-arm request and failure counts in the v1.1 raw Locust CSVs.

## Related documents

- [`RESULTS.md`](https://github.com/smadduri9/k8s-hpa-benchmark/blob/main/RESULTS.md#slo-and-error-budget-calibrated-runs) — current findings and historical scope
- [`POSTMORTEM.md`](https://github.com/smadduri9/k8s-hpa-benchmark/blob/main/POSTMORTEM.md) — unequal `minReplicas` confound and detection
- [`docs/phase5-bucket-schema.md`](https://github.com/smadduri9/k8s-hpa-benchmark/blob/main/docs/phase5-bucket-schema.md) — implemented server bucket schema and aggregation limitations
