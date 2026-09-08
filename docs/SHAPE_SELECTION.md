# Shape selection rule

**Version:** `shape_selection_rule_v2`

**Supersedes:** `shape_selection_rule_v1` (commit `6efd008`).

### What changed in v2 (committed before any v2 scoring)

| Change | v1 | v2 | Why |
|--------|----|----|-----|
| Eligibility threshold | ≥ 1080 requests per 1080 s window (mean ≥ 1 req/s; ~30 counts/plateau) | **`MIN_COUNTS_PER_PLATEAU = 100`** — mean counts per plateau ≥ 100 | At ~30 counts/plateau, Poisson relative noise is ~18% (`1/√30`), same order as shape differences. `constant` archetype is especially biased: flat template cannot distinguish flat signal from counting noise. 1,487 of 1,653 WC98 series already exceed 100k requests/day; no scarcity reason to keep the low threshold. |
| Noise floor | not reported | **`noise_floor_rmse = 1 / sqrt(mean_counts_per_plateau)`** beside each RMSE; flag **`FIT_BELOW_NOISE_FLOOR`** when `RMSE < noise_floor_rmse` | Candidates fitting below the Poisson floor are fitting counting noise, not shape, and **must not win**. |
| Distance rationale | stated without defence | **Distance metric rationale** section added | Reviewers asking “why not DTW?” need a committed answer before scores are interpreted. |

This document is frozen **before** any candidate window is scored under v2. Do not change thresholds, templates, or filters after inspecting candidate rankings. A new rule requires a new version string and a new commit predating any rescore.

## Purpose

Select 18-minute (1080 s) load-shape windows from WorldCup98 and RetailRocket traces by matching unit-mean plateau vectors to canonical archetypes. Absolute Locust user amplitude is **not** part of selection; it is set at runtime via `SHAPE_MEAN_USERS` (default `45`).

## Binning

1. Build 1-second request-count series per eligible series (below).
2. For native archetypes (`flash`, `ramp`, `constant`): take a contiguous **1080 s** window.
3. Aggregate each window to **36 plateaus** of **30 s** (`PLATEAU_SEC=30`, `RUN_TIME_SEC=1080`).
4. For `periodic`: take a contiguous **86400 s** source window, then aggregate to **36 plateaus** of **2400 s** each (`86400 / 36`); playback is 1080 s (dilation factor 80) in the Locust phase — RMSE here uses the 36-point plateau vector from the 86400 s source window.

## Series

| Dataset | Series key | Notes |
|---------|------------|-------|
| WorldCup98 | `server` field (8 bits) | One series per server. **Combined all-server series is not eligible.** |
| RetailRocket | `retailrocket` (site-wide) | After bot filter below. `visitorid` is not a server. |

### WorldCup98 timezone

Window start, calendar day, and periodic diurnal alignment use **France local time (+0200)**. Convert GMT epoch timestamps by **+2 hours** before assigning local calendar days and midnight offsets.

### RetailRocket timezone

Unix epoch milliseconds as published. Provenance records `timezone=UTC` (Kaggle page does not document a local offset).

## RetailRocket bot filter (committed before looking at windows)

Drop a `visitorid` if **either**:

1. `event_count / max(active_span_seconds, 1) > 1.0` events/s (superhuman).
2. Any 1-second bin for that visitor has **> 5** events.

Keep event types `view`, `addtocart`, and `transaction`. Record observed `visitors_total`, `visitors_dropped`, `events_dropped` when the filter runs. **Do not rescore after looking** at which windows rank highest.

## Eligibility filter (before RMSE)

| Rule | Condition |
|------|-----------|
| Window length | Exactly **1080 s** (native archetypes) or **86400 s** (`periodic` source only) |
| Stride | **60 s** between window starts |
| Minimum counts per plateau | **`mean(plateau_counts) >= MIN_COUNTS_PER_PLATEAU`** where **`MIN_COUNTS_PER_PLATEAU = 100`** |
| Timeline | 1 s bins are dense; zero bins are valid zeros, not gaps |
| Empty ITA days | Reject days 1–4 (empty log files on ITA page) |
| `INELIGIBLE_SERVER_ABSENT` | WorldCup98 `(server, local_day)` with **zero requests that calendar day** (+0200) — do not score any window on that pair |
| Peak filter | **No** absolute scaled user peak. **No** `maxReplicas` gate. Report `peak_to_mean = max(plateau) / mean(plateau)` on the raw-count plateau vector (equivalent on unit-mean form). Ratio is reported; **no numeric ratio cap** |

