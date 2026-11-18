"""Startup diagnostics omit error messages, paths and user-controlled identities."""
import json

from bruker_nmr.diagnostics import record_startup_failure


def test_startup_failure_is_sanitized(tmp_path, monkeypatch):
    monkeypatch.setattr("bruker_nmr.diagnostics.tempfile.gettempdir", lambda: str(tmp_path))
    error = ModuleNotFoundError("private input title /private/source/path")
    error.name = "missing.module"
    result = record_startup_failure({"version": "0.2.0", "source_commit": "a" * 40}, error)
    payload = json.loads(result.read_text())
    assert payload["exception_type"] == "ModuleNotFoundError"
    assert payload["missing_module"] == "missing.module"
    assert "private" not in result.read_text()
    record_startup_failure({"version": "private", "source_commit": "/private/path"}, error)
    assert "private" not in result.read_text()
