"""Validated status-only complex FID decoding through the pinned binary reader."""

import warnings
from types import MappingProxyType

import numpy as np
from nmrglue.fileio import bruker

from bruker_nmr.domain.parameters import freeze
from bruker_nmr.io.errors import InputError
from bruker_nmr.io.reader import _acquisition, _binary_layout, _choice
from bruker_nmr.io.status import number
from bruker_nmr.processing.fid import immutable


def validate(experiment, a, limits):
    _choice(a, "AQ_mod", {3})
    td = number(a, "TD", integer=True, positive=True)
    if td % 2:
        raise InputError("unpaired_fid_values")
    dtype, order = _choice(a, "DTYPA", {0, 2}), _choice(a, "BYTORDA", {0, 1})
    delay = number(a, "GRPDLY")
    if not 0 <= delay < td / 2:
        raise InputError("unverified_digital_filter_delay")
    number(a, "BF1", positive=True)
    number(a, "O1")
    nc = number(a, "NC", integer=True)
    if not -128 <= nc <= 128:
        raise InputError("unsupported_acquisition_scale")
    if 'PKNL' in a and a['PKNL'] is not True and a['PKNL'] != 'yes':
        raise InputError("unsupported_pknl")
    _binary_layout(experiment / "fid", td, dtype, order, limits.max_points)
    return td, dtype, order


def read_fid(source, candidate):
    try:
        a = _acquisition(candidate.experiment, set(source.files))
        td, dtype, order = validate(candidate.experiment, a, source.limits)
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            _, values = bruker.read_binary(
                str(candidate.experiment / "fid"),
                shape=((candidate.experiment / "fid").stat().st_size // (8 if dtype == 2 else 4),),
                cplex=False, big=order == 1, isfloat=dtype == 2
            )
        values = values[:td].astype(np.float64)
        if not np.isfinite(values).all():
            raise InputError("nonfinite_fid_values")
        fid = immutable(values[::2] + 1j * values[1::2])
        params = MappingProxyType({k: freeze(v) for k, v in a.items()})
        return fid, params
    except InputError:
        raise
    except (OSError, ValueError, Warning, FloatingPointError):
        raise InputError("fid_read_failed") from None
