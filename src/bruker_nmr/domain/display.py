"""Extrema-preserving display envelope; numerical source data remain full resolution."""
import math
from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class DisplayTrace:
    x: np.ndarray = field(repr=False)
    y: np.ndarray = field(repr=False)


def display_trace(x, y, x_min, x_max, pixels):
    if not math.isfinite(x_min) or not math.isfinite(x_max) or x_min >= x_max or pixels < 1:
        raise ValueError("Invalid display view")
    if x[0] > x[-1]:
        x, y = x[::-1], y[::-1]
    if x_max < x[0] or x_min > x[-1]:
        return DisplayTrace(np.array([], dtype=float), np.array([], dtype=float))
    first = max(0, int(np.searchsorted(x, x_min)) - 1)
    last = min(len(x), int(np.searchsorted(x, x_max, side="right")) + 1)
    x, y = x[first:last], y[first:last]
    if len(x) <= pixels * 4:
        return DisplayTrace(x, y)
    step = math.ceil(len(x) / pixels)
    groups = len(x) // step
    blocks = y[:groups * step].reshape(groups, step)
    offsets = np.arange(groups) * step
    selected = [offsets + blocks.argmin(axis=1), offsets + blocks.argmax(axis=1),
                np.array([0, len(x) - 1])]
    tail = groups * step
    if tail < len(x):
        selected += [np.array([tail + y[tail:].argmin(), tail + y[tail:].argmax()])]
    indices = np.unique(np.concatenate(selected))
    return DisplayTrace(x[indices], y[indices])
