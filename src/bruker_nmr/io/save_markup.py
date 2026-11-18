"""Write Bruker annotation files with an exact NMR-go round trip and recoverable backups."""

import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import uuid
import xml.etree.ElementTree as ET
import zipfile
from datetime import datetime
from pathlib import Path

from bruker_nmr.domain.integrals import integral_snapshot
from bruker_nmr.io.annotations import MAX_BYTES, _peaks, _ranges, _table
from bruker_nmr.io.errors import InputError
from bruker_nmr.io.markup import EXTRA, INPUTS, MAX_EXTRA, NATIVE, fingerprint, revision
from bruker_nmr.io.source import Source, _is_link

OWNED = (*NATIVE, EXTRA)
BACKUP_PREFIX = ".nmr-go-backup-"


def _number(value):
    if not math.isfinite(value) or abs(value) > 1e100:
        raise InputError("markup_value_out_of_range")
    return format(value, ".17g")


def _scale_status(path, scale):
    raw = path.read_bytes()
    lines = raw.splitlines(keepends=True)
    eol = b"\r\n" if b"\r\n" in raw else b"\n"
    matches = [i for i, line in enumerate(lines) if re.match(rb"##\$INTSCL\s*=", line)]
    if len(matches) > 1:
        raise InputError("source_changed_reopen")
    line = b"##$INTSCL= " + _number(scale).encode("ascii") + eol if scale is not None else b""
    if matches:
        lines[matches[0]] = line
    elif line:
        index = next(
            (i for i, value in enumerate(lines) if value.startswith(b"##END=")), len(lines)
        )
        if index and not lines[index - 1].endswith((b"\n", b"\r")):
            lines[index - 1] += eol
        lines.insert(index, line)
    return b"".join(lines)


def _serialize(folder, state, parts):
    grid = state.data.integration
    norm = state.integrals.normalization
    values = integral_snapshot(state.integrals, grid)
    ranges = [f"1D {len(values)} #regions in PPM", "# High Low Bias Slope"]
    table = ["# Integral values; units: ppm"]
    for n, (region, row) in enumerate(zip(state.integrals.regions, values), 1):
        bounds = f"{_number(row.high)} {_number(row.low)}"
        ranges.append(f"{bounds} {_number(region.bias)} {_number(region.slope)}")
        if row.value is not None:
            table.append(f"{n} {bounds} {_number(row.value)}")
    xml = ET.Element("PeakList")
    peaks = ET.SubElement(xml, "PeakList1D")
    for p in state.peaks.records:
        ET.SubElement(
            peaks, "Peak1D", F1=_number(p.ppm), intensity=_number(p.intensity), type=str(p.type)
        )
    scale = (
        math.ldexp(norm.scale * grid.step, state.data.operator.provenance.processed["NC_proc"])
        if norm.scale is not None
        else None
    )
    if scale is not None and scale <= 0:
        raise InputError("markup_value_out_of_range")
    files = {
        "intrng": ("\n".join(ranges) + "\n").encode(),
        "integrals.txt": ("\n".join(table) + "\n").encode(),
        "peaklist.xml": ET.tostring(xml, encoding="utf-8", xml_declaration=True),
        "procs": _scale_status(folder / "procs", scale),
    }
    if any(len(blob) > MAX_BYTES for blob in files.values()):
        raise InputError("markup_size_limit")
    # Exercise the same native parsers before replacing any file.
    issues = []
    parsed = _ranges(files["intrng"].decode(), grid, issues)
    rows = _table(files["integrals.txt"].decode(), grid, issues)
    picked = _peaks(files["peaklist.xml"].decode(), grid, issues)
    bins = [[r.left, r.right] for r in state.integrals.regions]
    if (
        issues
        or bins != [[r.left, r.right] for r in parsed]
        or len(picked) != len(state.peaks.records)
        or len(rows) != sum(v.value is not None for v in values)
    ):
        raise InputError("markup_round_trip_failed")
    written = {k: hashlib.sha256(v).hexdigest() for k, v in files.items()}
    new_parts = {k: written.get(k, None if k in ("peaks", "peakrng") else parts[k]) for k in INPUTS}
    ref = next((i for i, r in enumerate(state.integrals.regions) if r.id == norm.anchor_id), None)
    files[EXTRA] = json.dumps(
        {
            "version": 1,
            "inputs": new_parts,
            "bins": bins,
            "normalization": [norm.kind, norm.scale, ref, norm.protons],
            "peaks": [[p.label_visible, p.needs_review] for p in state.peaks.records],
            "calibration": state.calibration,
            "referenced": state.referenced,
            "markers_visible": state.peaks.markers_visible,
            "label_mode": state.peaks.label_mode,
        },
        allow_nan=False,
        separators=(",", ":"),
    ).encode()
    if len(files[EXTRA]) > MAX_EXTRA:
        raise InputError("markup_size_limit")
    return files, revision({**new_parts, EXTRA: hashlib.sha256(files[EXTRA]).hexdigest()})


