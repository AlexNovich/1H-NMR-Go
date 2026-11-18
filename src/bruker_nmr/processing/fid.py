"""Explicit bounded FID recipe. No stored phase, baseline or operator scale by default."""

from dataclasses import dataclass, field
from types import MappingProxyType

import numpy as np

from bruker_nmr.domain.integrals import AnnotationBundle, IntegralGrid
from bruker_nmr.domain.parameters import freeze
from bruker_nmr.io.errors import InputError

MAX_SI = 4 * 1024 * 1024


def si_choices(points):
    """Offer one to four times the rounded FID length, within the processing cap."""
    if type(points) is not int or not 1 <= points <= MAX_SI:
        return ()
    minimum = max(2, 1 << (points - 1).bit_length())
    return tuple(minimum << i for i in range(3) if minimum << i <= MAX_SI)


def automatic_si(points):
    if not si_choices(points):
        raise InputError("unsupported_zero_fill_size")
    return min(MAX_SI, max(2, 1 << (2 * points - 1).bit_length()))


@dataclass(frozen=True)
class Recipe:
    si: int
    lb: float = 0.3
    phase0: float = 0.0
    phase1: float = 0.0
    known_phase: bool = False


def immutable(array):
    return np.frombuffer(array.tobytes(), dtype=array.dtype)


@dataclass(frozen=True)
class RawResult:
    fid: np.ndarray = field(repr=False)
    ppm: np.ndarray = field(repr=False)
    real: np.ndarray = field(repr=False)
    imaginary: np.ndarray = field(repr=False)
    recipe: Recipe
    acquisition: object = field(repr=False)
    integration: IntegralGrid = field(repr=False)
    annotations: AnnotationBundle = field(default_factory=AnnotationBundle)
    raw_real: object = None
    raw_imaginary: object = None

    @property
    def can_phase(self):
        return True


def process(fid, params, recipe, cancelled=None):
    if fid.ndim != 1 or not np.iscomplexobj(fid) or not np.isfinite(fid).all():
        raise InputError("invalid_complex_fid")
    if fid.flags.writeable:
        fid = immutable(np.asarray(fid, dtype=np.complex128))
    n = len(fid)
    if (
        type(recipe.si) is not int
        or recipe.si < n
        or recipe.si > MAX_SI
        or recipe.si < 2
        or recipe.si & (recipe.si - 1)
    ):
        raise InputError("unsupported_zero_fill_size")
    if not np.isfinite(recipe.lb) or not 0 <= recipe.lb <= 100:
        raise InputError("invalid_line_broadening")
    if not np.isfinite([recipe.phase0, recipe.phase1]).all() or (
        not recipe.known_phase and (recipe.phase0 or recipe.phase1)
    ):
        raise InputError("explicit_known_phase_required")
    delay, sw = params["GRPDLY"], params["SW_h"]
    if not np.isfinite(delay) or not 0 <= delay < n:
        raise InputError("unverified_digital_filter_delay")
    if cancelled is not None and cancelled.is_set():
        raise InputError("processing_cancelled")
    # NumPy ifft includes 1/SI. Raw arbitrary units intentionally retain that scale.
    with np.errstate(over="raise", invalid="raise"):
        window = np.exp(-np.pi * recipe.lb * np.arange(n) / sw)
        transformed = np.conj(np.fft.fftshift(np.fft.ifft(fid * window, n=recipe.si)))
        k = np.arange(recipe.si)
        transformed *= np.exp(2j * np.pi * delay * k / recipe.si)
        if recipe.known_phase:
            transformed *= np.exp(1j * np.deg2rad(recipe.phase0 + recipe.phase1 * k / recipe.si))
    if cancelled is not None and cancelled.is_set():
        raise InputError("processing_cancelled")
    if not np.isfinite(transformed).all():
        raise InputError("nonfinite_raw_transform")
    # Acquisition-referenced axis, not a claim of chemical zero calibration.
    sf = params["BF1"]
    step = sw / sf / recipe.si
    offset = params["O1"] / sf + sw / 2 / sf
    ppm = immutable(offset - k * step)
    real, imaginary = immutable(transformed.real), immutable(transformed.imag)
    return RawResult(
        fid,
        ppm,
        real,
        imaginary,
        recipe,
        MappingProxyType({k: freeze(v) for k, v in params.items()}),
        IntegralGrid(real, offset, step),
    )
