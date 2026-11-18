"""Round-trip annotation metadata bound to the exact on-disk processed spectrum."""

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path

from bruker_nmr.domain.integrals import Normalization, integral_snapshot
from bruker_nmr.io.source import _is_link

EXTRA = "nmr-go-markup.json"
NATIVE = ("intrng", "integrals.txt", "peaklist.xml", "peakrng", "peaks", "procs")
INPUTS = (*NATIVE, "1r", "1i")
MAX_EXTRA = 4 * 1024 * 1024


@dataclass(frozen=True)
class Markup:
    revision: str
    calibration: float = 0.0
    referenced: bool = False
    markers_visible: bool = True
    label_mode: str = "auto"


def fingerprint(folder, names):
    result = {}
    for name in names:
        path = folder / name
        if _is_link(path):
            raise ValueError("Unsafe annotation path")
        if not path.exists():
            result[name] = None
        else:
            with path.open("rb") as stream:
                result[name] = hashlib.file_digest(stream, "sha256").hexdigest()
    return result


def revision(parts):
    return hashlib.sha256(json.dumps(parts, sort_keys=True).encode()).hexdigest()


def read_markup(folder, bundle, grid, parts):
    """Use native annotations when the app metadata is absent, stale or invalid."""
    plain = Markup(revision(parts))
    if parts[EXTRA] is None:
        return bundle, plain
    try:
        path = Path(folder) / EXTRA
        if path.stat().st_size > MAX_EXTRA:
            raise ValueError("Metadata too large")
        with path.open("rb") as stream:
            raw = stream.read(MAX_EXTRA + 1)
        if len(raw) > MAX_EXTRA:
            raise ValueError("Metadata too large")
        meta = json.loads(raw)
        if meta["version"] != 1 or meta["inputs"] != {key: parts[key] for key in INPUTS}:
            raise ValueError("Stale metadata")
        regions = bundle.integrals.regions
        if meta["bins"] != [[r.left, r.right] for r in regions]:
            raise ValueError("Region mismatch")
        flags = meta["peaks"]
        if len(flags) != len(bundle.peaks) or any(
            not isinstance(row, list) or len(row) != 2 or any(type(v) is not bool for v in row)
            for row in flags
        ):
            raise ValueError("Peak metadata mismatch")
        kind, scale, index, protons = meta["normalization"]
        anchor = None
        if index is not None:
            if type(index) is not int or not 0 <= index < len(regions):
                raise ValueError("Reference missing")
            anchor = regions[index].id
            if grid.reference_scale(regions[index], protons) != scale:
                raise ValueError("Reference mismatch")
        normalization = Normalization(kind, scale, anchor, protons, "saved NMR-go markup")
        calibration = meta["calibration"]
        if (
            type(calibration) not in (float, int)
            or not -1e100 <= calibration <= 1e100
            or type(meta["referenced"]) is not bool
            or (calibration and not meta["referenced"])
            or meta["label_mode"] not in ("auto", "all", "hidden")
            or type(meta["markers_visible"]) is not bool
        ):
            raise ValueError("Invalid display metadata")
        grid.shifted(calibration)
        peaks = tuple(
            replace(p, label_visible=visible, needs_review=review)
            for p, (visible, review) in zip(bundle.peaks, flags)
        )
        restored = replace(
            bundle,
            integrals=replace(bundle.integrals, normalization=normalization),
            peaks=peaks,
            issues=tuple(i for i in bundle.issues if i != "integrals:reference_required"),
        )
        integral_snapshot(restored.integrals, grid)
        return restored, replace(
            plain,
            calibration=calibration,
            referenced=meta["referenced"],
            markers_visible=meta["markers_visible"],
            label_mode=meta["label_mode"],
        )
    except (ValueError, TypeError, KeyError, IndexError, OSError, ArithmeticError, RecursionError):
        return replace(bundle, issues=(*bundle.issues, "markup:stale_or_invalid")), plain
