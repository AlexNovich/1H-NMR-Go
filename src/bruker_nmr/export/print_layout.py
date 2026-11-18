"""Physical print presentation; numerical integration is never recomputed here."""

import math

import numpy as np
from matplotlib.ticker import AutoMinorLocator, MaxNLocator, MultipleLocator

from bruker_nmr.domain.labels import format_clipped_integral as integral_text
from bruker_nmr.domain.labels import orthogonal_leaders as orthogonal_leaders
from bruker_nmr.domain.labels import place_peak_labels

__all__ = ["integral_text", "orthogonal_leaders"]


class PrintLayoutError(ValueError):
    pass


def place_integrals(labels, left, right, bands=1):
    if bands != 1:
        raise PrintLayoutError("Use one aligned integral row; zoom to fewer ranges if necessary.")
    placed, hidden = place_peak_labels(labels, left, right, spacing=16, auto=True)
    if hidden:
        raise PrintLayoutError(
            "Integral labels do not fit in one readable row. Close preview and zoom to fewer "
            "ranges. No values were hidden or exported."
        )
    return placed


def intersect(a, b, c, d, tolerance=1e-7):
    """Axis-aligned closed-segment intersection, including touches/overlaps."""
    return (
        max(min(a[0], b[0]), min(c[0], d[0])) <= min(max(a[0], b[0]), max(c[0], d[0])) + tolerance
        and max(min(a[1], b[1]), min(c[1], d[1]))
        <= min(max(a[1], b[1]), max(c[1], d[1])) + tolerance
    )


def validate_routes(routes):
    for i, route in enumerate(routes):
        for a, b in zip(route, route[1:]):
            if a[0] != b[0] and a[1] != b[1]:
                raise PrintLayoutError("Integral leader is not orthogonal.")
            for other in routes[:i]:
                for c, d in zip(other, other[1:]):
                    if intersect(a, b, c, d):
                        raise PrintLayoutError(
                            "Integral leaders cannot be separated at this view. "
                            "Adjust overlapping ranges or zoom before export."
                        )


def clipped_positions(ppm, real, view, calibration=0, max_marks=128):
    x = ppm + calibration
    if x[0] > x[-1]:
        x, y = x[::-1], real[::-1]
    else:
        y = real
    low, high = max(view.x_min, float(x[0])), min(view.x_max, float(x[-1]))
    if high < low:
        return ()
    start, end = np.searchsorted(x, [low, high])
    xx = np.concatenate(([low], x[start:end], [high]))
    yy = np.concatenate(([np.interp(low, x, y)], y[start:end], [np.interp(high, x, y)]))
    indices = np.flatnonzero(yy > view.y_max)
    if not len(indices):
        return ()
    breaks = np.diff(indices) > 1
    starts = indices[np.concatenate(([True], breaks))]
    ends = indices[np.concatenate((breaks, [True]))]
    chosen = np.arange(len(starts))
    if len(starts) > max_marks:
        bins = np.clip(
            ((xx[starts] - low) / max(high - low, np.finfo(float).tiny) * max_marks).astype(int),
            0,
            max_marks - 1,
        )
        _, chosen = np.unique(bins, return_index=True)
    return tuple(
        float(xx[start + np.argmax(yy[start : end + 1])])
        for start, end in zip(starts[chosen], ends[chosen])
    )


def configure_ticks(ax, view):
    span = view.x_max - view.x_min
    major = (
        MultipleLocator(1)
        if 6 <= span <= 18
        else MaxNLocator(nbins=8, steps=[1, 2, 2.5, 5, 10], min_n_ticks=3)
    )
    ax.xaxis.set_major_locator(major)
    ax.xaxis.set_minor_locator(AutoMinorLocator(5))
    values = major.tick_values(view.x_min, view.x_max)
    step = float(np.min(np.diff(values)))
    decimals = 12
    for digits in range(13):
        scaled = step * 10**digits
        if abs(scaled - round(scaled)) < 1e-7:
            decimals = digits
            break

    def number(value, _position):
        if abs(value) < step * 1e-8:
            value = 0.0
        if step < 1e-6 or abs(value) >= 1e6:
            digits = min(16, max(5, math.ceil(math.log10(max(abs(value), step) / step)) + 1))
            return f"{value:.{digits}g}".replace("-", "−")
        return f"{value:.{decimals}f}".replace("-", "−")

    from matplotlib.ticker import FuncFormatter

    ax.xaxis.set_major_formatter(FuncFormatter(number))
