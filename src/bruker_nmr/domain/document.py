"""GUI-independent opening snapshot and bounded reversible view/integration state."""
from __future__ import annotations

import math
import re
import uuid
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING

import numpy as np

from bruker_nmr.domain.integrals import (
    AnnotationBundle,
    IntegralGrid,
    IntegralState,
    Normalization,
    Peak,
    add_region,
    assign_reference,
    delete_region,
    edit_region,
    integral_snapshot,
)
from bruker_nmr.domain.parameters import SourceParameters
from bruker_nmr.domain.peaks import PeakState, nearest_extremum
from bruker_nmr.domain.style import DEFAULT_TRACE_COLOUR, DEFAULT_TRACE_PT, TRACE_PX_TO_PT

if TYPE_CHECKING:
    from bruker_nmr.io.reader import ProcessedSpectrum


@dataclass(frozen=True)
class ViewState:
    x_min: float
    x_max: float
    y_min: float
    y_max: float

    def __post_init__(self):
        if (not all(math.isfinite(v) for v in (self.x_min, self.x_max, self.y_min, self.y_max))
                or self.x_max <= self.x_min or self.y_max <= self.y_min):
            raise ValueError("View limits must be finite and increasing")


@dataclass(frozen=True)
class BaselineEdit:
    source: DocumentData = field(repr=False, compare=False)
    fit: object


@dataclass(frozen=True, eq=False)
class DocumentData:
    ppm: np.ndarray = field(repr=False)
    real: np.ndarray = field(repr=False)
    opening_mode: str
    operator: ProcessedSpectrum | None = field(default=None, repr=False)
    raw_available: bool = False
    experiment_number: int | None = None
    processing_number: int | None = None
    raw_source: object = field(default=None, repr=False, compare=False)
    phased_imaginary: np.ndarray | None = field(default=None, repr=False, compare=False)
    phase_steps: tuple = ()
    baseline: BaselineEdit | None = field(default=None, repr=False, compare=False)
    print_header: str = field(default="",repr=False,compare=False)
    source_parameters: SourceParameters = field(default_factory=SourceParameters, repr=False, compare=False)
    integration: IntegralGrid = field(init=False, repr=False, compare=False)

    def __post_init__(self):
        if (self.opening_mode not in {"demo", "operator", "raw"} or self.ppm.ndim != 1
                or self.real.ndim != 1 or len(self.ppm) != len(self.real) or len(self.ppm) < 2
                or self.ppm.flags.writeable or self.real.flags.writeable):
            raise ValueError("Document requires immutable one-dimensional source arrays")
        grid = (self.operator.integration if self.operator is not None and not self.phase_steps and self.baseline is None else
                IntegralGrid(self.real if self.ppm[0] > self.ppm[-1] else self.real[::-1],
                             float(np.max(self.ppm)),
                             float(abs(self.ppm[0] - self.ppm[-1]) / (len(self.ppm) - 1))))
        object.__setattr__(self, "integration", grid)

    @classmethod
    def from_operator(cls, spectrum, choice):
        return cls(spectrum.ppm, spectrum.real, "operator", spectrum,
                   choice.raw_available, choice.experiment_number, choice.processing_number,
                   print_header=spectrum.print_header,
                   source_parameters=SourceParameters.capture(spectrum.provenance.acquisition, spectrum.provenance.processed))

    @classmethod
    def from_demo(cls, spectrum):
        return cls(np.frombuffer(spectrum.ppm.tobytes(), dtype=np.float64),
                   np.frombuffer(spectrum.amplitude.tobytes(), dtype=np.float64), "demo")

    @property
    def can_phase(self):
        return self.operator is not None and self.operator.can_phase

    @property
    def complex_data(self):
        if not self.can_phase:
            raise ValueError('Imaginary data missing; use independent FID processing')
        imaginary = (self.phased_imaginary if self.phased_imaginary is not None
                     else self.operator.imaginary)
        return self.real + 1j * imaginary

    @property
    def phase_source(self):
        return self.baseline.source if self.baseline is not None else self

    def with_baseline(self, fit):
        from bruker_nmr.processing.fid import immutable
        source = self.phase_source
        real = immutable(source.real-fit.values(source.ppm))
        return replace(source, real=real, baseline=BaselineEdit(source, fit))

    def phased(self, phase):
        from bruker_nmr.processing.fid import immutable
        from bruker_nmr.processing.phase import rotate
        source = self.phase_source
        z = rotate(source.complex_data, phase)
        return replace(source, real=immutable(z.real), phased_imaginary=immutable(z.imag),
                       phase_steps=source.phase_steps + (phase,))

    @property
    def annotations(self):
        return self.operator.annotations if self.operator is not None else AnnotationBundle()

    def arrays(self):
        arrays = [self.ppm, self.real, self.phased_imaginary, self.integration.prefix]
        if self.operator is not None:
            arrays += [self.operator.ppm, self.operator.real, self.operator.raw_real,
                       self.operator.raw_imaginary, self.operator.imaginary,
                       self.operator.integration.prefix]
            if self.opening_mode == 'raw':
                arrays.append(self.operator.fid)
        if self.baseline is not None:
            arrays.extend(self.baseline.source.arrays())
        return tuple(a for a in arrays if a is not None)

    @property
    def byte_size(self):
        return _unique_data_bytes((self,))

    def full_view(self):
        low, high = float(np.min(self.real)), float(np.max(self.real))
        span = high - low
        if span == 0:
            span = max(abs(high) * 0.1, 1.0)
        return ViewState(float(np.min(self.ppm)), float(np.max(self.ppm)),
                         low - span * 0.05, high + span * 0.08)


