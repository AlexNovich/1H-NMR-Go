"""Bounded optional operator annotations; no titles, XML headers or paths retained."""
import math
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation

from bruker_nmr.domain.integrals import (
    MAX_REGIONS,
    AnnotationBundle,
    IntegralState,
    Normalization,
    Peak,
    Region,
)

MAX_BYTES = 2 * 1024 * 1024
MAX_LINES = 32000
MAX_PEAKS = 20000


def _text(path):
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("size_limit")
    with path.open("rb") as stream:
        payload = stream.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES:
        raise ValueError("size_limit")
    text = payload.decode("utf-8-sig")
    if "\0" in text or len(text.splitlines()) > MAX_LINES:
        raise ValueError("text_limit")
    return text


def _float(token):
    value = float(token)
    if not math.isfinite(value) or abs(value) > 1e100:
        raise ValueError("nonfinite_or_extreme_value")
    return value


def _precision(token):
    # Decimal text precision, including scientific notation, is operator provenance.
    try:
        result = float(Decimal(10) ** Decimal(Decimal(token).as_tuple().exponent)) / 2
    except (InvalidOperation, OverflowError, TypeError):
        raise ValueError("invalid_precision") from None
    if not math.isfinite(result):
        raise ValueError("invalid_precision")
    return result


def _units(text):
    for line in text.splitlines():
        declared = re.search(r"(?i)\bunits?\s*[:=(]\s*([A-Za-z]+)", line)
        if declared and declared[1].lower() != "ppm":
            raise ValueError("unsupported_units")
        if re.search(r"(?i)(regions|units?)", line):
            if re.search(r"(?i)\b(hz|points?|seconds?|radians?)\b", line):
                raise ValueError("unsupported_units")
            if "#regions" in line.lower() and not re.search(r"(?i)\bppm\b", line):
                raise ValueError("unsupported_or_missing_units")


def _lines(text):
    _units(text)
    for line in text.splitlines():
        if len(line) > 4096:
            raise ValueError("line_limit")
        if "#regions" in line.lower():
            continue  # TopSpin range-count/unit header is not a range row.
        content = line.split("#", 1)[0].strip()
        if not content:
            continue
        fields = content.split()
        # Other export headers are ignored; malformed numeric rows are reported.
        numeric_tail = False
        if len(fields) == 4:
            try:
                float(fields[-1])
                numeric_tail = True
            except ValueError:
                pass
        if numeric_tail or re.match(r"^[+\-\d.]", content) or fields[0].lower() in {"nan", "inf", "-inf"}:
            yield fields


@dataclass(frozen=True)
class _TableRow:
    high: float
    low: float
    value: float
    coordinate_tolerance: float
    tolerance: float


def _ranges(text, grid, issues):
    result = []
    invalid = 0
    for fields in _lines(text):
        if len(result) >= MAX_REGIONS:
            issues.append("intrng:region_limit")
            break
        try:
            if len(fields) != 4:
                raise ValueError("row_width")
            a, b, bias, slope = map(_float, fields)
            left, right = grid.bounds(a, b)
            result.append(Region(f"operator-{len(result) + 1}", left, right, "intrng",
                                 (a, b), bias, slope))
        except ValueError:
            invalid += 1
    if invalid:
        issues.append(f"intrng:invalid_rows:{invalid}")
    return result


def _table(text, grid, issues):
    result = []
    invalid = 0
    for fields in _lines(text):
        if len(result) >= MAX_REGIONS:
            issues.append("integrals.txt:region_limit")
            break
        try:
            if len(fields) != 4:
                raise ValueError("row_width")
            number, a, b, value = map(_float, fields)
            if number < 1 or number != int(number):
                raise ValueError("invalid_row_number")
            coordinate_tolerance = max(_precision(fields[1]), _precision(fields[2]))
            grid.bounds(a, b, tolerance=coordinate_tolerance)
            result.append(_TableRow(max(a, b), min(a, b), value,
                                    coordinate_tolerance, _precision(fields[3])))
        except ValueError:
            invalid += 1
    if invalid:
        issues.append(f"integrals.txt:invalid_rows:{invalid}")
    return result


def _peaks(text, grid, issues):
    upper = text.upper()
    if "<!DOCTYPE" in upper or "<!ENTITY" in upper:
        raise ValueError("xml_declarations_forbidden")
    root = ET.fromstring(text)
    if root.tag.rsplit("}", 1)[-1] not in {"PeakList", "PeakList1D"}:
        raise ValueError("unsupported_peak_document")
    for element in root.iter():
        if any(k.lower() in {"unit", "units", "f1unit"} and v.lower() != "ppm"
               for k, v in element.attrib.items()):
            raise ValueError("unsupported_peak_units")
    records = [e for e in root.iter() if e.tag.rsplit("}", 1)[-1] == "Peak1D"]
    if len(records) > MAX_PEAKS:
        raise ValueError("peak_limit")
    result, invalid = [], 0
    for element in records:
        try:
            ppm = _float(element.attrib["F1"])
            intensity = _float(element.attrib["intensity"])
            type_ = _float(element.attrib["type"])
            if not grid.minimum <= ppm <= grid.offset or type_ != int(type_):
                raise ValueError("peak_out_of_range_or_type")
            result.append(Peak(f"operator-peak-{len(result) + 1}", ppm, intensity, int(type_)))
        except (KeyError, ValueError):
            invalid += 1
    if invalid:
        issues.append(f"peaklist.xml:invalid_rows:{invalid}")
    return tuple(result)


