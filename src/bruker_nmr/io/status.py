"""Bounded status-only parsing through the pinned nmrglue JCAMP implementation."""
import io
import math
import re
import warnings
from pathlib import Path

from nmrglue.fileio import bruker

from bruker_nmr.io.errors import InputError


class _Lines(io.StringIO):
    def readline(self, *args):
        line = super().readline(*args)
        if line == "":
            # nmrglue's multiline parser otherwise loops forever on truncated values.
            raise EOFError
        return line


def read_status(path: Path) -> dict:
    try:
        return _read_status(path)
    except InputError:
        raise
    except (OSError, UnicodeError, OverflowError):
        raise InputError("status_read_failed") from None


def _read_status(path: Path) -> dict:
    if path.name not in {"acqus", "procs"}:
        raise InputError("status_file_required")
    if path.is_symlink():
        raise InputError("source_symlink")
    if not path.is_file():
        raise InputError("missing_" + path.name)
    if path.stat().st_size > 2 * 1024 * 1024:
        raise InputError("status_size_limit")
    payload = path.read_bytes()
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        text = payload.decode("cp1252")
    if "\x00" in text or len(text.splitlines()) > 32000:
        raise InputError("invalid_status")
    params = {}
    # Control framing/bounds here; nmrglue parses every scalar/string/array value.
    lines = _Lines(text)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error")
            while lines.tell() < len(text):
                line = lines.readline().rstrip()
                if not line or line.startswith("$$"):
                    continue
                if line.startswith("##END="):
                    break
                if line.startswith("##$"):
                    if "=" not in line:
                        raise InputError("invalid_status")
                    key = line[3:line.index("=")]
                    if not re.fullmatch(r"[A-Za-z0-9_]+", key) or key in params:
                        raise InputError("duplicate_or_invalid_parameter")
                    value_text = line.split("=", 1)[1].lstrip()
                    if value_text.startswith("("):
                        bounds = re.match(r"\(0\.\.(\d+)\)", value_text)
                        if bounds is None or int(bounds[1]) >= 100000:
                            raise InputError("status_array_limit")
                    parsed_key, value = bruker.parse_jcamp_line(line, lines)
                    if value_text.startswith("(") and len(value) != int(bounds[1]) + 1:
                        raise InputError("status_array_count_mismatch")
                    params[parsed_key] = value
                elif not line.startswith("##"):
                    raise InputError("invalid_status")
    except InputError:
        raise
    except Exception:
        raise InputError("invalid_status") from None
    return params


def number(params: dict, key: str, *, integer=False, positive=False):
    value = params.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise InputError("invalid_or_missing_" + key.lower())
    if abs(value) > 1e100 or not math.isfinite(value) or (positive and value <= 0):
        raise InputError("invalid_or_missing_" + key.lower())
    if integer and value != int(value):
        raise InputError("invalid_or_missing_" + key.lower())
    return int(value) if integer else float(value)
