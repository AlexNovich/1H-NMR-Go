"""Source dependency smoke is distinct from frozen and actual Windows acceptance."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.mark.parametrize("scale", ["1", "1.5", "2"])
def test_window_self_test_without_network(tmp_path, scale):
    result = tmp_path / "result.json"
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen", QT_SCALE_FACTOR=scale, MPLCONFIGDIR=str(tmp_path / "mpl"))
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    code = ("import socket,runpy; "
            "socket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(RuntimeError('No network')); "
            "runpy.run_module('bruker_nmr',run_name='__main__')")
    completed = subprocess.run([sys.executable, "-c", code, "--self-test", str(result)],
                               env=env, check=False, timeout=120)
    report = json.loads(result.read_text())
    assert completed.returncode == 0, (report.get('error_type'), report.get('check_error'))
    assert report["status"] == "passed"
    assert len(report["checks"]) == 17
    assert report["window_ready_seconds"] > 0
    assert report["window_fits_screen"]
    assert report["chooser_fits_screen"]
    assert report["integral_table_fits_screen"]
    assert report["phase_fits_screen"]
    assert report["baseline_fits_screen"]
    assert report["workbench_fits_screen"]
    assert report["pdf_preview_fits_screen"]
    assert len(report["pdf_examples"]) == 2
    assert report["peak_table_fits_screen"]
    for name in report["review_images"]:
        assert (tmp_path/name).read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert result.with_suffix(".pdf").is_file()
