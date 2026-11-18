"""Small non-sensitive source/build identity, without installation paths."""
import json
import subprocess
import sys
from pathlib import Path

from bruker_nmr import __version__


def build_identity() -> dict:
    packaged = Path(__file__).with_name("build_identity.json")
    if packaged.is_file():
        return json.loads(packaged.read_text(encoding="utf-8-sig"))
    commit = "unavailable"
    if not getattr(sys, "frozen", False):
        try:
            commit = subprocess.check_output(
                ["git", "rev-parse", "HEAD"], cwd=Path(__file__).resolve().parents[2],
                stderr=subprocess.DEVNULL, timeout=3, text=True).strip()
        except (OSError, subprocess.SubprocessError):
            pass
    return {"version": __version__, "source_commit": commit, "build_kind": "source"}
