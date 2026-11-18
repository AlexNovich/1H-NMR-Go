"""Exercise the real index in disposable repositories, without confidential inputs."""
import subprocess
import sys
from pathlib import Path

import pytest

GUARD = Path(__file__).resolve().parents[1] / "scripts/check_no_sensitive_data.py"


def run_guard(tmp_path, name, data, stale=False):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    p = tmp_path / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)
    subprocess.run(["git", "-C", str(tmp_path), "add", "--", name], check=True)
    if stale:
        p.write_text("safe worktree\n")
    return subprocess.run([sys.executable, str(GUARD), "--staged"], cwd=tmp_path,
                          capture_output=True).returncode


@pytest.mark.parametrize("name,data", [
    ("source with spaces.py", b"x = 1\n"),
    ("tests/generated.py", b"def signal(x): return x*x\n"),
])
def test_allowed(tmp_path, name, data):
    assert run_guard(tmp_path, name, data) == 0


@pytest.mark.parametrize("name,data", [
    ("notes.txt", b"gh" + b"p_" + b"a" * 36),
    ("hidden.txt", b"PK\x03\x04synthetic archive marker"),
    ("renamed.txt", b"##" + b"$TD= 32\n"),
    ("experiment/fid", b"independently generated"),
    ("tests/fixtures/image.dat", b"\x89PNG\0generated"),
    (".env.local", b"placeholder"),
])
def test_blocked_even_with_stale_worktree(tmp_path, name, data):
    assert run_guard(tmp_path, name, data, stale=True) == 1


def test_rename_and_delete(tmp_path):
    assert run_guard(tmp_path, "old name.txt", b"hello") == 0
    subprocess.run(["git", "mv", "old name.txt", "new name.txt"], cwd=tmp_path, check=True)
    assert subprocess.run([sys.executable, str(GUARD), "--tracked"], cwd=tmp_path).returncode == 0
    subprocess.run(["git", "rm", "-f", "new name.txt"], cwd=tmp_path, check=True)
    assert subprocess.run([sys.executable, str(GUARD), "--staged"], cwd=tmp_path).returncode == 0


def test_manifest_requires_hash_and_origin(tmp_path):
    import hashlib
    import json
    data = b"\x89PNG\0independently generated signature"
    name = "tests/fixtures/synthetic.dat"
    assert run_guard(tmp_path, name, data) == 1
    manifest = {name: {"sha256": hashlib.sha256(data).hexdigest(),
                       "origin": "Independent literal bytes in test_manifest_requires_hash_and_origin"}}
    (tmp_path / "tests/synthetic_manifest.json").write_text(json.dumps(manifest))
    subprocess.run(["git", "add", "tests/synthetic_manifest.json"], cwd=tmp_path, check=True)
    assert subprocess.run([sys.executable, str(GUARD), "--staged"], cwd=tmp_path).returncode == 0
