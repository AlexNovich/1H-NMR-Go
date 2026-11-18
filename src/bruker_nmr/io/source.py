"""Bounded, immutable-source folder/ZIP access; no network or persistent extraction."""
import os
import re
import stat
import struct
import tempfile
import unicodedata
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from bruker_nmr.io.errors import InputError


@dataclass(frozen=True)
class Limits:
    max_entries: int = 4096
    max_depth: int = 8
    max_total_bytes: int = 512 * 1024 * 1024
    max_file_bytes: int = 128 * 1024 * 1024
    max_ratio: float = 200
    max_candidates: int = 128
    max_points: int = 4 * 1024 * 1024


def _reparse(attributes) -> bool:
    # Python 3.11 Path.is_symlink alone does not cover Windows directory junctions.
    return bool(getattr(attributes, "st_file_attributes", 0) & 0x400)


def _is_link(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        return _reparse(path.lstat())
    except FileNotFoundError:
        return False


def _member_path(name: str, limits: Limits) -> PurePosixPath:
    if not name or "\\" in name or "\0" in name or name.startswith("/"):
        raise InputError("unsafe_archive_path")
    parts = name.rstrip("/").split("/")
    if len(parts) > limits.max_depth:
        raise InputError("source_depth_limit")
    for part in parts:
        if (part in {"", ".", ".."} or any(c in part for c in ":*?\"<>|")
                or any(ord(c) < 32 for c in part) or part.endswith((" ", "."))
                or re.match(r"^(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)", part, re.I)):
            raise InputError("unsafe_archive_path")
    return PurePosixPath(*parts)


def _extract(archive: Path, destination: Path, limits: Limits):
    # Bound central-directory allocation before ZipFile constructs every ZipInfo.
    size = archive.stat().st_size
    if size > limits.max_total_bytes:
        raise InputError("source_byte_limit")
    with archive.open("rb") as stream:
        stream.seek(max(0, size - 65557))
        tail = stream.read()
    end = tail.rfind(b"PK\x05\x06")
    if end < 0 or len(tail) - end < 22:
        raise InputError("invalid_archive_directory")
    _, disk, directory_disk, on_disk, entries, directory_size, offset, comment = struct.unpack(
        "<4s4H2LH", tail[end:end + 22])
    if (disk or directory_disk or on_disk != entries or entries == 65535
            or len(tail) - end != 22 + comment or offset + directory_size != size - len(tail) + end):
        raise InputError("unsupported_archive_layout")
    if entries > limits.max_entries or directory_size > 16 * 1024 * 1024:
        raise InputError("source_entry_limit")
    with zipfile.ZipFile(archive) as source:
        infos = source.infolist()
        if len(infos) > limits.max_entries:
            raise InputError("source_entry_limit")
        seen = {}
        entries = []
        total = 0
        for info in infos:
            # ZipInfo normalizes backslashes on Windows and truncates NULs. Validate
            # the original central-directory name before trusting its normalized form.
            path = _member_path(info.orig_filename, limits)
            key = unicodedata.normalize("NFC", path.as_posix()).casefold()
            if key in seen:
                raise InputError("duplicate_archive_path")
            seen[key] = info.is_dir()
            filetype = stat.S_IFMT(info.external_attr >> 16)
            if filetype not in {0, stat.S_IFREG, stat.S_IFDIR}:
                raise InputError("archive_link_or_special_file")
            if info.flag_bits & 1:
                raise InputError("encrypted_archive_member")
            if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
                raise InputError("unsupported_archive_compression")
            if path.suffix.lower() in {".zip", ".rar", ".7z"}:
                raise InputError("nested_archive")
            total += info.file_size
            if info.file_size > limits.max_file_bytes or total > limits.max_total_bytes:
                raise InputError("source_byte_limit")
            if info.file_size / max(1, info.compress_size) > limits.max_ratio:
                raise InputError("archive_ratio_limit")
            entries.append((info, path))
        for key in seen:
            if any(seen.get(str(parent)) is False for parent in PurePosixPath(key).parents):
                raise InputError("archive_file_directory_collision")
        actual_total = 0
        for info, path in entries:
            target = destination.joinpath(*path.parts)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            actual = 0
            with source.open(info) as stream, target.open("xb") as output:
                while chunk := stream.read(64 * 1024):
                    if actual == 0 and chunk.startswith((b"PK\x03\x04", b"Rar!", b"7z\xbc\xaf")):
                        raise InputError("nested_archive")
                    actual += len(chunk)
                    actual_total += len(chunk)
                    if actual > limits.max_file_bytes or actual_total > limits.max_total_bytes:
                        raise InputError("source_byte_limit")
                    output.write(chunk)
            if actual != info.file_size:
                raise InputError("archive_member_size_mismatch")


def _walk(root: Path, limits: Limits) -> tuple[Path, ...]:
    files = []
    seen = set()
    count = total = 0
    stack = [(root, 0)]
    while stack:
        directory, depth = stack.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                count += 1
                if count > limits.max_entries:
                    raise InputError("source_entry_limit")
                relative = Path(entry.path).relative_to(root)
                if len(relative.parts) > limits.max_depth:
                    raise InputError("source_depth_limit")
                key = unicodedata.normalize("NFC", relative.as_posix()).casefold()
                if key in seen:
                    raise InputError("duplicate_source_path")
                seen.add(key)
                if entry.is_symlink() or _reparse(entry.stat(follow_symlinks=False)):
                    raise InputError("source_symlink")
                if entry.is_dir(follow_symlinks=False):
                    if depth + 1 > limits.max_depth:
                        raise InputError("source_depth_limit")
                    stack.append((Path(entry.path), depth + 1))
                elif entry.is_file(follow_symlinks=False):
                    size = entry.stat(follow_symlinks=False).st_size
                    total += size
                    if size > limits.max_file_bytes or total > limits.max_total_bytes:
                        raise InputError("source_byte_limit")
                    files.append(Path(entry.path))
                else:
                    raise InputError("source_special_file")
    return tuple(sorted(files))


class Source:
    """Keep a ZIP's managed temporary directory alive until the context closes."""
    def __init__(self, path: Path | str, limits: Limits | None = None):
        self.original = Path(path).absolute()
        self.limits = limits or Limits()
        self.root = self.original
        self.files = ()
        self._temporary = None
        self._active = False
        self._candidates = None

    def __enter__(self):
        try:
            if any(_is_link(p) for p in (self.original, *self.original.parents)):
                raise InputError("source_symlink")
            if self.original.is_file():
                if self.original.suffix.lower() != ".zip":
                    raise InputError("zip_or_folder_required")
                self._temporary = tempfile.TemporaryDirectory(prefix="nmr-go-import-")
                self.root = Path(self._temporary.name)
                _extract(self.original, self.root, self.limits)
            elif not self.original.is_dir():
                raise InputError("source_not_found")
            elif self.root.parent.name.lower() == "pdata":
                self.root = self.root.parent.parent
            elif self.root.name.lower() == "pdata":
                self.root = self.root.parent
            self.files = _walk(self.root, self.limits)
            self._active = True
            return self
        except InputError:
            self.close()
            raise
        except (OSError, RuntimeError, zipfile.BadZipFile, zlib.error, EOFError, ValueError):
            self.close()
            raise InputError("source_read_failed") from None

    def close(self):
        self._active = False
        self._candidates = None
        self.files = ()
        if self._temporary:
            try:
                self._temporary.cleanup()
            except OSError:
                raise InputError("temporary_cleanup_failed") from None
            self._temporary = None

    def __exit__(self, *exc):
        self.close()

    def discover(self):
        if not self._active:
            raise InputError("source_closed")
        from bruker_nmr.io.reader import discover
        if self._candidates is None:
            try:
                self._candidates = discover(self)
            except InputError:
                raise
            except (OSError, ValueError, Warning):
                raise InputError("source_read_failed") from None
        return self._candidates

    def read_processed(self, candidate_id: str | None = None):
        from bruker_nmr.io.reader import read_processed
        records = self.discover()
        if candidate_id is None:
            if len(records) != 1:
                raise InputError("explicit_selection_required")
            candidate_id = records[0].candidate_id
        candidate = next((c for c in records if c.candidate_id == candidate_id), None)
        if candidate is None:
            raise InputError("unknown_selection")
        return read_processed(self, candidate)
