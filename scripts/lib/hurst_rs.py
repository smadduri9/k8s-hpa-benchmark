#!/usr/bin/env python3
"""R/S Hurst exponent (numpy only).

OS/arch assumptions: macOS (darwin) or Linux, Python 3.14+ (repo venv).
"""

from __future__ import annotations

import numpy as np

MIN_RS_LENGTH = 64


def hurst_rs(series: np.ndarray) -> float | None:
    """Return Hurst exponent via rescaled-range statistic, or None if too short."""
    x = np.asarray(series, dtype=np.float64)
    if x.size < MIN_RS_LENGTH:
        return None
    if np.all(x == x[0]):
        return None
    max_lag = x.size // 2
    lags = np.unique(np.logspace(np.log10(10), np.log10(max_lag), num=25).astype(int))
    rs_points: list[tuple[float, float]] = []
    for lag in lags:
        if lag < 2:
            continue
        n_chunks = x.size // lag
        if n_chunks < 2:
            continue
        trimmed = x[: n_chunks * lag].reshape(n_chunks, lag)
        mean_adj = trimmed - trimmed.mean(axis=1, keepdims=True)
        cumdev = np.cumsum(mean_adj, axis=1)
        r = cumdev.max(axis=1) - cumdev.min(axis=1)
        s = trimmed.std(axis=1, ddof=1)
        valid = s > 0
        if not np.any(valid):
            continue
        rs = np.mean(r[valid] / s[valid])
        if rs > 0:
            rs_points.append((float(lag), float(rs)))
    if len(rs_points) < 2:
        return None
    log_lag = np.log([p[0] for p in rs_points])
    log_rs = np.log([p[1] for p in rs_points])
    slope, _ = np.polyfit(log_lag, log_rs, 1)
    return float(slope)


def hurst_or_missing(series: np.ndarray) -> str | float:
    value = hurst_rs(series)
    if value is None:
        return "MISSING reason=series_too_short"
    return round(value, 6)
