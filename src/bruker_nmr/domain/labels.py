"""Small renderer-independent label placement and formatting for screen/future PDF."""
import math
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Label:
    id: str
    center: float
    width: float
    text: str
    priority: float = 0.
    coordinate_order: float = 0.


@dataclass(frozen=True)
class Placement:
    label: Label
    left: float
    lane: int


def place_labels(labels, left, right, *, lanes=2, auto=True, gap=5):
    occupied = [[] for _ in range(lanes)]
    result, hidden = [], []
    for label in sorted(labels, key=lambda x: (-x.priority, x.center, x.id)):
        if label.width > right-left:
            if auto:
                hidden.append(label.id)
                continue
        start = max(left, min(right-label.width, label.center-label.width/2))
        end = start+label.width
        lane = next((i for i, row in enumerate(occupied)
                     if all(end+gap <= a or start >= b+gap for a, b in row)), None)
        if lane is None:
            if auto:
                hidden.append(label.id)
                continue
            lane = len(result) % lanes
        occupied[lane].append((start, end))
        result.append(Placement(label, start, lane))
    return tuple(result), tuple(hidden)


def format_ppm(value):
    return f'{0. if abs(value) < .0005 else value:.3f}'


def format_integral(value, reference=False):
    return ('?' if value is None else f'{value:.2f}') + (' H (ref)' if reference else ' H')


def format_peak_label(peak, calibration=0.):
    return format_ppm(peak.ppm+calibration) + (' ?' if peak.needs_review else '')


def format_clipped_integral(row, view):
    value = "?" if row.value is None else f"{0.0 if abs(row.value) < 0.005 else row.value:.2f}"
    value = value.replace("-", "−") + ("*" if row.reference else "")
    return ("‹" if row.high > view.x_max else "") + value + ("›" if row.low < view.x_min else "")


def visible_peaks(state, view, preview=None):
    """Shared selection policy; a screen-only drag preview never alters source records."""
    for peak in state.peaks.records:
        if preview is not None and preview[0] == peak.id:
            peak = replace(peak,ppm=preview[1]-state.calibration)
        if peak.label_visible and view.x_min <= peak.ppm+state.calibration <= view.x_max:
            yield peak


def label_priority(peak, selected=None):
    return float('inf') if peak.id == selected else abs(peak.intensity)


def spread(centers, spacing, left, right):
    """Least-squares ordered centers with equal minimum spacing, in any length unit.

    Pool adjacent violators after removing i*spacing; project the fitted blocks onto
    the common feasible interval. No renderer or scientific coordinates are altered.
    """
    centers = tuple(centers)
    if (not all(math.isfinite(v) for v in (*centers, spacing, left, right))
            or spacing <= 0 or left > right
            or any(a > b for a, b in zip(centers, centers[1:]))):
        raise ValueError('Invalid ordered label geometry')
    if not centers:
        return ()
    hi = right-(len(centers)-1)*spacing
    if hi < left-1e-9:
        raise ValueError('Labels do not fit at the requested spacing')
    hi = max(left, hi)
    blocks = []
    for i, center in enumerate(centers):
        blocks.append([center-i*spacing, 1])
        while len(blocks) > 1 and blocks[-2][0]/blocks[-2][1] > blocks[-1][0]/blocks[-1][1]:
            total, count = blocks.pop()
            blocks[-1][0] += total
            blocks[-1][1] += count
    fitted = [min(max(total/count, left), hi) for total, count in blocks for _ in range(count)]
    return tuple(v+i*spacing for i, v in enumerate(fitted))


def place_peak_labels(labels, left, right, *, spacing, auto=True):
    """Bounded O(n log n) priority selection, then ordered minimum-displacement fan-out.

    Rotated labels have the same transverse font height. All mode retains all labels,
    explicitly allowing overlap if the requested spacing cannot fit.
    """
    labels = tuple(labels)
    if not labels:
        return (), ()
    width = max(label.width for label in labels)
    if (not all(math.isfinite(v) for v in (left,right,spacing))
            or any(not math.isfinite(p.center) or not math.isfinite(p.width)
                   or p.width <= 0 or math.isnan(p.priority) for p in labels)
            or len({p.id for p in labels}) != len(labels)
            or spacing < width or right < left):
        raise ValueError('Invalid rotated label width or spacing')
    capacity = max(0, int(math.floor((right-left-width)/spacing))+1)
    ranked = sorted(labels, key=lambda p: (-p.priority, p.center, p.coordinate_order, p.id))
    selected = ranked[:capacity] if auto else ranked
    hidden = tuple(p.id for p in ranked[len(selected):])
    selected.sort(key=lambda p: (p.center, p.coordinate_order, p.id))
    if not selected:
        return (), hidden
    a, b = left+width/2, right-width/2
    if a > b:  # All mode: retain over-wide labels with explicit overlap/clipping.
        a = b = (left+right)/2
    distance = min(spacing, (b-a)/(len(selected)-1)) if len(selected) > 1 else spacing
    centers = (spread((p.center for p in selected), distance, a, b) if distance > 0
               else (a,)*len(selected))
    return tuple(Placement(p, c-p.width/2, 0) for p, c in zip(selected, centers)), hidden


def orthogonal_leaders(placements):
    """Ordered elbow levels: overlapping rightward routes rise, leftward routes fall.

    The levels change only the leader corridor, never bracket or label baselines.
    """
    pairs = [(p.label.center, p.left + p.label.width / 2) for p in placements]
    levels = [0] * len(pairs)
    for direction, indices in ((1, range(len(pairs))), (-1, range(len(pairs) - 1, -1, -1))):
        done = []
        for i in indices:
            start, end = pairs[i]
            if (end - start) * direction <= 1e-7:
                continue
            blockers = [
                j
                for j in done
                if max(min(pairs[j]), min(start, end)) <= min(max(pairs[j]), max(start, end)) + 1e-7
            ]
            levels[i] = max((levels[j] + 1 for j in blockers), default=0)
            done.append(i)
    return pairs, levels