def _unique_data_bytes(data):
    data = {id(d): d for d in data}.values()
    arrays, peaks, fits = {}, {}, {}
    for d in data:
        arrays.update({id(a): a for a in d.arrays()})
        peaks.update({id(p): p for p in d.annotations.peaks})
        if d.baseline is not None:
            fits[id(d.baseline.fit)] = d.baseline.fit
    return (sum(a.nbytes for a in arrays.values()) + 512 * len(peaks)
            + sum(1024 + 512*len(fit.anchors) for fit in fits.values()))


@dataclass(frozen=True)
class DocumentState:
    data: DocumentData = field(repr=False)
    view: ViewState
    integrals: IntegralState = field(default_factory=IntegralState)
    peaks: PeakState = field(default_factory=PeakState)
    calibration: float = 0.
    referenced: bool = False
    line_width: float = DEFAULT_TRACE_PT / TRACE_PX_TO_PT
    trace_color: str = DEFAULT_TRACE_COLOUR
    show_integrals: bool = True
    notice: str = ''

    @property
    def grid(self):
        return self.data.integration if self.calibration == 0 else self.data.integration.shifted(self.calibration)

    def full_view(self, data=None):
        view = (self.data if data is None else data).full_view()
        return replace(view, x_min=view.x_min+self.calibration, x_max=view.x_max+self.calibration)


def _state_bytes(states):
    states = tuple(states)
    integrals = {id(s.integrals): s.integrals for s in states}
    regions = {id(r) for a in integrals.values() for r in a.regions}
    data = {id(s.data): s.data for s in states}
    source_peaks = {id(p) for d in data.values() for p in d.annotations.peaks}
    peak_tuples = {id(s.peaks.records): s.peaks.records for s in states}
    peak_records = {id(p) for records in peak_tuples.values() for p in records}-source_peaks
    return (_unique_data_bytes(s.data for s in states) + 512*len(states) + 1024*len(regions)
            + sum(512+8*len(a.regions) for a in integrals.values()) + 512*len(peak_records)
            + sum(256+8*len(records) for records in peak_tuples.values()))


@dataclass(frozen=True)
class Edit:
    before: DocumentState
    after: DocumentState
    label: str