At `PLATEAU_SEC=30` and `RUN_TIME_SEC=1080`, 36 plateaus imply **≥ 3600** requests per native window when the threshold binds uniformly. Eligibility does **not** depend on `SHAPE_MEAN_USERS`.

## Poisson noise floor (reported beside RMSE)

For each candidate window after plateau aggregation:

```
mean_counts_per_plateau = mean(plateau_counts)
noise_floor_rmse = 1 / sqrt(mean_counts_per_plateau)
```

Print `noise_floor_rmse` beside `RMSE`. If **`RMSE < noise_floor_rmse`**, flag **`FIT_BELOW_NOISE_FLOOR`**. Such a candidate is fitting counting noise rather than shape and **must not win** (excluded from top-5 ranking even if RMSE sorts first).

## Canonical templates (length 36, unit mean = 1)

Used only for RMSE. Let `i` be plateau index `0 … 35`.

### `constant`

```
[1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1]
```

### `ramp`

Linear from **0.5** to **1.5** across 36 plateaus:

```
plateau[i] = 0.5 + (1.0 * i / 35)
```

### `flash`

Segment durations **14 / 6 / 16** plateaus (420 s / 180 s / 480 s). Levels `B, 3B, B` with unit mean:

```
[0.75] * 14 + [2.25] * 6 + [0.75] * 16
```

### `periodic`

```
plateau[i] = 1 + 0.5 * sin(2 * pi * i / 36)
```

(range approximately 0.5–1.5; mean 1)

## Distance metric

For candidate plateau vector `x` (36 counts):

```
x_norm = x / mean(x)
RMSE = sqrt(mean((x_norm - template)^2))
```

Compare each eligible window to the archetype template for that scoring pass. **Do not score until this file is committed.**

## Distance metric rationale

Why Euclidean RMSE on unit-mean plateau vectors, not DTW or shape-based distance?

1. **Empirical comparison across 112 UCR datasets** (arXiv:2004.09546): Euclidean, DTW, and shape-based distance perform similarly — winning counts 32 / 31 / 28 for DTW / shape-based / Euclidean, with ARI spread 0.016 between DTW and Euclidean. No large general advantage to DTW.
2. **DTW alignment is largely redundant here.** The 60 s sliding stride already covers time shift; the same paper notes DTW and Euclidean are equivalent at window size 0.
3. **DTW duration warping is wrong for autoscaling.** It treats a 3-minute spike and a 6-minute spike as similar. For HPA, burst duration relative to ~60 s reaction time matters; penalising duration mismatch is desired behaviour.
4. **DTW cost.** The same paper reports 32 days on a 40-core machine for its largest datasets. This repo scores on the order of **240,000** candidate windows.
5. **Shape-based distance z-normalizes** and is scale-invariant, which would erase `peak_to_mean` — the property that determines how hard the autoscaler is driven. Unit-mean normalisation removes absolute scale while preserving relative amplitude, which is what we want.

## Tie-break (lower wins first)

1. Lower RMSE (among candidates not `FIT_BELOW_NOISE_FLOOR`)
2. Earlier window start (France **+0200** for WC98; Unix epoch for RR)
3. Lower `server` id (WC98) or `retailrocket` for RR
4. Smaller local-day index

## Report (per dataset × archetype)

Publish **top 5** rankable candidates (excluding `FIT_BELOW_NOISE_FLOOR`), never only the winner. Each row includes:

- `server_or_series_id`
- local date (+0200 for WC98)
- `window_offset_sec` from local midnight (WC98) or Unix second (RR)
- `RMSE`
- `noise_floor_rmse`
- `FIT_BELOW_NOISE_FLOOR` (true/false)
- `peak_to_mean`
- `N_windows_total`
- `N_windows_eligible`
- count of `INELIGIBLE_SERVER_ABSENT` pairs skipped

If any archetype has **fewer than 5** eligible rankable candidates, **stop and report** rather than scoring a thin pool.

## Hurst reporting (selection phase metadata)

Only **`hurst_native`** is published — the R/S exponent of the source 1-second request-count series for the winning window (numpy R/S; series length must be ≥ 64).

`hurst_native` is reported for the source 1-second series. No post-plateau Hurst value is published. The 36-plateau representation has too few points for an R/S estimate (minimum 64), and a hold-resampled version measures the resampling rather than the traffic. The plateau representation destroys all sub-30s structure by construction, so the shapes we run are not self-similar at fine timescales regardless of the source. This is a property of the representation, not of the traces.

