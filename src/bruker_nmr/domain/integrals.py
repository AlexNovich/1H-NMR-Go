"""Full-grid, signed half-open integration and immutable normalization transitions."""
from __future__ import annotations

import math
import uuid
from dataclasses import dataclass, field, replace

import numpy as np

MAX_REGIONS = 4096


@dataclass(frozen=True)
class Region:
    id: str
    left: int
    right: int
    source: str = "manual"
    imported_bounds: tuple[float, float] | None = None
    bias: float = 0.0
    slope: float = 0.0
    table_value: float | None = None
    table_tolerance: float | None = None

    @property
    def correction_supported(self):
        return self.bias == 0 and self.slope == 0


@dataclass(frozen=True)
class Normalization:
    kind: str = "absent"
    scale: float | None = None
    anchor_id: str | None = None
    protons: float | None = None
    provenance: str = "none"

    def __post_init__(self):
        if self.kind not in {"absent", "imported", "unverified", "anchor", "frozen"}:
            raise ValueError("Unknown normalization state")
        if self.scale is not None and (not math.isfinite(self.scale) or self.scale <= 0):
            raise ValueError("Normalization scale must be finite and positive")
        if (self.kind in {"anchor", "frozen", "imported"}) != (self.scale is not None):
            raise ValueError("Normalization state/scale mismatch")
        if self.kind == "anchor":
            if not self.anchor_id or self.protons is None or not math.isfinite(self.protons) or self.protons <= 0:
                raise ValueError("Reference requires a positive assigned proton count")
        elif self.anchor_id is not None or self.protons is not None:
            raise ValueError("Only an explicit reference owns a proton assignment")


@dataclass(frozen=True)
class IntegralState:
    regions: tuple[Region, ...] = ()
    normalization: Normalization = field(default_factory=Normalization)

    def __post_init__(self):
        if len(self.regions) > MAX_REGIONS or len({r.id for r in self.regions}) != len(self.regions):
            raise ValueError("Integral count or identity limit")
        if self.normalization.kind == "anchor" and not any(
                r.id == self.normalization.anchor_id for r in self.regions):
            raise ValueError("Reference integral is missing")


@dataclass(frozen=True)
class Peak:
    id: str
    ppm: float
    intensity: float
    type: int
    source: str = 'operator'
    label_visible: bool = True
    needs_review: bool = False


@dataclass(frozen=True)
class AnnotationBundle:
    integrals: IntegralState = field(default_factory=IntegralState)
    peaks: tuple[Peak, ...] = ()
    issues: tuple[str, ...] = ()


class IntegralGrid:
    """One immutable prefix sum, shared across all view/edit history snapshots.

    Bounds snap with np.rint (ties to even). Area = step * sum(y[left:right]).
    Reference floor is a conservative floating-point cancellation bound, not an
    estimate of experimental noise or a claim of signal significance.
    """
    def __init__(self, real, offset, step):
        if (len(real) < 2 or not math.isfinite(offset) or not math.isfinite(step) or step <= 0
                or not np.isfinite(real).all()):
            raise ValueError("Invalid integration grid")
        self.offset, self.step, self.size = float(offset), float(step), len(real)
        prefix = np.empty(len(real) + 1, dtype=np.float64)
        prefix[0] = 0
        with np.errstate(over="raise", invalid="raise"):
            np.cumsum(real, dtype=np.float64, out=prefix[1:])
            absolute_sum = float(np.sum(np.abs(real), dtype=np.float64))
        if not np.isfinite(prefix).all() or not math.isfinite(absolute_sum):
            raise ValueError("Integration range exceeds floating-point limits")
        self.prefix = np.frombuffer(prefix.tobytes(), dtype=np.float64)
        self.reference_floor = max(np.finfo(float).tiny,
                                   64 * np.finfo(float).eps * self.step * absolute_sum)

    def shifted(self, delta):
        if not math.isfinite(delta) or not math.isfinite(self.offset+delta):
            raise ValueError('Invalid axis calibration')
        grid = object.__new__(IntegralGrid)
        grid.offset, grid.step, grid.size = self.offset+delta, self.step, self.size
        grid.prefix, grid.reference_floor = self.prefix, self.reference_floor
        return grid

    @property
    def minimum(self):
        return self.ppm(self.size - 1)

    def ppm(self, index):
        return self.offset - index * self.step

    def bounds(self, a, b, *, clip=False, tolerance=0.0):
        if not all(math.isfinite(v) for v in (a, b, tolerance)) or tolerance < 0:
            raise ValueError("Integral boundaries must be finite")
        high, low = max(a, b), min(a, b)
        fuzz = max(self.step * 1e-9, tolerance)
        if not clip and (high > self.offset + fuzz or low < self.minimum - fuzz):
            raise ValueError("Integral boundaries are outside the measured range")
        high = min(self.offset, max(self.minimum, high))
        low = min(self.offset, max(self.minimum, low))
        left, right = (int(np.rint((self.offset - v) / self.step)) for v in (high, low))
        left, right = max(0, left), min(self.size - 1, right)
        if left >= right:
            raise ValueError("Integral must span at least one grid interval")
        return left, right

    def area(self, region):
        if not 0 <= region.left < region.right <= self.size:
            raise ValueError("Invalid integral indices")
        area = self.step * float(self.prefix[region.right] - self.prefix[region.left])
        if not math.isfinite(area):
            raise ValueError("Integral area exceeds floating-point limits")
        return area

    def reference_scale(self, region, protons):
        if not region.correction_supported:
            raise ValueError("Imported bias/slope correction is unsupported; choose another reference")
        area = self.area(region)
        if not math.isfinite(protons) or not 0 < protons <= 1e6:
            raise ValueError("Assigned proton count must be positive and at most 1000000")
        if area <= self.reference_floor:
            raise ValueError("Reference area must be positive and above the numerical cancellation floor")
        scale = protons / area
        if not math.isfinite(scale) or scale <= 0:
            raise ValueError("Reference scale exceeds floating-point limits")
        return scale