class Document:
    """100 commands, shared immutable arrays/prefix, 256 MiB retained estimate.

    Opening is pinned. Fifty lightweight views are separate from named edits. Edit
    snapshots share unchanged regions/arrays. Reserve capacity for 20 ordinary edits;
    numerical transforms account explicitly for their additional arrays.
    """
    def __init__(self, data, *, max_commands=100, memory_limit=256 * 1024 * 1024):
        from bruker_nmr.domain.integrals import MAX_REGIONS
        required = data.byte_size + 1024 * (MAX_REGIONS + 20) + 22 * (1024 + 8 * MAX_REGIONS)
        if max_commands < 20 or required > memory_limit:
            raise ValueError("Insufficient history capacity for 20 ordinary commands")
        self.document_id = uuid.uuid4().hex
        self.revision = 0
        self.opening = DocumentState(data, data.full_view(), data.annotations.integrals,
                                     PeakState(data.annotations.peaks))
        markup = getattr(data.operator, 'markup', None)
        if markup is not None:
            calibrated = replace(self.opening, calibration=markup.calibration,
                                 referenced=markup.referenced,
                                 peaks=replace(self.opening.peaks, markers_visible=markup.markers_visible,
                                               label_mode=markup.label_mode))
            self.opening = replace(calibrated, view=calibrated.full_view())
        self.current = self.opening
        self.source_location = data.raw_source
        self.markup_revision = markup.revision if markup is not None else None
        self._saved_markup = self._markup_state()
        self.raw_opening = self.opening if data.opening_mode == 'raw' else None
        self._undo = []
        self._redo = []
        self._views = []
        self.view_revision = 0
        self.max_commands = max_commands
        self.memory_limit = memory_limit

    @property
    def can_undo(self):
        return bool(self._undo)

    @property
    def can_redo(self):
        return bool(self._redo)

    @property
    def retained_bytes(self):
        states = [self.opening, self.current]
        for edit in (*self._undo, *self._redo):
            states.extend((edit.before, edit.after))
        if self.raw_opening is not None:
            states.append(self.raw_opening)
        return _state_bytes(states) + 256*len(self._views)

    @property
    def undo_count(self):
        return len(self._undo)

    @property
    def undo_label(self):
        return self._undo[-1].label if self._undo else ''

    @property
    def redo_label(self):
        return self._redo[-1].label if self._redo else ''

    @property
    def view_count(self):
        return len(self._views)

    def navigate(self, view):
        if not isinstance(view, ViewState):
            raise ValueError('Navigation requires validated view bounds')
        if view == self.current.view:
            return False
        self._views.append(self.current.view)
        self._views = self._views[-50:]
        self.current = replace(self.current, view=view)
        self.view_revision += 1
        return True

    def apply_view(self, view):
        return self.navigate(view)

    def previous_view(self):
        if not self._views:
            return False
        self.current = replace(self.current, view=self._views.pop())
        self.view_revision += 1
        return True

    def _apply(self, state, label="Edit spectrum"):
        if state == self.current:
            return False
        if (not math.isfinite(state.calibration) or abs(state.calibration) > 1e6
                or not math.isfinite(state.line_width) or not .2 <= state.line_width <= 6):
            raise ValueError('Calibration or line width outside supported bounds')
        if not isinstance(state.trace_color, str) or re.fullmatch(r'#[0-9a-f]{6}', state.trace_color) is None:
            raise ValueError('Trace colour must be an opaque RGB hex value (#rrggbb)')
        integral_snapshot(state.integrals, state.data.integration)
        # Reject an oversized mode transition before altering any history.
        required = _state_bytes(
                    (self.opening, state, self.current, self.raw_opening or self.opening))
        if required + 6*1024*1024 > self.memory_limit:
            raise ValueError('Processing result exceeds reversible history memory limit')
        self._undo.append(Edit(self.current, state, label))
        self._redo.clear()
        self._views.clear()
        self.current = state
        while (len(self._undo) > self.max_commands or self.retained_bytes > self.memory_limit):
            self._undo.pop(0)
        self.revision += 1
        return True

    def apply_integrals(self, state, label="Edit integrals"):
        return self._apply(replace(self.current, integrals=state), label)

    def _markup_state(self):
        state = self.current
        return state.integrals, state.peaks, state.calibration, state.referenced

    @property
    def can_save_markup(self):
        state = self.current
        return (self.markup_revision is not None and self.source_location is not None
                and state.data.opening_mode == 'operator'
                and not state.data.phase_steps and state.data.baseline is None)

    def mark_saved(self, path, revision):
        self.source_location = (path, self.source_location[1])
        self.markup_revision = revision
        self._saved_markup = self._markup_state()

    @property
    def integration_modified(self):
        return (self._markup_state() != self._saved_markup
                or self.current.data is not self.opening.data
                or self.current.line_width != self.opening.line_width
                or self.current.trace_color != self.opening.trace_color
                or self.current.show_integrals != self.opening.show_integrals)

    def apply_raw(self, data):
        state = DocumentState(data, data.full_view(), line_width=self.current.line_width,
                              trace_color=self.current.trace_color, show_integrals=self.current.show_integrals)
        if self.current.data.opening_mode == 'raw':
            old, new = self.current.data.integration, data.integration
            regions = []
            for region in self.current.integrals.regions:
                left, right = new.bounds(old.ppm(region.left), old.ppm(region.right))
                regions.append(replace(region, left=left, right=right))
            integrals = replace(self.current.integrals, regions=tuple(regions))
            norm = integrals.normalization
            if norm.kind == 'anchor':
                try:
                    integrals = assign_reference(integrals, new, norm.anchor_id, norm.protons)
                except ValueError:
                    integrals = replace(integrals, normalization=Normalization(
                        'unverified', provenance='recipe changed; assign a valid reference'))
            elif norm.kind != 'absent':
                integrals = replace(integrals, normalization=Normalization(
                    'unverified', provenance='recipe changed; assign a valid reference'))
            peaks = self.current.peaks.review()
            if any(not new.minimum <= p.ppm <= new.offset for p in peaks.records):
                raise ValueError('Existing peaks fall outside the new grid; edit them before reprocessing')
            state = replace(self.current, data=data, integrals=integrals, peaks=peaks,
                            view=self.current.full_view(data) if self.current.view ==
                            self.current.full_view() else self.current.view,
                            notice='FID reprocessed: baseline Off, ranges remapped, areas recomputed; '
                                   'review peaks and reference scale.')
        changed = self._apply(state, "Process FID")
        if self.raw_opening is None:
            self.raw_opening = state
        return changed

    def apply_phase(self, phase):
        if phase.p0 == 0 and phase.p1 == 0 and self.current.data.baseline is None:
            return False
        data = self.current.data.phased(phase)
        integrals = self.current.integrals
        norm = integrals.normalization
        if norm.kind == 'anchor':
            # Reject invalidated reference explicitly; no stale proton values.
            integrals = assign_reference(integrals, data.integration, norm.anchor_id, norm.protons)
        elif norm.kind != 'absent':
            integrals = replace(integrals, normalization=Normalization(
                'unverified', provenance='phase changed; assign a new reference'))
        return self._apply(replace(self.current, data=data, integrals=integrals,
                                   view=self.current.full_view(data) if self.current.view ==
                                   self.current.full_view() else self.current.view,
                                   peaks=self.current.peaks.review(),
                                   notice='Phase changed; review existing peak picks. Refit baseline if needed.'), 'Phase')

    def baseline_integrals(self, data):
        integrals = self.current.integrals
        norm = integrals.normalization
        if norm.kind == 'anchor':
            integrals = assign_reference(integrals, data.integration, norm.anchor_id, norm.protons)
        return integrals

    def apply_baseline(self, fit, *, prepared=None):
        if self.current.data.baseline is not None and self.current.data.baseline.fit == fit:
            return False
        data = prepared if prepared is not None else self.current.data.with_baseline(fit)
        # A prepared preview belongs to the exact pre-baseline immutable source.
        if data.baseline is None or data.baseline.source is not self.current.data.phase_source:
            raise ValueError('Stale baseline preview; select anchors again')
        return self._apply(replace(self.current, data=data, integrals=self.baseline_integrals(data),
                                   peaks=self.current.peaks.review(),
                                   notice='Baseline changed; review existing peak picks.'), 'Baseline')

    def disable_baseline(self):
        source = self.current.data.baseline
        if source is None:
            return False
        data = source.source
        return self._apply(replace(self.current, data=data, integrals=self.baseline_integrals(data),
                                   peaks=self.current.peaks.review(),
                                   notice='Baseline changed; review existing peak picks.'), 'Baseline Off')

    def add_integral(self, a, b):
        return self.apply_integrals(add_region(self.current.integrals, self.current.grid, a, b), "Add integral")

    def edit_integral(self, id, a, b):
        return self.apply_integrals(edit_region(self.current.integrals, self.current.grid, id, a, b), "Edit integral bounds")

    def assign_protons(self, id, protons):
        return self.apply_integrals(assign_reference(self.current.integrals, self.current.data.integration, id, protons), "Assign reference H")

    def delete_integral(self, id):
        return self.apply_integrals(delete_region(self.current.integrals, id), "Delete integral")

    def clear_integrals(self):
        return self.apply_integrals(IntegralState(), "Clear integrals")

    def apply_peaks(self, peaks, label="Peak visibility/review"):
        grid = self.current.data.integration
        for peak in peaks.records:
            tolerance = grid.step if peak.source == 'operator' else 0.
            if not grid.minimum-tolerance <= peak.ppm <= grid.offset+tolerance:
                raise ValueError('Peak position outside the measured grid')
        return self._apply(replace(self.current, peaks=peaks), label)

    def add_peak(self, position, radius):
        from bruker_nmr.domain.peaks import MAX_PEAKS
        if len(self.current.peaks.records) >= MAX_PEAKS:
            raise ValueError('Peak count limit reached')
        data = self.current.data
        index = nearest_extremum(data.ppm, data.real, position-self.current.calibration, radius)
        peak = Peak(uuid.uuid4().hex, float(data.ppm[index]), float(data.real[index]), 0, 'manual')
        return self.apply_peaks(replace(self.current.peaks, records=self.current.peaks.records+(peak,)), "Add peak")

    def add_peaks_in_rectangle(self, x0, y0, x1, y1):
        from bruker_nmr.domain.peaks import MAX_PEAKS, rectangle_maxima

        state = self.current
        if not np.isfinite([x0, y0, x1, y1]).all():
            raise ValueError('Choose a finite peak-picking rectangle')
        low, high = sorted((x0-state.calibration, x1-state.calibration))
        bottom, top = sorted((y0, y1))
        ppm, real = state.data.ppm, state.data.real
        step = ppm[1] - ppm[0]
        occupied = {round((p.ppm-ppm[0])/step) for p in state.peaks.records}
        indices = [i for i in rectangle_maxima(ppm, real, low, high, bottom, top)
                   if i not in occupied]
        if not indices:
            return 0
        if len(state.peaks.records) + len(indices) > MAX_PEAKS:
            raise ValueError('Peak count limit reached. Pick a smaller rectangle.')
        additions = tuple(Peak(uuid.uuid4().hex, float(ppm[i]), float(real[i]), 0, 'manual')
                          for i in indices)
        self.apply_peaks(replace(state.peaks, records=state.peaks.records+additions),
                         'Pick peaks in rectangle')
        return len(additions)

    def move_peak(self, id, position):
        native = position-self.current.calibration
        grid = self.current.data.integration
        if not math.isfinite(native) or not grid.minimum <= native <= grid.offset:
            raise ValueError('Peak position must be within the measured axis')
        peak = self.current.peaks.by_id(id)
        return self.apply_peaks(self.current.peaks.changed(replace(
            peak, ppm=native, source='edited' if peak.source == 'operator' else peak.source,
            needs_review=False)), "Move peak")

    def delete_peak(self, id):
        self.current.peaks.by_id(id)
        return self.apply_peaks(replace(self.current.peaks, records=tuple(
            p for p in self.current.peaks.records if p.id != id)), "Delete peak")

    def clear_peaks(self):
        return self.apply_peaks(replace(self.current.peaks, records=()), "Clear peaks")

    def peak_label(self, id, visible):
        peak = self.current.peaks.by_id(id)
        return self.apply_peaks(self.current.peaks.changed(replace(peak, label_visible=bool(visible))))

    def review_peak(self, id):
        peak = self.current.peaks.by_id(id)
        return self.apply_peaks(self.current.peaks.changed(replace(peak, needs_review=False)))

    def set_zero(self, id):
        peak = self.current.peaks.by_id(id)
        calibration = -peak.ppm
        delta = calibration-self.current.calibration
        view = self.current.view
        return self._apply(replace(self.current, calibration=calibration, referenced=True,
                                   view=replace(view, x_min=view.x_min+delta, x_max=view.x_max+delta),
                                   notice='Zero reference set; areas and source arrays unchanged.'), 'Set zero reference')

    def appearance(self, *, line_width=None, trace_color=None, show_integrals=None, markers=None, label_mode=None):
        peaks = self.current.peaks
        peaks = replace(peaks, markers_visible=peaks.markers_visible if markers is None else markers,
                        label_mode=peaks.label_mode if label_mode is None else label_mode)
        return self._apply(replace(self.current, peaks=peaks,
                    line_width=self.current.line_width if line_width is None else line_width,
                    trace_color=self.current.trace_color if trace_color is None else (trace_color.lower() if isinstance(trace_color, str) else trace_color),
                    show_integrals=self.current.show_integrals if show_integrals is None else show_integrals), "Appearance")

    def undo(self):
        if not self._undo:
            return False
        edit = self._undo.pop()
        self._redo.append(edit)
        self.current = edit.before
        self._views.clear()
        self.view_revision += 1
        self.revision += 1
        return True

    def redo(self):
        if not self._redo:
            return False
        edit = self._redo.pop()
        self._undo.append(edit)
        self.current = edit.after
        self._views.clear()
        self.view_revision += 1
        self.revision += 1
        return True

    def reset(self):
        target = self.raw_opening if self.current.data.opening_mode == 'raw' else self.opening
        return self._apply(replace(target), "Reset to opening")


@dataclass(frozen=True)
class LoadTicket:
    generation: int
    document_id: str
    revision: int


class LoadGate:
    """Reject stale results after cancellation, another open, or document edits."""
    def __init__(self):
        self.generation = 0

    def begin(self, document):
        self.generation += 1
        return LoadTicket(self.generation, document.document_id, document.revision)

    def cancel(self):
        self.generation += 1

    def accepts(self, ticket, document):
        return ticket == LoadTicket(self.generation, document.document_id, document.revision)
