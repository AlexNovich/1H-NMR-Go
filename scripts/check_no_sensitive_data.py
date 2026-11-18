"""Conservative index/tree guard. Diagnostics contain paths and rule names only."""
import argparse
import hashlib
import json
import re
import subprocess
from pathlib import PurePosixPath

# Owner-approved distribution assets, pinned to their reviewed bytes.
RELEASE_ASSETS = {
    "docs/example-workbench.png": "c57cc164859cf903f6fb6fd8284f118f063c0f4933c02419acb60b0f0d6ed1f4",
    "docs/example-spectrum.png": "aad94dd1b9fe1ce06d6901a0526d4ec4795ab383adf1e876bbbb17127a9f5407",
    "examples/bruker-1h-example.zip": "e60061fedb7715660b188ed4c7e989434ffdeb839892db7b59312afcbf04e062",
}


def git(*args):
    return subprocess.check_output(["git", *args])


def inspect(path, data, mode, manifest):
    if path in RELEASE_ASSETS:
        if mode == "100644" and hashlib.sha256(data).hexdigest() == RELEASE_ASSETS[path]:
            return None
        return "release-asset-digest-mismatch"
    p = PurePosixPath(path)
    name = p.name.lower()
    if path in {
        "01_Product_Requirements.md", "02_Preliminary_Technical_Proposal.md",
        "03_Agent_Planning_Brief.md", "04_Local_Inspection_and_Validation.md",
        "docs/planning/ARGON_PLAN.md", "docs/planning/LOCAL_DATA_SURVEY.md",
        "docs/planning/EVIDENCE_REVIEW.md",
    }:
        return "local-only-report"
    if p.suffix.lower() in {".pem", ".key"} or re.search(
            r"(?:credentials|token).*\.json$", name):
        return "credential-file"
    if mode not in ("100644", "100755"):
        return "non-regular-file"
    if len(data) > 1_000_000:
        return "size-limit"
    if any(part.lower() in {"local_data", "reports", ".aws", ".codex", ".agents"}
           for part in p.parts):
        return "local-only-path"
    if name.startswith((".env", ".nmr-go-backup-")) or any(
            part.startswith(".nmr-go-write-") for part in p.parts) or name in {
        "fid", "ser", "1r", "1i", "2rr", "title", "intrng", "integrals.txt",
        "peaklist.xml", "peakrng", "peaks", "audita.txt", "auditp.txt",
        "nmr-go-markup.json", ".nmr-go-save.lock",
    } or name.startswith(("acqu", "procs")) or name == "proc":
        return "sensitive-name"
    secrets = [rb"(?:gh[pousr]_)[A-Za-z0-9_]{20,}", rb"github_pat_[A-Za-z0-9_]{20,}",
               rb"AKIA[A-Z0-9]{16}", rb"-----BEGIN [A-Z ]*PRIVATE KEY-----",
               rb"(?im)^\s*(?:password|api_key|access_token)\s*[:=]\s*['\"]?[^\s'\"]{8,}"]
    if any(re.search(pattern, data) for pattern in secrets):
        return "secret-pattern"
    if re.search(rb"(?m)^##(?:TITLE|JCAMPDX|\$TD|\$NUC1)\s*=", data):
        return "bruker-content"
    signatures = (b"PK\x03\x04", b"Rar!", b"7z\xbc\xaf", b"%PDF-", b"\x89PNG", b"\xff\xd8\xff", b"GIF8")
    binary = (data.startswith(signatures) or b"\0" in data or
              p.suffix.lower() in {".zip", ".rar", ".7z", ".pdf", ".png", ".jpg", ".jpeg", ".npy", ".npz", ".tif", ".tiff"})
    if binary:
        entry = manifest.get(path, {})
        if (not entry.get("origin") or entry.get("sha256") != hashlib.sha256(data).hexdigest()
                or not path.startswith("tests/fixtures/")):
            return "binary-without-reviewed-manifest"
    else:
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            return "non-utf8"
    return None


def main():
    parser = argparse.ArgumentParser()
    modes = parser.add_mutually_exclusive_group(required=True)
    modes.add_argument("--staged", action="store_true")
    modes.add_argument("--tracked", action="store_true")
    parser.parse_args()
    # Both modes read the index, never worktree bytes. CI's checkout index is the tracked tree.
    records = git("ls-files", "--stage", "-z").split(b"\0")
    entries = []
    for record in filter(None, records):
        metadata, raw_path = record.split(b"\t", 1)
        mode, oid, stage = metadata.decode().split()
        entries.append((raw_path.decode("utf-8"), mode, oid, stage))
    manifest = {}
    for path, mode, oid, stage in entries:
        if path == "tests/synthetic_manifest.json":
            manifest = json.loads(git("cat-file", "blob", oid))
    failures = 0
    for path, mode, oid, stage in entries:
        reason = "unmerged-index" if stage != "0" else inspect(
            path, git("cat-file", "blob", oid), mode, manifest)
        if reason:
            print(f"{path!r}: {reason}")
            failures += 1
    print(f"Data guard: {len(entries)} files, {failures} violations")
    return bool(failures)


if __name__ == "__main__":
    raise SystemExit(main())
