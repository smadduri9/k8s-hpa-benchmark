# Rate-gap investigation — run-20260904T230444Z (fixed arm)

Assessable serving rows with `latency_p50_ms=MISSING` after burst-onset exclusion (57-row coverage denominator). Cross-reference: `replica_series_fixed.csv` + `kubectl get events -n hpa-eval` for `hpa-eval-fixed-*` pods, window `2026-09-04T23:06:04Z`–`2026-09-04T23:24:04Z`.

| timestamp (UTC) | ready | state | replica_transitions (prior 30s) | pod_restart (prior 30s) | verdict |
|---|---:|---|---|---|---|
| 2026-09-04T23:10:19Z | 2 | DEGRADED | 23:10:12 1→2 | none | EXPLAINED (replica trans) |
| 2026-09-04T23:10:34Z | 2 | DEGRADED | 23:10:12 1→2 | none | EXPLAINED (replica trans) |
| 2026-09-04T23:11:04Z | 1 | DEGRADED | 23:10:43 2→1 | none | EXPLAINED (replica trans) |
| 2026-09-04T23:11:19Z | 2 | DEGRADED | 23:11:14 1→2 | none | EXPLAINED (replica trans) |
| 2026-09-04T23:11:34Z | 1 | DEGRADED | 23:11:14 1→2, 23:11:29 2→1 | none | EXPLAINED (replica trans) |
| 2026-09-04T23:12:04Z | 2 | DEGRADED | 23:12:00 1→2 | none | EXPLAINED (replica trans) |
| 2026-09-04T23:12:19Z | 3 | AVAILABLE | 23:12:00 1→2, 23:12:15 2→3 | none | EXPLAINED (replica trans) |
| 2026-09-04T23:12:49Z | 3 | AVAILABLE | 23:12:31 3→2, 23:12:46 2→3 | 23:12:39 Created+Started skbwz | EXPLAINED (both) |
| 2026-09-04T23:13:04Z | 2 | DEGRADED | 23:12:46 2→3, 23:13:02 3→2 | 23:12:39 Created+Started skbwz | EXPLAINED (both) |
| 2026-09-04T23:13:34Z | 2 | DEGRADED | 23:13:17 2→3, 23:13:33 3→2 | 23:13:10 Created+Started kpwnf | EXPLAINED (both) |
| 2026-09-04T23:14:19Z | 3 | AVAILABLE | 23:14:04 3→2, 23:14:19 2→3 | none | EXPLAINED (replica trans) |
| 2026-09-04T23:14:34Z | 2 | DEGRADED | 23:14:19 2→3 | none | EXPLAINED (replica trans) |
| 2026-09-04T23:14:49Z | 3 | AVAILABLE | 23:14:35 3→2 | none | EXPLAINED (replica trans) |
| 2026-09-04T23:15:04Z | 2 | DEGRADED | 23:14:35 3→2, 23:14:50 2→3 | none | EXPLAINED (replica trans) |
| 2026-09-04T23:15:19Z | 1 | DEGRADED | 23:14:50 2→3, 23:15:06 3→2 | none | EXPLAINED (replica trans) |
| 2026-09-04T23:16:34Z | 1 | DEGRADED | none | none | UNEXPLAINED (≤1 counter sample in rate window) |
| 2026-09-04T23:16:49Z | 1 | DEGRADED | 23:16:39 0→1 | none | EXPLAINED (replica trans) |
| 2026-09-04T23:17:04Z | 1 | DEGRADED | 23:16:39 0→1 | none | EXPLAINED (replica trans) |
| 2026-09-04T23:17:19Z | 1 | DEGRADED | none | none | UNEXPLAINED (≤1 counter sample in rate window) |
| 2026-09-04T23:17:34Z | 1 | DEGRADED | none | none | UNEXPLAINED (≤1 counter sample in rate window) |
| 2026-09-04T23:20:04Z | 1 | DEGRADED | 23:20:00 0→1 | none | EXPLAINED (replica trans) |
| 2026-09-04T23:20:34Z | 1 | DEGRADED | 23:20:15 1→0, 23:20:31 0→1 | 23:20:26 Killing skbwz | EXPLAINED (both) |
| 2026-09-04T23:21:04Z | 1 | DEGRADED | 23:20:46 1→0, 23:21:02 0→1 | none | EXPLAINED (replica trans) |
| 2026-09-04T23:21:19Z | 1 | DEGRADED | 23:21:02 0→1 | none | EXPLAINED (replica trans) |

**Counts:** 21/24 explained by replica transition or pod restart (`Killing`/`Started`/`BackOff`/`Created`) within the prior 30s. 3/24 lack that correlation but have direct Prometheus evidence of ≤1 `app_requests_total` counter sample in the 30s rate window — `rate()` correctly returns empty. Zero rows show a populated gauge with capturable rate data that collection missed.