def _write(folder, state, expected):
    lock = folder / ".nmr-go-save.lock"
    if _is_link(lock):
        raise InputError("markup_save_locked")
    try:
        lock.touch(exist_ok=False)
    except FileExistsError:
        raise InputError("markup_save_locked") from None
    try:
        parts = fingerprint(folder, (*INPUTS, EXTRA))
        if revision(parts) != expected:
            raise InputError("markup_changed_reopen")
        files, saved = _serialize(folder, state, parts)
        with tempfile.TemporaryDirectory(prefix=".nmr-go-write-", dir=folder) as tmp:
            stage = Path(tmp)
            for name, payload in files.items():
                with (stage / name).open("xb") as stream:
                    stream.write(payload)
                    stream.flush()
                    os.fsync(stream.fileno())
            backup = folder / (
                BACKUP_PREFIX
                + datetime.now().strftime("%Y%m%d-%H%M%S")
                + "-"
                + uuid.uuid4().hex[:8]
                + ".zip"
            )
            absent = [name for name in OWNED if parts[name] is None]
            with zipfile.ZipFile(stage / "backup.zip", "x", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("restore.json", json.dumps({"absent": absent, "version": 1}))
                for name in OWNED:
                    if parts[name] is not None:
                        archive.write(folder / name, name)
            with (stage / "backup.zip").open("rb+") as stream:
                os.fsync(stream.fileno())
            if fingerprint(folder, (*INPUTS, EXTRA)) != parts:
                raise InputError("markup_changed_reopen")
            os.replace(stage / "backup.zip", backup)
            applied = []
            try:
                for name in OWNED:
                    if name in files:
                        os.replace(stage / name, folder / name)
                    elif parts[name] is not None:
                        (folder / name).unlink()
                    else:
                        continue
                    applied.append(name)
            except OSError:
                try:
                    with zipfile.ZipFile(backup) as archive:
                        for name in reversed(applied):
                            if name in absent:
                                (folder / name).unlink()
                            else:
                                with archive.open(name) as src, (stage / name).open("xb") as dst:
                                    shutil.copyfileobj(src, dst)
                                os.replace(stage / name, folder / name)
                except (OSError, zipfile.BadZipFile):
                    raise InputError("markup_restore_required") from None
                raise
        return saved, backup.name
    finally:
        lock.unlink(missing_ok=True)


def save_markup(path, locator, state, expected, destination=None):
    """Write only the selected pdata; for ZIP create a new full dataset under destination."""
    if (
        state.data.opening_mode != "operator"
        or state.data.phase_steps
        or state.data.baseline is not None
    ):
        raise InputError("markup_processed_required")
    try:
        with Source(path) as source:
            matches = [
                c
                for c in source.discover()
                if c.processed_available
                and c.experiment.relative_to(source.root).as_posix() == locator[0]
                and c.processing.relative_to(source.root).as_posix() == locator[1]
            ]
            if len(matches) != 1:
                raise InputError("source_changed_reopen")
            proc = matches[0].processing
            if source.original.is_dir():
                if destination is not None:
                    raise InputError("markup_invalid_destination")
                saved, backup = _write(proc, state, expected)
                return Path(path), saved, backup
            if destination is None:
                raise InputError("markup_destination_required")
            parent = Path(destination).absolute()
            if not parent.is_dir() or any(_is_link(p) for p in (parent, *parent.parents)):
                raise InputError("markup_invalid_destination")
            # An exclusive directory reservation prevents overwriting even an empty folder.
            target = Path(tempfile.mkdtemp(prefix="NMR-go-dataset-", dir=parent))
            try:
                shutil.copytree(source.root, target, dirs_exist_ok=True)
                saved, backup = _write(target / proc.relative_to(source.root), state, expected)
                return target, saved, backup
            except BaseException:
                shutil.rmtree(target)
                raise
    except InputError:
        raise
    except (OSError, ArithmeticError, ValueError, zipfile.BadZipFile):
        raise InputError("markup_save_failed") from None