def _normalize(regions, table, grid, params, issues):
    if not regions:
        if any(i.startswith(("intrng:", "integrals.txt:")) for i in issues):
            return IntegralState((), Normalization("unverified", provenance="annotation import incomplete"))
        return IntegralState()
    matches, conflict = [], False
    for index, region in enumerate(regions):
        high, low = sorted(region.imported_bounds, reverse=True)
        candidates = [j for j, row in enumerate(table)
                      if abs(row.high - high) <= grid.step + row.coordinate_tolerance
                      and abs(row.low - low) <= grid.step + row.coordinate_tolerance]
        if len(candidates) == 1:
            j, = candidates
            matches.append((index, j))
        elif len(candidates) > 1:
            conflict = True
    used = [j for _, j in matches]
    if len(set(used)) != len(used) or len(set(used)) < len(table):
        conflict = True
    for i, j in matches:
        regions[i] = replace(regions[i], table_value=table[j].value,
                             table_tolerance=table[j].tolerance)
    if any(not r.correction_supported for r in regions):
        issues.append("intrng:unsupported_bias_slope")
        return IntegralState(tuple(regions), Normalization("unverified", provenance="bias/slope unsupported"))
    scale = None
    intscl = params.get("INTSCL")
    if (not isinstance(intscl, bool) and isinstance(intscl, (int, float))
            and math.isfinite(intscl) and 0 < intscl <= 1e100):
        scale = math.ldexp(float(intscl), -params["NC_proc"]) / grid.step
        if not math.isfinite(scale) or scale <= 0:
            scale = None
    elif intscl is not None:
        issues.append("procs:invalid_intscl")
    provenance = "operator INTSCL; no inferred reference"
    if scale is not None:
        for i, j in matches:
            row = table[j]
            predicted = scale * grid.area(regions[i])
            slack = max(row.tolerance, abs(predicted) * 1e-12)
            if not math.isfinite(predicted) or abs(predicted - row.value) > slack:
                conflict = True
    else:
        # Find intersection of scale intervals allowed by the printed value precision.
        lower, upper = 0., math.inf
        candidates = []
        for i, j in matches:
            area, row = grid.area(regions[i]), table[j]
            if abs(area) <= grid.reference_floor:
                conflict |= abs(row.value) > row.tolerance
                continue
            a, b = sorted(((row.value - row.tolerance) / area,
                           (row.value + row.tolerance) / area))
            lower, upper = max(lower, a), min(upper, b)
            candidates.append(row.value / area)
        if candidates and 0 < upper and lower <= upper:
            candidates.sort()
            scale = min(upper, max(lower, candidates[len(candidates) // 2]))
            if not math.isfinite(scale) or scale <= 0:
                scale = None
            provenance = "matched integral table scale; no inferred reference"
        elif candidates:
            conflict = True
    if conflict:
        issues.append("integrals:source_conflict")
        return IntegralState(tuple(regions), Normalization("unverified", provenance="annotation sources conflict"))
    if scale is None:
        if regions:
            issues.append("integrals:reference_required")
            return IntegralState(tuple(regions), Normalization("unverified", provenance="operator scale unavailable"))
        return IntegralState()
    return IntegralState(tuple(regions), Normalization("imported", scale, provenance=provenance))


def read_annotations(processing, files, grid, params):
    """Optional-file failures remain notices; the verified spectrum stays available."""
    available = {p.name for p in files if p.parent == processing}
    issues = []
    results = {}
    for name, parser in (("intrng", _ranges), ("integrals.txt", _table), ("peaklist.xml", _peaks)):
        if name not in available:
            continue
        try:
            results[name] = parser(_text(processing / name), grid, issues)
        except (OSError, UnicodeError, ValueError, ET.ParseError, OverflowError):
            issues.append(name + ":unreadable_or_unsupported")
    regions, table = list(results.get("intrng", [])), results.get("integrals.txt", [])
    if not regions and table:
        issues.append("integrals:table_geometry_reduced_precision")
        for row in table:
            left, right = grid.bounds(row.high, row.low, tolerance=row.coordinate_tolerance)
            regions.append(Region(f"operator-{len(regions) + 1}", left, right, "integrals.txt",
                                  (row.high, row.low)))
    try:
        state = _normalize(regions, table, grid, params, issues)
    except (ValueError, OverflowError, ZeroDivisionError):
        issues.append("integrals:normalization_unavailable")
        state = IntegralState(tuple(regions), Normalization("unverified", provenance="invalid operator normalization"))
    if "peaklist.xml" not in available and {"peakrng", "peaks"} & available:
        issues.append("peaks:binary_or_ranges_not_imported")
    return AnnotationBundle(state, tuple(results.get("peaklist.xml", ())), tuple(issues))
