"""Relative, pivot-aware phase and bounded blind ACME-style proposals.

No operator phase parameters are read. The optimizer assumes positive absorption;
its confidence is a heuristic, never chemical or universal phase validation.
"""
from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize
from scipy.signal import find_peaks, peak_widths

from bruker_nmr.processing.fid import immutable


@dataclass(frozen=True)
class Phase:
    p0: float = 0.
    p1: float = 0.
    pivot: float = .5

    def __post_init__(self):
        if (not all(np.isfinite(v) for v in (self.p0, self.p1, self.pivot))
                or abs(self.p0) > 720 or abs(self.p1) > 1440 or not 0 <= self.pivot <= 1):
            raise ValueError('Phase outside supported bounds')


def rotate(data, phase):
    z = np.asarray(data, dtype=np.complex128)
    if z.ndim != 1 or len(z) < 2 or not np.isfinite(z).all():
        raise ValueError('Phase requires a finite complex one-dimensional spectrum')
    angle = phase.p0 + phase.p1 * (np.arange(len(z)) / len(z) - phase.pivot)
    return immutable(z * np.exp(1j * np.deg2rad(angle)))


@dataclass(frozen=True)
class Proposal:
    phase: Phase
    objective: str
    score: float
    confident: bool
    reason: str
    evaluations: int


class PhaseCancelled(Exception):
    pass


def _adjacent_weights(indices, weights):
    """Only true neighboring full-grid bins contribute a local derivative."""
    return np.sqrt(weights[1:] * weights[:-1]) * (np.diff(indices) == 1)


def auto_phase(data, *, weighted=True, cancelled=None):
    """Bounded deterministic multi-start; at most 8192 objective samples.

    Keep the strongest magnitude points in each of 1024 contiguous blocks, so
    narrow signals survive reduction. Equalize local envelopes in weighted mode;
    gate below six robust noise deviations. Final rotation always uses full SI.
    """
    if cancelled is not None and cancelled():
        raise PhaseCancelled()
    z = np.asarray(data, dtype=np.complex128)
    if z.ndim != 1 or len(z) < 16 or not np.isfinite(z).all():
        raise ValueError('Invalid automatic phase input')
    scale = float(np.max(np.abs(z)))
    if scale <= np.finfo(float).tiny:
        raise ValueError('No signal for automatic phase')
    z = z / scale
    mag = np.abs(z)
    # Contiguous local windows, not a uniformly decimated display trace.
    block = max(8, (len(z) + 1023) // 1024)
    starts = np.arange(0, len(z), block)
    peaks = np.array([a + np.argmax(mag[a:a+block]) for a in starts])
    indices = np.unique(np.clip((peaks[:, None] + np.arange(-4, 4)).ravel(), 0, len(z)-1))
    zz, x = z[indices], indices / len(z) - .5
    noise = float(np.median(np.abs(np.diff(z))) / .954)
    envelope = np.abs(zz)
    weights = np.ones(len(zz))
    if weighted:
        weights = 1 / np.maximum(envelope, max(noise * 6, .01))
        weights[envelope < noise * 6] = 0
    if np.count_nonzero(weights) < 8:
        raise ValueError('Insufficient signal above noise; use manual phase')
    step_weights = _adjacent_weights(indices, weights)
    penalty_scale = max(float(np.sum((weights * envelope)**2)), 1e-30)
    count = 0

    def score(p):
        nonlocal count
        if cancelled is not None and cancelled():
            raise PhaseCancelled()
        count += 1
        y = (zz * np.exp(1j * np.deg2rad(p[0] + p[1] * x))).real
        derivative = np.abs(np.diff(y)) * step_weights
        total = derivative.sum()
        if total <= 1e-30:
            return 1e6
        probabilities = derivative / total
        positive = probabilities > 0
        entropy = -float(np.sum(probabilities[positive] * np.log(probabilities[positive])))
        negative = np.minimum(y, 0) * weights
        penalty = 1000 * float(negative @ negative) / penalty_scale
        return entropy + penalty

    seeds = sorted(((a, b) for a in range(-180, 180, 45)
                    for b in range(-360, 361, 90)), key=score)[:5]
    results = [minimize(score, seed, method='Nelder-Mead', bounds=((-540, 540), (-360, 360)),
                        options={'maxfev': 1000, 'xatol': .002, 'fatol': 1e-8})
               for seed in seeds]
    if cancelled is not None and cancelled():
        raise PhaseCancelled()
    best = min(results, key=lambda r: r.fun)
    phase = Phase((float(best.x[0]) + 180) % 360 - 180, float(best.x[1]), .5)
    corrected = rotate(z, phase).real
    found, _ = find_peaks(mag, prominence=max(10*noise, .01), distance=max(2, len(z)//2048))
    negative_fraction = float(np.sum(np.minimum(corrected, 0)**2) / max(np.sum(corrected**2), 1e-30))
    spread = np.ptp(found / len(z)) if len(found) > 1 else 0
    broad = bool(len(found) and np.max(peak_widths(mag, found)[0]) / len(z) > .008)
    dominant = bool(len(found) > 1 and np.max(mag[found]) > 10 * np.min(mag[found]))
    confident = bool(best.success and len(found) >= 3 and spread > .25
                     and negative_fraction < .02 and noise < .02 and abs(phase.p1) < 355
                     and not broad and not dominant and abs(best.x[0]) < 535
                     and bool(np.all(corrected[found] >= .95 * mag[found])))
    reason = ('Review every signal before Apply; confidence is heuristic.' if confident else
              'Low confidence: broad/dominant/sparse/noisy, negative signals, bounds or ambiguous phase. Use manual controls.')
    return Proposal(phase, 'weighted-acme' if weighted else 'plain-acme',
                    float(best.fun), confident, reason, count)
