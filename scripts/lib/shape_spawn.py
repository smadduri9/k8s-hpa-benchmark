#!/usr/bin/env python3
"""Spawn-rate scaling for trace-derived Locust shapes.

OS/arch assumptions: macOS (darwin) or Linux, Python 3.14+ (repo venv).
Keeps steepest plateau-to-plateau spawn duration constant across SHAPE_MEAN_USERS.
"""

from __future__ import annotations

REFERENCE_SHAPE_MEAN_USERS = 45
REFERENCE_SPAWN_FAST = 60
REFERENCE_SPAWN_SLOW = 10
REFERENCE_JUMP_THRESHOLD = 30


def scaled_plateau_users(unit_plateaus: list[float], mean_users: int) -> list[int]:
    return [max(1, round(float(unit) * mean_users)) for unit in unit_plateaus]


def max_plateau_user_jump(scaled: list[int]) -> int:
    return max(abs(scaled[i + 1] - scaled[i]) for i in range(len(scaled) - 1))


def reference_spawn_rate(max_jump_users: int) -> int:
    if max_jump_users >= REFERENCE_JUMP_THRESHOLD:
        return REFERENCE_SPAWN_FAST
    return REFERENCE_SPAWN_SLOW


def spawn_rate_for_mean_users(unit_plateaus: list[float], mean_users: int) -> int:
    """Spawn rate that preserves reference steepest-transition duration."""
    scaled = scaled_plateau_users(unit_plateaus, mean_users)
    max_jump = max_plateau_user_jump(scaled)
    ref_scaled = scaled_plateau_users(unit_plateaus, REFERENCE_SHAPE_MEAN_USERS)
    ref_jump = max_plateau_user_jump(ref_scaled)
    if ref_jump <= 0:
        return REFERENCE_SPAWN_SLOW
    ref_rate = reference_spawn_rate(ref_jump)
    ref_duration = ref_jump / ref_rate
    return max(1, round(max_jump / ref_duration))


def reference_steepest_spawn_sec(unit_plateaus: list[float]) -> float:
    ref_scaled = scaled_plateau_users(unit_plateaus, REFERENCE_SHAPE_MEAN_USERS)
    ref_jump = max_plateau_user_jump(ref_scaled)
    ref_rate = reference_spawn_rate(ref_jump)
    return ref_jump / ref_rate
