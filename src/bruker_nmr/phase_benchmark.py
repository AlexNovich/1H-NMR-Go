"""Independent Lorentzian fixtures and reproducible synthetic-only phase timing."""
import importlib.metadata
import json
import platform
import time
from pathlib import Path

import numpy as np

from bruker_nmr.processing.phase import auto_phase, rotate


def analytic(n=8192, *, kind='narrow'):
    x = np.arange(n) / n
    centers = [.2, .48, .8]
    widths = [.0006, .001, .0008]
    amplitudes = [1, .7, .9]
    if kind == 'broad':
        widths = [.008, .01, .006]
    if kind == 'solvent':
        amplitudes[1] = 25
    if kind == 'mixed':
        amplitudes[1] *= -1
    if kind == 'sparse':
        centers, widths, amplitudes = centers[:1], widths[:1], amplitudes[:1]
    z = sum(a*w/(w+1j*(x-c)) for c, w, a in zip(centers, widths, amplitudes))
    return x, z, tuple(zip(centers, widths))


def noisy_observation(clean):
    rng = np.random.default_rng(771)
    return clean + .08 * (rng.normal(size=len(clean)) + 1j*rng.normal(size=len(clean)))


def regional_metrics(x, truth, corrected, windows):
    result = []
    for center, width in windows:
        mask = abs(x-center) < 8*width
        t, c = truth[mask].real, corrected[mask].real
        dispersion = truth[mask].imag
        residual = c-t
        result.append({'relative_area_error': float(abs(sum(residual)/sum(t))),
                       'dispersion_over_absorption': float(abs(residual @ dispersion) /
                            max(dispersion @ dispersion, 1e-30))})
    return result


def benchmark(destination, *, runs=10, sizes=(8192, 65536, 262144, 1048576, 4194304)):
    if runs < 10:
        raise ValueError('At least ten timed warm runs required')
    timing = []
    for n in sizes:
        x, truth, windows = analytic(n)
        distorted = truth * np.exp(1j*np.deg2rad(47-83*(x-.5)))
        elapsed = []
        for _ in range(runs+2):
            started = time.perf_counter()
            proposal = auto_phase(distorted)
            elapsed.append(time.perf_counter()-started)
        timing.append({'si': n, 'first_call_seconds': elapsed[0],
                       'warmup_seconds': elapsed[1], 'runs': runs,
                       'warm_seconds': elapsed[2:], 'median_seconds': float(np.median(elapsed[2:])),
                       'p95_seconds': float(np.percentile(elapsed[2:], 95)),
                       'confident': proposal.confident,
                       'regions': regional_metrics(x, truth, rotate(distorted, proposal.phase), windows)})
    quality = []
    for kind in ('narrow', 'broad', 'solvent', 'noise', 'sparse', 'mixed'):
        x, truth, windows = analytic(kind=kind)
        observed = noisy_observation(truth) if kind == 'noise' else truth
        distorted = observed * np.exp(1j*np.deg2rad(47-83*(x-.5)))
        for weighted in (False, True):
            proposal = auto_phase(distorted, weighted=weighted)
            corrected = rotate(distorted, proposal.phase)
            occupied = np.logical_or.reduce([abs(x-c)<8*w for c, w in windows])
            signal_angle = np.angle(corrected[occupied]/truth[occupied], deg=True)
            residual_phase = (47+proposal.phase.p0 + (proposal.phase.p1-83)*(x[occupied]-.5))
            error = np.angle(np.exp(1j*np.deg2rad(residual_phase)), deg=True)
            quality.append({'case': kind, 'objective': proposal.objective,
                            'confident': proposal.confident, 'p0': proposal.phase.p0,
                            'p1': proposal.phase.p1, 'evaluations': proposal.evaluations,
                            'max_occupied_phase_error_degrees': float(max(abs(error))),
                            'max_occupied_signal_angle_deviation_degrees': float(max(abs(signal_angle))),
                            'regions': regional_metrics(x, truth, corrected, windows)})
    report = {'machine': platform.machine(), 'platform': platform.system(),
              'python': platform.python_version(),
              'versions': {p: importlib.metadata.version(p) for p in ('numpy', 'scipy', 'nmrglue')},
              'scope': 'Independent artificial Lorentzian data only; first-call is per-size, '
                       'not a cold OS cache. Ten warm runs after one warmup per SI. '
                       'CLI process starts before module imports; process duration recorded separately.',
              'timing': timing, 'quality': quality}
    Path(destination).write_text(json.dumps(report, indent=2), encoding='utf-8')
    return report
