"""Explicit constant/linear baselines from operator-selected quiet regions only."""
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Anchor:
    high: float
    low: float

    def __post_init__(self):
        if not np.isfinite([self.high, self.low]).all() or self.high <= self.low:
            raise ValueError('Anchor bounds must be finite, with high ppm above low ppm')


@dataclass(frozen=True)
class Summary:
    ppm: float
    median: float
    mad: float
    points: int


@dataclass(frozen=True)
class BaselineFit:
    mode: str
    intercept: float
    slope: float
    origin: float
    anchors: tuple[Anchor, ...]
    summaries: tuple[Summary, ...]
    excluded_integrals: bool
    warnings: tuple[str, ...] = ()

    def values(self, ppm):
        return self.intercept + self.slope * (np.asarray(ppm)-self.origin)


def fit_baseline(ppm, real, anchors, *, mode='constant', excluded=(), peak_positions=()):
    """Equal-weight robust region medians; linear median pairwise slopes.

    Anchors snap to half-open full-grid bins. Integral exclusions never identify
    quiet data. Peak records and wide ranges provide advisory warnings only, never
    certify quietness or change the numerical fit.
    """
    x, y = np.asarray(ppm), np.asarray(real)
    positions = np.asarray(peak_positions, dtype=float)
    if positions.ndim != 1 or not np.isfinite(positions).all():
        raise ValueError('Baseline peak positions must be finite native ppm coordinates')
    if (mode not in ('constant', 'linear') or x.ndim != 1 or y.shape != x.shape
            or len(x) < 5 or not np.isrealobj(x) or not np.isrealobj(y)
            or not np.isfinite(x).all() or not np.isfinite(y).all()):
        raise ValueError('Baseline requires finite one-dimensional data and a supported mode')
    if len(anchors) > 64 or len(anchors) < (2 if mode == 'linear' else 1):
        raise ValueError('Choose at least two separated anchors for Linear, one for Constant (max 64)')
    n = len(x)
    step = abs(float(x[-1]-x[0]))/(n-1)
    if step <= 0 or not np.allclose(np.diff(x), x[1]-x[0], rtol=1e-8, atol=step*1e-7):
        raise ValueError('Baseline requires a uniform spectral grid')
    high, low = float(max(x[0], x[-1])), float(min(x[0], x[-1]))
    descending = y if x[0] > x[-1] else y[::-1]
    ranges, samples, clipped = [], [], []
    for anchor in anchors:
        a, b = min(high, anchor.high), max(low, anchor.low)
        left, right = (int(np.rint((high-v)/step)) for v in (a, b))
        if left < 0 or right > n-1 or right-left < 5:
            raise ValueError('Each anchor must overlap at least five measured bins')
        if any(left < previous_right and right > previous_left
               for previous_left, previous_right in ranges):
            raise ValueError('Anchor ranges must not overlap after grid snapping')
        indices = np.arange(left, right)
        keep = np.ones(right-left, dtype=bool)
        for region in excluded:
            if region.left < right and region.right > left:
                keep[max(0, region.left-left):min(right-left, region.right-left)] = False
        indices = indices[keep]
        if len(indices) < 5:
            raise ValueError('Anchor has fewer than five bins after integral exclusions')
        values = descending[indices]
        median = float(np.median(values))
        samples.append((indices, values, Summary(float(high-np.median(indices)*step), median,
                        float(np.median(np.abs(values-median))), len(indices))))
        ranges.append((left, right))
        clipped.append(Anchor(high-left*step, high-right*step))
    centers = np.array([s.ppm for _, _, s in samples])
    levels = np.array([s.median for _, _, s in samples])
    origin = float(np.median(centers))
    slope = 0.
    if mode == 'linear':
        separation = np.ptp(centers)
        if separation < max(8*step, (high-low)*1e-3):
            raise ValueError('Linear anchors are too close; select separated quiet regions')
        slopes = [(levels[j]-levels[i])/(centers[j]-centers[i]) for i in range(len(centers))
                  for j in range(i+1, len(centers))
                  if abs(centers[j]-centers[i]) >= max(8*step, (high-low)*1e-3)]
        if not slopes:
            raise ValueError('Linear anchors are degenerate')
        slope = float(np.median(slopes))
    intercept = float(np.median(levels-slope*(centers-origin)))
    if not np.isfinite([intercept, slope, origin]).all():
        raise ValueError('Baseline coefficients exceed floating-point limits')
    warnings = []
    if any(a.high-a.low > .25*(high-low) for a in clipped):
        warnings.append('An anchor spans more than 25% of the measured range; signals may be subtracted.')
    if any(np.any((positions > a.low) & (positions <= a.high)) for a in clipped):
        warnings.append('Anchor bounds contain a recorded peak; verify exclusions and choose quiet ranges.')
    # These diagnostics can detect disagreement/curvature, never certify quietness.
    scale = max(float(np.ptp(y)), np.finfo(float).tiny)
    for indices, values, summary in samples:
        residual = values-(intercept+slope*(high-indices*step-origin))
        adjacent = np.diff(indices) == 1
        differences = np.abs(np.diff(residual))[adjacent]
        noise = float(np.median(differences)) if len(differences) else 0.
        spread = float(np.median(np.abs(residual-np.median(residual))))
        if max(abs(float(np.median(residual))), spread) > max(8*noise, .005*scale):
            warnings.append('Anchors disagree or contain curvature; selected signals may be subtracted.')
            break
    if mode == 'linear' and max(high-max(centers), min(centers)-low) > 5*np.ptp(centers):
        warnings.append('Large extrapolation beyond anchor centers; review the entire spectrum.')
    return BaselineFit(mode, intercept, slope, origin, tuple(clipped),
                       tuple(s for _, _, s in samples), bool(excluded), tuple(warnings))
