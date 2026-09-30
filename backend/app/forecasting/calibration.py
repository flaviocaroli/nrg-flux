"""Shared conformal-calibration math: asymmetric, normalized, rolling CQR.

Why this exists (v6):
  * SYMMETRIC was wrong for prices. One factor widened both edges by the same
    multiple, but upward misses (scarcity spikes) and downward misses
    (negative/near-zero solar hours) have very different magnitudes. We now
    calibrate each edge on its own conformity scores, targeting a miss budget
    of (1-target)/2 per tail — the standard two-sided split-conformal scheme,
    marginally guaranteed to reach >= target coverage.
  * ROLLING beats "15% of all history". Conformal guarantees hold only in so
    far as the calibration slice is exchangeable with tomorrow. A slice that
    stretches back ~100 days across fuel regimes and seasons is neither. The
    slice is now the most recent NRGFLUX_CAL_DAYS (default 45) — and because
    the scheduler retrains daily, the factors follow the market day by day.

Scores are normalized by the model's own band width, so the correction is
multiplicative: uncertain hours stretch more than confident ones. Negative
scale values are legitimate — they *tighten* an edge that was too wide.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

import numpy as np

DEFAULT_CAL_DAYS = 180           # long window: the spike tail needs samples
DEFAULT_HALF_LIFE_DAYS = 90      # ...but the current regime dominates
MIN_CAL_HOURS = 24 * 14          # never calibrate on less than two weeks
MAX_CAL_FRACTION = 0.30          # never starve the fit stage


def exp_weights(n: int, half_life_hours: float | None = None) -> np.ndarray:
    """Exponential recency weights for n chronologically ordered scores.

    The newest observation gets weight 1.0; one half-life earlier gets 0.5,
    and so on. This is the reconciliation of two failure modes we measured:
    (numbers from IT-North backtests, July 2026)
    a short recent window under-covers because it holds too few tail events
    (62.7% at 45d on IT-North), while a long unweighted window under-covers
    because old regimes dilute the current one (66.1% pre-v6). A long,
    recency-weighted window keeps the tail samples AND the current regime.
    """
    if half_life_hours is None:
        half_life_hours = float(os.environ.get(
            "NRGFLUX_CAL_HALFLIFE_DAYS", str(DEFAULT_HALF_LIFE_DAYS))) * 24
    ages = np.arange(n - 1, -1, -1, dtype=float)      # newest -> age 0
    return np.power(0.5, ages / half_life_hours)


def _weighted_quantile(scores: np.ndarray, weights: np.ndarray,
                       level: float) -> float:
    """Conservative weighted quantile (weighted split-conformal style).

    Sort by score, accumulate normalized weights, and take the first score at
    which the cumulative weight reaches ``level``. An effective-sample-size
    finite-sample correction replaces the usual (n+1)/n bump.
    """
    order = np.argsort(scores)
    s, w = scores[order], weights[order]
    n_eff = float(w.sum()) ** 2 / float((w ** 2).sum())
    lvl = min(level * (n_eff + 1.0) / n_eff, 1.0)
    cum = np.cumsum(w) / w.sum()
    idx = int(np.searchsorted(cum, lvl, side="left"))
    return float(s[min(idx, len(s) - 1)])


def rolling_cal_hours(n_available: int) -> int:
    """Size of the rolling calibration slice, bounded by the data we have."""
    cal_days = int(os.environ.get("NRGFLUX_CAL_DAYS", str(DEFAULT_CAL_DAYS)))
    want = cal_days * 24
    return int(min(max(want, MIN_CAL_HOURS), n_available * MAX_CAL_FRACTION))


@dataclass
class AsymmetricScales:
    lo: float                     # multiplier applied downward to p10
    hi: float                     # multiplier applied upward to p90
    raw_coverage_pct: float       # band coverage on the slice BEFORE widening
    n: int                        # calibration hours

    @property
    def symmetric_equivalent(self) -> float:
        """For back-compat reporting alongside the old single factor."""
        return max(self.lo, self.hi)


def asymmetric_cqr(y: np.ndarray, lo: np.ndarray, hi: np.ndarray,
                   target_coverage: float = 0.80,
                   weights: np.ndarray | None = None) -> AsymmetricScales:
    """Per-tail normalized conformal factors from a held-out slice.

    Each tail gets half the miss budget: for an 80% band, each edge may miss
    10% of the time. Scores are recency-weighted by default (see exp_weights),
    assuming ``y`` is in chronological order — pass ``weights`` explicitly to
    override, or equal weights to recover plain split conformal.
    """
    y, lo, hi = np.asarray(y, float), np.asarray(lo, float), np.asarray(hi, float)
    width = np.maximum(hi - lo, 1e-6)
    n = len(y)
    if weights is None:
        weights = exp_weights(n)
    tail = (1.0 - target_coverage) / 2.0
    level = 1.0 - tail

    scores_lo = (lo - y) / width      # >0 when reality fell below the band
    scores_hi = (y - hi) / width      # >0 when reality broke above it
    return AsymmetricScales(
        lo=_weighted_quantile(scores_lo, weights, level),
        hi=_weighted_quantile(scores_hi, weights, level),
        raw_coverage_pct=float(np.mean((y >= lo) & (y <= hi)) * 100),
        n=n,
    )


def apply_scales(p10: np.ndarray, p90: np.ndarray,
                 scale_lo: float, scale_hi: float) -> tuple[np.ndarray, np.ndarray]:
    """Widen (or tighten) each edge by its own factor of the band width."""
    width = np.maximum(np.asarray(p90, float) - np.asarray(p10, float), 1e-6)
    return p10 - scale_lo * width, p90 + scale_hi * width


# ------------------------------------------------- conditional (v7)

def conditional_asymmetric_cqr(y: np.ndarray, lo: np.ndarray, hi: np.ndarray,
                               cond: np.ndarray,
                               target_coverage: float = 0.80,
                               weights: np.ndarray | None = None) -> dict:
    """Two-regime conditional calibration on a conditioning variable.

    A single global factor is a compromise: it under-covers volatile hours
    (spiky evenings) and over-covers calm ones (3am). Splitting the
    calibration scores at the median of a volatility signal and fitting
    per-regime asymmetric factors attacks exactly that pattern, at the cost
    of halving the per-bucket sample — which the long weighted window (180d)
    was sized to afford. Two buckets, not ten: each bucket must still hold
    enough tail events for its 90th-percentile score to mean something.

    Returns {"threshold": float, "calm": AsymmetricScales,
             "volatile": AsymmetricScales}; ``cond`` at inference time is
    compared against the STORED threshold, so train and serve agree even as
    the live volatility distribution drifts.
    """
    y, cond = np.asarray(y, float), np.asarray(cond, float)
    if weights is None:
        weights = exp_weights(len(y))
    thr = float(np.median(cond))
    out = {"threshold": thr}
    for name, mask in (("calm", cond <= thr), ("volatile", cond > thr)):
        if mask.sum() < MIN_CAL_HOURS // 2:      # bucket too thin — fall back
            out[name] = asymmetric_cqr(y, np.asarray(lo)[...],
                                       np.asarray(hi)[...],
                                       target_coverage, weights)
            continue
        out[name] = asymmetric_cqr(y[mask], np.asarray(lo, float)[mask],
                                   np.asarray(hi, float)[mask],
                                   target_coverage, weights[mask])
    return out


def apply_conditional(p10: np.ndarray, p90: np.ndarray, cond: np.ndarray,
                      threshold: float,
                      calm_lo: float, calm_hi: float,
                      vol_lo: float, vol_hi: float) -> tuple[np.ndarray, np.ndarray]:
    """Row-wise application: each hour gets its regime's pair of factors."""
    p10, p90 = np.asarray(p10, float), np.asarray(p90, float)
    cond = np.asarray(cond, float)
    width = np.maximum(p90 - p10, 1e-6)
    volatile = cond > threshold
    s_lo = np.where(volatile, vol_lo, calm_lo)
    s_hi = np.where(volatile, vol_hi, calm_hi)
    return p10 - s_lo * width, p90 + s_hi * width
