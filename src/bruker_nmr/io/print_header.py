"""Optional local-only print text; never log or serialize sample metadata."""

import math
import re
import unicodedata


def clean_text(value):
    if not isinstance(value, str):
        return ""
    text = " ".join(
        "".join(c if not unicodedata.category(c).startswith("C") else " " for c in value).split()
    )
    # Titles sometimes contain an acquisition workstation path. Do not prefill it.
    if re.search(r"(?:^|\s)(?:[A-Za-z]:[\\/]|/|\\\\)", text):
        return ""
    return text


def optional_title(path):
    from bruker_nmr.io.source import _is_link

    try:
        if _is_link(path):
            return ""
        with path.open("rb") as stream:
            blob = stream.read(4097)
        if len(blob) > 4096 or b"\0" in blob:
            return ""
        try:
            text = blob.decode("utf-8")
        except UnicodeDecodeError:
            text = blob.decode("cp1252")
        return clean_text(text)
    except (OSError, UnicodeError):
        return ""


def read_header(source, candidate, acquisition=None):
    from bruker_nmr.io.reader import _acquisition

    a = (
        acquisition
        if acquisition is not None
        else _acquisition(candidate.experiment, set(source.files))
    )
    title = ""
    if candidate.processing is not None:
        path = candidate.processing / "title"
        if path in source.files:
            title = optional_title(path)
    parent = candidate.experiment.parent
    if parent != source.root and source.root in parent.parents:
        dataset = parent.name
    elif source.original.is_dir() and candidate.experiment == source.root:
        dataset = (
            candidate.experiment.parent.name
            if candidate.experiment.name.isdecimal()
            else candidate.experiment.name
        )
    else:
        dataset = (
            source.original.stem
            if source.original.suffix.lower() == ".zip"
            else source.original.name
        )
    filename = (
        clean_text(source.original.stem)
        if source.original.suffix.lower() == ".zip"
        else clean_text(dataset)
    )
    names = [
        v for v in (filename, title or (clean_text(dataset) if dataset != filename else "")) if v
    ]
    if len(names) == 2 and names[0] == names[1]:
        names.pop()
    # Bound only descriptive names so long titles cannot displace numeric facts.
    names = [v if len(v) <= 384 else v[:383] + "…" for v in names]
    solvent = clean_text(a.get("SOLVENT", ""))
    solvent = solvent if len(solvent) <= 120 else solvent[:119] + "…"
    facts = [solvent, clean_text(a.get("NUC1", ""))]
    for key, prefix, suffix in [("SFO1", "", " MHz"), ("NS", "NS=", "")]:
        value = a.get(key)
        try:
            if (
                isinstance(value, (int, float))
                and not isinstance(value, bool)
                and math.isfinite(value)
                and value > 0
            ):
                if key != "NS" or int(value) == value:
                    facts.append(f"{prefix}{value:g}{suffix}")
        except (OverflowError, ValueError, TypeError):
            pass
    header = "\n".join(v for v in (*names, ", ".join(v for v in facts if v)) if v)
    return header if len(header) <= 1024 else header[:1023] + "…"
