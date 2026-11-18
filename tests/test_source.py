"""Archive/folder adversarial checks use independently generated temporary inputs."""
import stat
import struct
import zipfile
from pathlib import Path

import numpy as np
import pytest

from bruker_nmr.io.errors import InputError
from bruker_nmr.io.source import Limits, Source
from tests.synthetic_bruker import experiment


def archive(path, entries):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as output:
        for name, content in entries:
            if isinstance(name, str):
                # Preserve intentionally malformed ZIP names on Windows too.
                info = zipfile.ZipInfo()
                info.filename = info.orig_filename = name
                name = info
            output.writestr(name, content)
    return path


def test_archive_folder_equality_and_cleanup(tmp_path):
    exp, _, _, _ = experiment(tmp_path / "folder")
    path = tmp_path / "synthetic.zip"
    archive(path, [(p.relative_to(exp.parent).as_posix(), p.read_bytes())
                   for p in exp.rglob("*") if p.is_file()])
    original = path.read_bytes()
    with Source(exp) as folder, Source(path) as zipped:
        root = zipped.root
        left = folder.read_processed()
        right = zipped.read_processed()
        assert np.array_equal(left.real, right.real)
        assert np.array_equal(left.ppm, right.ppm)
    assert not root.exists()
    assert path.read_bytes() == original


@pytest.mark.parametrize("name", ["../escape", "/absolute", "C:/drive", "//server/a",
                                      "a\\b", "a/./b", "a//b", "AUX.txt", "a:stream",
                                      "a./b", "a /b"])
def test_bad_archive_paths(tmp_path, name):
    path = archive(tmp_path / "bad.zip", [(name, b"independent")])
    with zipfile.ZipFile(path) as check:
        assert check.infolist()[0].orig_filename == name
    with pytest.raises(InputError):
        with Source(path):
            pass
    assert not (tmp_path / "escape").exists()


def test_archive_original_path_checked_before_windows_normalization(tmp_path, monkeypatch):
    path = archive(tmp_path / "bad.zip", [("a\\b", b"independent")])
    original_init = zipfile.ZipInfo.__init__

    def windows_init(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        self.filename = self.filename.replace("\\", "/")

    monkeypatch.setattr(zipfile.ZipInfo, "__init__", windows_init)
    with pytest.raises(InputError, match="Unsafe archive path"):
        with Source(path):
            pass


@pytest.mark.parametrize("entries", [
    [("A/file", b"x"), ("a/FILE", b"y")],
    [("one", b"x"), ("one/two", b"y")],
    [("nested.zip", b"x")], [("hidden.dat", b"PK\x03\x04independent")],
])
def test_archive_collisions_and_nested(tmp_path, entries):
    path = archive(tmp_path / "bad.zip", entries)
    with pytest.raises(InputError):
        with Source(path):
            pass


def test_archive_symlink(tmp_path):
    info = zipfile.ZipInfo("link")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    path = archive(tmp_path / "link.zip", [(info, b"../outside")])
    with pytest.raises(InputError, match="Archive link"):
        with Source(path):
            pass


@pytest.mark.parametrize("corruption", ["crc", "encrypted", "truncated"])
def test_corrupt_archive(tmp_path, corruption):
    path = archive(tmp_path / "bad.zip", [("test.dat", b"independently_generated_data")])
    data = bytearray(path.read_bytes())
    if corruption == "crc":
        data[data.index(b"independently_generated_data")] ^= 1
    elif corruption == "encrypted":
        central = data.index(b"PK\x01\x02")
        struct.pack_into("<H", data, 6, 1)
        struct.pack_into("<H", data, central + 8, 1)
    else:
        del data[-50:]
    path.write_bytes(data)
    with pytest.raises(InputError):
        with Source(path):
            pass


@pytest.mark.parametrize("limits", [Limits(max_file_bytes=4), Limits(max_total_bytes=4),
                                      Limits(max_entries=1), Limits(max_depth=1)])
def test_archive_and_folder_limits(tmp_path, limits):
    folder = tmp_path / "folder"
    (folder / "a").mkdir(parents=True)
    (folder / "a/test").write_bytes(b"independent")
    (folder / "a/other").write_bytes(b"data")
    path = archive(tmp_path / "bad.zip", [("a/test", b"independent"), ("a/other", b"data")])
    for location in [path, folder]:
        with pytest.raises(InputError):
            with Source(location, limits):
                pass


def test_archive_compression_ratio(tmp_path):
    path = tmp_path / "ratio.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as output:
        output.writestr("zeroes.dat", bytes(100000))
    with pytest.raises(InputError, match="Archive ratio limit"):
        with Source(path):
            pass


def test_folder_symlink_and_missing_source(tmp_path):
    # No privileged Windows symlink requirement: test source links where permitted.
    missing = tmp_path / "missing"
    with pytest.raises(InputError, match="Source not found"):
        with Source(missing):
            pass
    link = tmp_path / "link"
    try:
        link.symlink_to(tmp_path, target_is_directory=True)
    except OSError:
        pytest.skip("Creating a folder symlink requires additional Windows privileges")
    with pytest.raises(InputError, match="Source symlink"):
        with Source(tmp_path):
            pass


def test_source_closed_and_unknown_format(tmp_path):
    source = Source(tmp_path)
    with pytest.raises(InputError, match="Source closed"):
        source.discover()
    path = tmp_path / "unsupported.rar"
    path.write_bytes(b"independent")
    with pytest.raises(InputError, match="Zip or folder required"):
        with Source(path):
            pass


def test_metadata_permission_failure_sanitized(tmp_path, monkeypatch):
    exp, _, _, _ = experiment(tmp_path)
    with Source(exp) as source:
        def denied(*args):
            raise PermissionError("Confidential path must not escape in an error")
        monkeypatch.setattr("bruker_nmr.io.status.Path.read_bytes", denied)
        candidate, = source.discover()
        assert not candidate.processed_available
        assert "status_read_failed" in candidate.issues


def test_archive_cleanup_on_read_error(tmp_path, monkeypatch):
    path = archive(tmp_path / "bad.zip", [("one.dat", b"independent")])
    source = Source(path)
    def denied(*args, **kwargs):
        raise PermissionError("Confidential source path")
    monkeypatch.setattr("bruker_nmr.io.source.zipfile.ZipFile.open", denied)
    with pytest.raises(InputError) as error:
        with source:
            pass
    assert not source.root.exists()
    assert "Confidential" not in str(error.value)


def test_corrupt_deflate_is_sanitized_and_temp_removed(tmp_path):
    path = tmp_path / "corrupt-deflate.zip"
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as output:
        output.writestr("payload.dat", b"independent data " * 100)
    payload = bytearray(path.read_bytes())
    name_length, extra_length = struct.unpack_from("<HH", payload, 26)
    payload[30 + name_length + extra_length] = 0xff  # Invalid DEFLATE block type.
    path.write_bytes(payload)
    source = Source(path)
    with pytest.raises(InputError):
        with source:
            pass
    assert not source.root.exists()


def test_windows_reparse_attribute_rejected(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from bruker_nmr.io.source import _is_link, _reparse
    assert _reparse(SimpleNamespace(st_file_attributes=0x400))
    assert not _reparse(SimpleNamespace(st_file_attributes=0x20))
    monkeypatch.setattr(Path, "lstat", lambda self: SimpleNamespace(st_file_attributes=0x400))
    monkeypatch.setattr(Path, "is_symlink", lambda self: False)
    assert _is_link(tmp_path)
    with pytest.raises(InputError, match="Source symlink"):
        with Source(tmp_path):
            pass