**v2 winning-window values (sanity check):**

| Shape | `hurst_native` | Note |
|-------|----------------|------|
| `wc98_flash` | 0.885 | |
| `wc98_ramp` | 0.955 | |
| `wc98_constant` | 0.570 | Lower than flash/ramp — consistent with flatter, less bursty source (expected direction) |
| `wc98_periodic` | 0.884 | Native series is 86400 s (full local day) |
| `rr_periodic` | 0.780 | |

A previously computed `hurst_scaled` (hold-resampling 36 plateau user counts to 1 s) was **withdrawn**: consecutive identical samples inflate R/S persistence (e.g. `wc98_constant` native 0.570 → scaled 0.863). That measured the hold, not the traffic. Do not publish or replace it with another estimator.

## Runtime amplitude (not a trace property)

Locust shapes store **unit-mean** plateau vectors. Deployment sets:

```
SHAPE_MEAN_USERS = int(os.environ.get("SHAPE_MEAN_USERS", "45"))
users(t) = max(1, round(UNIT_MEAN_PLATEAUS[i] * SHAPE_MEAN_USERS))
```

Default **45** matches the current 3×`e2-standard-2` cluster. Phase 5 sets this from measured capacity. **Absolute amplitude is a deployment parameter, not a trace property.**

## Scoring interpretation (v2 results)

Smooth archetypes match real traffic well; sharp ones are idealisations that real traffic only approximates.

| Archetype | Best RMSE | Noise floor | Interpretation |
|-----------|-----------|-------------|----------------|
| `constant` | 0.025 | 0.017 | Close fit — flat traffic exists in WC98 |
| `ramp` | 0.048 | 0.029 | Close fit — gradual ramps exist |
| `flash` | 0.326 | 0.093 | Above noise floor, but mediocre fit — template is idealised |
| `periodic` (WC98) | 0.229 | 0.008 | Above noise floor, moderate fit — diurnal structure is real but not sinusoidal |

A reader comparing RMSE across archetypes must not treat high flash/periodic RMSE as a bad fit to data; the templates are sharp or sinusoidal idealisations. Trace-derived flash is **approximate**, not a tight reconstruction.

## Committed shapes (five, not six)

| Shape | Dataset | Archetype |
|-------|---------|-----------|
| `wc98_flash` | WorldCup98 | `flash` |
| `wc98_ramp` | WorldCup98 | `ramp` |
| `wc98_constant` | WorldCup98 | `constant` |
| `wc98_periodic` | WorldCup98 | `periodic` |
| `rr_periodic` | RetailRocket | `periodic` |

## RetailRocket scope — `constant` archetype dropped (rule unchanged)

v2 scoring found **zero** eligible RetailRocket `constant` windows (`N_windows_eligible=0` out of 198,702). The v2 threshold was **not** lowered after observing this outcome.

**Why no eligible constant windows**

- RetailRocket: 2,756,101 events over ~4.5 months ≈ **0.24 events/s**.
- A native **1080 s** window holds ≈ **247 events** ≈ **7 counts/plateau** (36 plateaus × 30 s).
- v2 requires **≥ 100 counts/plateau** (≈ 3,600 events/window). No threshold that admits RR constant would leave the shape distinguishable from counting noise.

**Why periodic survives**

- Periodic uses a **86400 s** source window: ≈ **20,400 events** across 36 plateaus of **2400 s** ≈ **567 counts/plateau** (well above 100).

**Why dilating constant is not a fix**

- A 24-hour e-commerce window **is** periodic — the diurnal cycle is its dominant feature. No flat 24-hour stretch exists to find.

**Per-dataset thresholds rejected**

- Would destroy cross-dataset comparability.
- Lowering a frozen rule after observing exclusion is exactly what the freeze prevents.

**Outcome**

RetailRocket serves as the **modern-provenance control on the periodic archetype only**. State this limitation wherever RetailRocket is cited. See `docs/shape_provenance/rr_periodic.json`.

## Archetypes per dataset (selection pass)

| Dataset | Archetypes scored | Committed shape |
|---------|-------------------|-----------------|
| WorldCup98 | `flash`, `ramp`, `constant`, `periodic` | all four |
| RetailRocket | `constant`, `periodic` | **`periodic` only** (`constant` dropped — see above) |
