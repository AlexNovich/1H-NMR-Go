"""Strict 1D proton operator import. Numerical data never pass through the GUI."""
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

import numpy as np
from nmrglue.fileio import bruker

from bruker_nmr.domain.integrals import AnnotationBundle, IntegralGrid
from bruker_nmr.domain.parameters import freeze
from bruker_nmr.io.annotations import read_annotations
from bruker_nmr.io.errors import InputError
from bruker_nmr.io.markup import EXTRA, INPUTS, Markup, fingerprint, read_markup
from bruker_nmr.io.print_header import read_header
from bruker_nmr.io.status import number, read_status

ANNOTATIONS = ("intrng", "integrals.txt", "peaklist.xml", "peakrng", "peaks")


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    experiment_number: int | None
    processing_number: int | None
    nucleus: str
    dimension: int | None
    processed_available: bool
    raw_available: bool
    issues: tuple[str, ...]
    annotation_names: tuple[str, ...]
    experiment: Path = field(repr=False)
    processing: Path | None = field(repr=False)
    raw_points: int | None = None


@dataclass(frozen=True)
class Provenance:
    source: Path = field(repr=False)
    experiment: str = field(repr=False)
    processing: str = field(repr=False)
    acquisition: Mapping = field(repr=False)
    processed: Mapping = field(repr=False)
    scaling: str = "raw * 2**NC_proc, once; no phase or baseline applied"


@dataclass(frozen=True)
class ProcessedSpectrum:
    raw_real: np.ndarray = field(repr=False)
    real: np.ndarray = field(repr=False)
    raw_imaginary: np.ndarray | None = field(repr=False)
    imaginary: np.ndarray | None = field(repr=False)
    ppm: np.ndarray = field(repr=False)
    provenance: Provenance
    annotation_names: tuple[str, ...]
    integration: IntegralGrid = field(repr=False)
    annotations: AnnotationBundle = field(repr=False)

    print_header: str = field(default='',repr=False,compare=False)
    markup: Markup | None = field(default=None, repr=False, compare=False)

    @property
    def can_phase(self):
        return self.imaginary is not None

    @property
    def complex_data(self):
        if not self.can_phase:
            raise InputError("imaginary_unavailable_use_fid")
        return _immutable(self.real + 1j * self.imaginary)


def _immutable(array):
    return np.frombuffer(array.tobytes(), dtype=array.dtype).reshape(array.shape)


def _choice(params, key, choices):
    value = number(params, key, integer=True)
    if value not in choices:
        raise InputError("unsupported_" + key.lower())
    return value


def _acquisition(experiment: Path, files: set[Path]):
    a = read_status(experiment / "acqus")
    dimension = number(a, "PARMODE", integer=True)
    if dimension != 0 or any(experiment / n in files for n in ("ser", "acqu2s")):
        raise InputError("unsupported_dimension")
    nucleus = a.get("NUC1")
    if nucleus != "1H":
        raise InputError("unsupported_nucleus")
    number(a, "SFO1", positive=True)
    number(a, "SW_h", positive=True)
    return a


