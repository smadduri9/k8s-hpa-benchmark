# Shape selection rule

**Version:** `shape_selection_rule_v1`

This document is frozen **before** any candidate window is scored. Do not change thresholds, templates, or filters after inspecting candidate rankings. A new rule requires a new version string and a new commit predating any rescore.

## Purpose

Select 18-minute (1080 s) load-shape windows from WorldCup98 and RetailRocket traces by matching unit-mean plateau vectors to canonical archetypes. Absolute Locust user amplitude is **not** part of selection; it is set at runtime via `SHAPE_MEAN_USERS` (default `45`).

## Binning

1. Build 1-second request-count series per eligible series (below).
2. For native archetypes (`flash`, `ramp`, `constant`): take a contiguous **1080 s** window.
3. Aggregate each window to **36 plateaus** of **30 s** (`PLATEAU_SEC=30`, `RUN_TIME_SEC=1080`).
4. For `periodic`: take a contiguous **86400 s** source window, then dilate to 36 plateaus (playback 1080 s; dilation factor 80) in the extraction phase — not in this scoring step’s RMSE input, which uses the 36 plateau vector after aggregation/dilation as defined in the extraction script.

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
| Minimum requests | **≥ 1080** requests in the window (mean ≥ 1 req/s) |
| Timeline | 1 s bins are dense; zero bins are valid zeros, not gaps |
| Empty ITA days | Reject days 1–4 (empty log files on ITA page) |
| `INELIGIBLE_SERVER_ABSENT` | WorldCup98 `(server, local_day)` with **zero requests that calendar day** (+0200) — do not score any window on that pair |
| Peak filter | **No** absolute scaled user peak. **No** `maxReplicas` gate. Report `peak_to_mean = max(plateau) / mean(plateau)` on the raw-count plateau vector (equivalent on unit-mean form). Ratio is reported; **no numeric ratio cap** in v1 |

Eligibility does **not** depend on `SHAPE_MEAN_USERS`.

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

## Tie-break (lower wins first)

1. Lower RMSE
2. Earlier window start (France **+0200** for WC98; Unix epoch for RR)
3. Lower `server` id (WC98) or `retailrocket` for RR
4. Smaller local-day index

## Report (per dataset × archetype)

Publish **top 5** candidates, never only the winner. Each row includes:

- `server_or_series_id`
- local date (+0200 for WC98)
- `window_offset_sec` from local midnight
- `RMSE`
- `peak_to_mean`
- `N_windows_total`
- `N_windows_eligible`
- count of `INELIGIBLE_SERVER_ABSENT` pairs skipped

## Hurst reporting (selection phase metadata)

Three values per winning window (numpy R/S only; `len < 64` → `MISSING reason=series_too_short`):

| Field | Definition |
|-------|------------|
| `hurst_native` | 1 s count series, unmodified |
| `hurst_plateau` | After 30 s aggregation, **before** `SHAPE_MEAN_USERS` |
| `hurst_scaled` | After `SHAPE_MEAN_USERS` and integer rounding |

Native-to-plateau is the **dominant** burstiness loss (30 s plateaus destroy sub-30 s structure). Do **not** present a single before/after pair implying amplitude thinning caused Hurst change. `hurst_scaled` vs `hurst_plateau` differences are from integer rounding, not thinning.

## Runtime amplitude (not a trace property)

Locust shapes store **unit-mean** plateau vectors. Deployment sets:

```
SHAPE_MEAN_USERS = int(os.environ.get("SHAPE_MEAN_USERS", "45"))
users(t) = max(1, round(UNIT_MEAN_PLATEAUS[i] * SHAPE_MEAN_USERS))
```

Default **45** matches the current 3×`e2-standard-2` cluster. Phase 5 sets this from measured capacity. **Absolute amplitude is a deployment parameter, not a trace property.**

## Archetypes per dataset

| Dataset | Archetypes |
|---------|------------|
| WorldCup98 | `flash`, `ramp`, `constant`, `periodic` |
| RetailRocket | `constant`, `periodic` |
