"""Opt-in handoff of sanitized startup failures; no source content or telemetry."""
import json
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path


def record_startup_failure(identity, error):
    location = Path(tempfile.gettempdir()) / "NMR-go" / "startup-error.json"
    result = {"scope": "startup", "utc": datetime.now(timezone.utc).isoformat(),
              "exception_type": type(error).__name__}
    for key, pattern in [("version", r"[0-9]+\.[0-9]+\.[0-9]+"),
                         ("source_commit", r"[0-9a-f]{40}")]:
        value = identity.get(key, "")
        result[key] = value if isinstance(value, str) and re.fullmatch(pattern, value) else "unavailable"
    module = getattr(error, "name", None)
    if isinstance(module, str) and re.fullmatch(r"[A-Za-z0-9_.]+", module):
        result["missing_module"] = module
    try:
        location.parent.mkdir(parents=True, exist_ok=True)
        location.write_text(json.dumps(result, indent=2), encoding="utf-8")
        return location
    except OSError:
        return None