def region_by_id(state, id):
    region = next((r for r in state.regions if r.id == id), None)
    if region is None:
        raise ValueError("Select an integral")
    return region


def add_region(state, grid, a, b):
    if len(state.regions) >= MAX_REGIONS:
        raise ValueError("Integral count limit reached")
    left, right = grid.bounds(a, b, clip=True)
    region = Region(uuid.uuid4().hex, left, right)
    norm = state.normalization
    if norm.kind == "absent" and grid.area(region) > grid.reference_floor:
        norm = Normalization("anchor", grid.reference_scale(region, 1.0), region.id, 1.0,
                             "first positive manual integral")
    return IntegralState(state.regions + (region,), norm)


def edit_region(state, grid, id, a, b):
    previous = region_by_id(state, id)
    left, right = grid.bounds(a, b, clip=True)
    region = replace(previous, left=left, right=right)
    norm = state.normalization
    if norm.anchor_id == id:
        norm = replace(norm, scale=grid.reference_scale(region, norm.protons))
    return IntegralState(tuple(region if r.id == id else r for r in state.regions), norm)


def assign_reference(state, grid, id, protons):
    region = region_by_id(state, id)
    norm = Normalization("anchor", grid.reference_scale(region, protons), id, protons,
                         "explicit proton assignment")
    return replace(state, normalization=norm)


def delete_region(state, id):
    region_by_id(state, id)
    norm = state.normalization
    if norm.anchor_id == id:
        norm = Normalization("frozen", norm.scale, provenance="reference deleted; scale retained")
    return IntegralState(tuple(r for r in state.regions if r.id != id), norm)


@dataclass(frozen=True)
class IntegralValue:
    id: str
    high: float
    low: float
    area: float
    value: float | None
    reference: bool
    operator_value: float | None
    correction_supported: bool


def integral_snapshot(state, grid):
    """Renderer/PDF-independent values: never use display points or view clipping."""
    norm = state.normalization
    result = []
    for r in state.regions:
        area = grid.area(r)
        value = area * norm.scale if norm.scale is not None and r.correction_supported else None
        if value is not None and not math.isfinite(value):
            raise ValueError("Normalized integral exceeds floating-point limits")
        result.append(IntegralValue(r.id, grid.ppm(r.left), grid.ppm(r.right), area,
                                    value, norm.anchor_id == r.id, r.table_value,
                                    r.correction_supported))
    return tuple(result)


def has_overlaps(state):
    ordered = sorted(state.regions, key=lambda r: (r.left, r.right))
    right = -1
    for r in ordered:
        if r.left < right:
            return True
        right = max(right, r.right)
    return False
