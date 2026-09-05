#!/usr/bin/env python3
"""Exact two-sided Wilcoxon signed-rank test on paired samples, NumPy only.

No SciPy. This repo runs a small pinned tooling venv and a hand-rolled test is
cheaper than another dependency, but a silent bug here would be maximally
damaging: the whole purpose of the test is to make results credible. It is
therefore validated against a published worked example with a known exact
p-value before use (see ground_truth_check).

The null distribution is enumerated exactly by dynamic programming over
achievable rank sums, not sampled and not normal-approximated. Ranks are doubled
to integers so mid-ranks from ties stay exact.

P_FLOOR is always reported alongside the p-value. With n pairs the smallest
attainable two-sided p is 2/2**n: at n=3 that is 0.25, so no result on three
repetitions can be significant at 0.05 no matter how large the effect. Printing
the floor next to the p-value makes an underpowered n self-evident.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

import numpy as np

# Beyond this the exact DP table is still fine, but a run needing it would mean
# the repetition budget changed radically; fail loudly rather than silently
# switching to an approximation.
MAX_EXACT_N = 50

ZERO_METHOD_WILCOX = "wilcox"  # discard zero differences, reduce n


@dataclass(frozen=True)
class WilcoxonResult:
    n_pairs: int  # pairs supplied
    n_effective: int  # pairs after discarding zero differences
    n_zeros: int
    w_plus: float
    w_minus: float
    statistic: float  # min(w_plus, w_minus)
    p_value: float
    p_floor: float
    ties_present: bool
    method: str

    @property
    def direction(self) -> str:
        """Which side carries the larger signed-rank mass."""
        if self.w_plus > self.w_minus:
            return "positive"
        if self.w_minus > self.w_plus:
            return "negative"
        return "tied"


def p_floor(n_effective: int) -> float:
    """Smallest attainable two-sided p at this n. Undefined (1.0) at n == 0."""
    if n_effective <= 0:
        return 1.0
    return 2.0 / (2.0**n_effective)


def _average_ranks(values: np.ndarray) -> np.ndarray:
    """Ranks of values, ties averaged (mid-ranks), 1-based."""
    order = np.argsort(values, kind="stable")
    ranks = np.empty(values.size, dtype=np.float64)
    ranks[order] = np.arange(1, values.size + 1, dtype=np.float64)
    # Replace each tied group's ranks with the group mean.
    unique_values, inverse = np.unique(values, return_inverse=True)
    for index in range(unique_values.size):
        mask = inverse == index
        if np.count_nonzero(mask) > 1:
            ranks[mask] = ranks[mask].mean()
    return ranks


def _exact_two_sided_p(doubled_ranks: np.ndarray, statistic_doubled: int) -> float:
    """2 * P(W+ <= statistic) under the null, by exact enumeration.

    Each of the n differences is independently positive or negative with
    probability 1/2, so W+ is the sum of a uniformly random subset of the ranks.
    counts[s] is the number of the 2**n subsets whose doubled rank sum is s.
    """
    total = int(doubled_ranks.sum())
    counts = np.zeros(total + 1, dtype=np.float64)
    counts[0] = 1.0
    for rank in doubled_ranks:
        rank = int(rank)
        shifted = np.zeros_like(counts)
        shifted[rank:] = counts[: counts.size - rank]
        counts = counts + shifted

    n = doubled_ranks.size
    cumulative = counts[: statistic_doubled + 1].sum()
    p = 2.0 * cumulative / (2.0**n)
    return float(min(p, 1.0))


def wilcoxon_signed_rank(
    x: np.ndarray | list[float],
    y: np.ndarray | list[float] | None = None,
) -> WilcoxonResult:
    """Exact two-sided signed-rank test. Pass paired samples, or differences in x."""
    x_arr = np.asarray(x, dtype=np.float64)
    if y is None:
        differences = x_arr
    else:
        y_arr = np.asarray(y, dtype=np.float64)
        if x_arr.shape != y_arr.shape:
            raise ValueError(
                f"WILCOXON_SHAPE_MISMATCH x={x_arr.shape} y={y_arr.shape}"
            )
        differences = x_arr - y_arr
    if differences.ndim != 1:
        raise ValueError(f"WILCOXON_NOT_1D shape={differences.shape}")

    n_pairs = differences.size
    nonzero = differences[differences != 0.0]
    n_zeros = n_pairs - nonzero.size
    n_effective = nonzero.size

    if n_effective == 0:
        # Every pair identical: no evidence in either direction, and no test.
        return WilcoxonResult(
            n_pairs=n_pairs,
            n_effective=0,
            n_zeros=n_zeros,
            w_plus=0.0,
            w_minus=0.0,
            statistic=0.0,
            p_value=1.0,
            p_floor=p_floor(0),
            ties_present=False,
            method="degenerate_all_zero",
        )
    if n_effective > MAX_EXACT_N:
        raise ValueError(
            f"WILCOXON_N_TOO_LARGE n={n_effective} max_exact={MAX_EXACT_N}"
        )

    absolute = np.abs(nonzero)
    ranks = _average_ranks(absolute)
    ties_present = bool(np.unique(absolute).size != absolute.size)

    w_plus = float(ranks[nonzero > 0].sum())
    w_minus = float(ranks[nonzero < 0].sum())
    statistic = min(w_plus, w_minus)

    # Doubling makes mid-ranks (x.5) integral so the DP stays exact.
    doubled_ranks = np.rint(ranks * 2.0).astype(np.int64)
    statistic_doubled = int(round(statistic * 2.0))
    p_value = _exact_two_sided_p(doubled_ranks, statistic_doubled)

    return WilcoxonResult(
        n_pairs=n_pairs,
        n_effective=n_effective,
        n_zeros=n_zeros,
        w_plus=w_plus,
        w_minus=w_minus,
        statistic=statistic,
        p_value=p_value,
        p_floor=p_floor(n_effective),
        ties_present=ties_present,
        method="exact",
    )


def format_result_lines(result: WilcoxonResult, label: str) -> list[str]:
    """WILCOXON and P_FLOOR lines. P_FLOOR is never omitted."""
    return [
        f"WILCOXON {label} n={result.n_effective} n_zeros={result.n_zeros} "
        f"w_plus={result.w_plus:g} w_minus={result.w_minus:g} "
        f"statistic={result.statistic:g} p={result.p_value:.6f} "
        f"method={result.method} ties={str(result.ties_present).lower()} "
        f"direction={result.direction}",
        f"P_FLOOR n={result.n_effective} "
        f"min_attainable_two_sided_p={result.p_floor:.6f} "
        f"significant_at_0.05_possible={str(result.p_floor <= 0.05).lower()}",
    ]


# Hollander & Wolfe, Nonparametric Statistical Methods, Hamilton depression scale
# data, as published in the scipy.stats.wilcoxon documentation. Exact two-sided
# p is stated there to full precision: 1352/32768.
GROUND_TRUTH_DIFFERENCES = [
    6, 8, 14, 16, 23, 24, 28, 29, 41, -48, 49, 56, 60, -67, 75,
]
GROUND_TRUTH_STATISTIC = 24.0
GROUND_TRUTH_P = 0.041259765625


def ground_truth_check() -> bool:
    """Validate the implementation against the published example. Prints evidence."""
    result = wilcoxon_signed_rank(GROUND_TRUTH_DIFFERENCES)
    statistic_ok = result.statistic == GROUND_TRUTH_STATISTIC
    # Exact arithmetic: the p-value is a dyadic rational and must match bit for bit.
    p_ok = result.p_value == GROUND_TRUTH_P

    print(
        "WILCOXON_GROUND_TRUTH source=hollander_wolfe_via_scipy_docs "
        f"n={result.n_effective} "
        f"expected_statistic={GROUND_TRUTH_STATISTIC:g} observed_statistic={result.statistic:g} "
        f"expected_p={GROUND_TRUTH_P!r} observed_p={result.p_value!r} "
        f"exact_match={str(statistic_ok and p_ok).lower()}"
    )

    # Independent second opinion: brute-force all 2**15 sign flips rather than the
    # DP used by the implementation, so a DP bug cannot validate itself.
    ranks = _average_ranks(np.abs(np.asarray(GROUND_TRUTH_DIFFERENCES, dtype=np.float64)))
    n = ranks.size
    signs = ((np.arange(2**n)[:, None] >> np.arange(n)) & 1).astype(np.float64)
    sums = signs @ ranks
    brute_p = float(min(2.0 * np.count_nonzero(sums <= result.statistic) / 2.0**n, 1.0))
    brute_ok = brute_p == result.p_value
    print(
        f"WILCOXON_BRUTE_FORCE_CROSSCHECK combinations={2**n} "
        f"brute_p={brute_p!r} dp_p={result.p_value!r} match={str(brute_ok).lower()}"
    )

    floor = p_floor(15)
    print(
        f"P_FLOOR n=15 min_attainable_two_sided_p={floor:.10f} "
        f"observed_p_above_floor={str(result.p_value >= floor).lower()}"
    )

    if statistic_ok and p_ok and brute_ok:
        print("WILCOXON_GROUND_TRUTH_PASS")
        return True
    print("WILCOXON_GROUND_TRUTH_FAIL", flush=True)
    return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ground-truth-check",
        "--self-test",
        action="store_true",
        help="validate against the published worked example and exit",
    )
    args = parser.parse_args()
    if args.ground_truth_check:
        return 0 if ground_truth_check() else 1
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