def _binary_layout(path, points, dtype_code, byte_order, max_points):
    if not 2 <= points <= max_points:
        raise InputError("point_count_limit")
    if dtype_code not in {0, 2} or byte_order not in {0, 1}:
        raise InputError("unsupported_binary_format")
    if path.is_symlink():
        raise InputError("source_symlink")
    if not path.is_file():
        raise InputError("missing_binary")
    size = 4 if dtype_code == 0 else 8
    expected = points * size
    actual = path.stat().st_size
    padded = ((expected + 1023) // 1024) * 1024
    if actual not in {expected, padded}:
        raise InputError("binary_size_mismatch")
    if actual > expected:
        with path.open("rb") as stream:
            stream.seek(expected)
            if any(stream.read()):
                raise InputError("nonzero_binary_padding")
    return actual // size


def _raw(experiment, a, limits):
    from bruker_nmr.io.fid import validate
    validate(experiment, a, limits)
    _choice(a, "AQ_mod", {3})
    td = number(a, "TD", integer=True, positive=True)
    if td % 2:
        raise InputError("unpaired_fid_values")
    dtype = _choice(a, "DTYPA", {0, 2})
    order = _choice(a, "BYTORDA", {0, 1})
    delay = number(a, "GRPDLY")
    if delay < 0:
        raise InputError("unverified_digital_filter_delay")
    stored = _binary_layout(experiment / "fid", td, dtype, order, limits.max_points)
    if dtype == 2:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            _, values = bruker.read_binary(str(experiment / "fid"), shape=(stored,),
                                            cplex=False, big=order == 1, isfloat=True)
        if not np.isfinite(values[:td]).all():
            raise InputError("nonfinite_fid_values")


def _processed(processing, limits, files):
    p = read_status(processing / "procs")
    _choice(p, "PPARMOD", {0})
    if any(processing / n in files for n in ("2rr", "proc2s")):
        raise InputError("unsupported_dimension")
    _choice(p, "FT_mod", {6})
    size = number(p, "SI", integer=True, positive=True)
    dtype = _choice(p, "DTYPP", {0, 2})
    order = _choice(p, "BYTORDP", {0, 1})
    number(p, "SF", positive=True)
    number(p, "SW_p", positive=True)
    number(p, "OFFSET")
    nc = number(p, "NC_proc", integer=True)
    if not -128 <= nc <= 128:
        raise InputError("processed_scale_limit")
    if number(p, "STSR", integer=True) != 0 or number(p, "STSI", integer=True) not in {0, size}:
        raise InputError("unsupported_processed_window")
    xdim = number(p, "XDIM", integer=True)
    if xdim < 0 or xdim > size or (xdim and size % xdim):
        raise InputError("unsupported_processed_layout")
    _binary_layout(processing / "1r", size, dtype, order, limits.max_points)
    if processing / "1i" in files:
        _binary_layout(processing / "1i", size, dtype, order, limits.max_points)
    return p


def discover(source):
    files = set(source.files)
    experiments = set()
    for path in files:
        if path.name in {"acqus", "acqu", "fid", "ser", "acqu2s"}:
            experiments.add(path.parent)
        if path.name in {"procs", "proc", "1r", "1i"} and path.parent.parent.name == "pdata":
            experiments.add(path.parent.parent.parent)
    records = []
    for experiment in sorted(experiments):
        issues = []
        nucleus = "unverified"
        dimension = None
        a = None
        try:
            metadata = read_status(experiment / "acqus")
            nucleus = metadata.get("NUC1") if metadata.get("NUC1") in {"1H", "13C"} else "unsupported"
            dimension = number(metadata, "PARMODE", integer=True) + 1
            a = _acquisition(experiment, files)
        except InputError as error:
            issues.append(error.code)
        raw = False
        if a is not None:
            try:
                _raw(experiment, a, source.limits)
                raw = True
            except InputError as error:
                issues.append("raw:" + error.code)
        procs = sorted({p.parent for p in files
                        if p.parent.parent == experiment / "pdata"}) or [None]
        for processing in procs:
            errors = list(issues)
            valid = False
            if a is not None and processing is not None:
                try:
                    _processed(processing, source.limits, files)
                    valid = True
                except InputError as error:
                    errors.append("processed:" + error.code)
            elif processing is None:
                errors.append("processed:missing_procs")
            records.append(Candidate(
                str(len(records)), int(experiment.name) if experiment.name.isdecimal() else None,
                int(processing.name) if processing and processing.name.isdecimal() else None,
                nucleus, dimension, valid, raw, tuple(errors),
                tuple(n for n in ANNOTATIONS if processing and processing / n in files),
                experiment, processing, int(a["TD"]) // 2 if raw else None))
            if len(records) > source.limits.max_candidates:
                raise InputError("candidate_count_limit")
    if not records:
        raise InputError("no_experiments")
    return tuple(records)


def _decode(path, p, limits):
    points = number(p, "SI", integer=True)
    dtype = _choice(p, "DTYPP", {0, 2})
    order = _choice(p, "BYTORDP", {0, 1})
    _binary_layout(path, points, dtype, order, limits.max_points)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        _, values = bruker.read_pdata_binary(str(path), shape=None, submatrix_shape=None,
                                            big=order == 1, isfloat=dtype == 2)
    raw = _immutable(values[:points])
    with np.errstate(over="raise", invalid="raise", under="raise"):
        real = np.ldexp(raw.astype(np.float64), number(p, "NC_proc", integer=True))
    if not np.isfinite(real).all():
        raise InputError("nonfinite_processed_values")
    return raw, _immutable(real)


def read_processed(source, candidate):
    try:
        files = set(source.files)
        a = _acquisition(candidate.experiment, files)
        if candidate.processing is None:
            raise InputError("missing_procs")
        parts = fingerprint(candidate.processing, (*INPUTS, EXTRA))
        p = _processed(candidate.processing, source.limits, files)
        raw, real = _decode(candidate.processing / "1r", p, source.limits)
        imaginary = raw_imaginary = None
        if candidate.processing / "1i" in files:
            raw_imaginary, imaginary = _decode(candidate.processing / "1i", p, source.limits)
        size = number(p, "SI", integer=True)
        ppm = number(p, "OFFSET") - np.arange(size) * (number(p, "SW_p") / number(p, "SF") / size)
        if not np.isfinite(ppm).all() or not np.all(np.diff(ppm) < 0):
            raise InputError("invalid_ppm_grid")
        acquisition = MappingProxyType({k: freeze(v) for k, v in a.items()})
        processed = MappingProxyType({k: freeze(v) for k, v in p.items()})
        provenance = Provenance(source.original,
                                candidate.experiment.relative_to(source.root).as_posix(),
                                candidate.processing.relative_to(source.root).as_posix(),
                                acquisition, processed)
        grid = IntegralGrid(real, number(p, "OFFSET"), number(p, "SW_p") / number(p, "SF") / size)
        annotations = read_annotations(candidate.processing, files, grid, p)
        annotations, markup = read_markup(candidate.processing, annotations, grid, parts)
        if fingerprint(candidate.processing, (*INPUTS, EXTRA)) != parts:
            raise InputError("source_changed_reopen")
        return ProcessedSpectrum(raw, real, raw_imaginary, imaginary, _immutable(ppm),
                                 provenance, candidate.annotation_names, grid, annotations,
                                 read_header(source,candidate,a), markup)
    except InputError:
        raise
    except (OSError, ValueError, Warning, FloatingPointError):
        raise InputError("processed_read_failed") from None
