"""Synthetic native markup round trips, recovery copies and normal Qt Save entry."""

import hashlib
import json
import zipfile

import pytest
from PySide6.QtWidgets import QFileDialog, QMessageBox

from bruker_nmr.domain.document import Document
from bruker_nmr.domain.integrals import integral_snapshot
from bruker_nmr.io.errors import InputError
from bruker_nmr.io.markup import EXTRA
from bruker_nmr.io.save_markup import save_markup
from bruker_nmr.io.source import Source
from bruker_nmr.ui.loading import discover_choices, load_choice
from tests.test_annotations import annotated
from tests.test_viewer import QueuedPool, window


def document(path):
    (choice,) = discover_choices(path)
    return Document(load_choice(path, choice))


def store(doc, target=None):
    path, choice = doc.source_location
    result = save_markup(path, choice.locator, doc.current, doc.markup_revision, target)
    doc.mark_saved(*result[:2])
    return result


def edit(doc):
    first, second, _ = doc.current.integrals.regions
    doc.delete_integral(first.id)
    doc.edit_integral(second.id, 10, 7)
    doc.assign_protons(second.id, 2.5)
    first, second = doc.current.peaks.records
    doc.delete_peak(first.id)
    doc.peak_label(second.id, False)
    doc.set_zero(second.id)


def meaning(doc):
    state = doc.current
    ranges = [
        (v.high, v.low, v.value, v.reference)
        for v in integral_snapshot(state.integrals, state.grid)
    ]
    peaks = [
        (p.ppm, p.intensity, p.type, p.label_visible, p.needs_review) for p in state.peaks.records
    ]
    return ranges, peaks, state.integrals.normalization.kind, state.calibration, state.referenced


def test_folder_save_selected_proc_native_exact_and_recovery(tmp_path):
    exp, proc, _ = annotated(tmp_path)
    original = {p.name: p.read_bytes() for p in proc.iterdir()}
    (proc / "peaks").write_bytes(b"legacy test peaks")
    other = exp / "pdata" / "2"
    other.mkdir()
    for name, blob in original.items():
        (other / name).write_bytes(blob)
    with Source(proc) as source:
        choice = next(c for c in source.discover() if c.processing == proc)
    from bruker_nmr.ui.loading import Choice

    doc = Document(load_choice(proc, Choice.from_candidate(choice, exp)))
    edit(doc)
    expected = meaning(doc)
    path, _, backup = store(doc)
    assert path == proc and not doc.integration_modified
    assert all((other / name).read_bytes() == blob for name, blob in original.items())
    assert (proc / "1r").read_bytes() == original["1r"]
    with zipfile.ZipFile(proc / backup) as archive:
        for name in ["intrng", "integrals.txt", "peaklist.xml", "procs"]:
            assert archive.read(name) == original[name]
        assert archive.read("peaks") == b"legacy test peaks"
        assert EXTRA in json.loads(archive.read("restore.json"))["absent"]
    assert not (proc / "peaks").exists()
    with Source(exp) as source:
        c = next(c for c in source.discover() if c.processing == proc)
        saved = source.read_processed(c.candidate_id)
    assert meaning(Document(type(doc.current.data).from_operator(saved, c))) == expected
    doc.undo()
    assert doc.integration_modified
    doc.redo()
    assert not doc.integration_modified


def test_zip_transfer_complete_and_second_save_persists_deletions(qtbot, tmp_path, monkeypatch):
    exp, proc, _ = annotated(tmp_path / "original")
    ancillary = exp / "auxiliary.txt"
    ancillary.write_text("independent untouched extra member")
    archive = tmp_path / "synthetic dataset.zip"
    with zipfile.ZipFile(archive, "w") as out:
        for f in exp.rglob("*"):
            if f.is_file():
                out.write(f, "dataset/1/" + f.relative_to(exp).as_posix())
    original = hashlib.sha256(archive.read_bytes()).digest()
    pool = QueuedPool()
    ui = window(qtbot, pool=pool)
    ui.begin_open(archive)
    pool.run_next()
    pool.run_next()
    edit(ui.document)
    expected = meaning(ui.document)
    parent = tmp_path / "destination"
    parent.mkdir()
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", lambda *a, **k: str(parent))
    monkeypatch.setattr(QMessageBox, "exec", lambda *a: None)
    ui.save_action.trigger()
    assert ui.saving
    pool.run_next()
    target = ui.document.source_location[0]
    assert target.parent == parent and target.is_dir()
    assert (target / "dataset/1/auxiliary.txt").read_bytes() == ancillary.read_bytes()
    assert (target / "dataset/1/pdata/1/1r").read_bytes() == (proc / "1r").read_bytes()
    assert meaning(document(target)) == expected and not ui.document.integration_modified
    ui.document.clear_peaks()
    ui.document.clear_integrals()
    ui.save_action.trigger()
    pool.run_next()
    assert ui.document.source_location[0] == target
    assert meaning(document(target)) == meaning(ui.document)
    assert not document(target).current.peaks.records
    assert not document(target).current.integrals.regions
    assert hashlib.sha256(archive.read_bytes()).digest() == original
    ui.close()


def test_frozen_scale_and_stale_metadata(tmp_path):
    exp, proc, _ = annotated(tmp_path)
    doc = document(exp)
    anchor = doc.current.integrals.regions[1]
    doc.assign_protons(anchor.id, 2)
    doc.delete_integral(anchor.id)
    store(doc)
    assert meaning(document(exp)) == meaning(doc)
    (proc / "peaklist.xml").write_text("<PeakList/>")
    with pytest.raises(InputError) as failure:
        store(doc)
    assert failure.value.code == "markup_changed_reopen"
    reopened = document(exp)
    assert not reopened.current.peaks.records
    assert "markup:stale_or_invalid" in reopened.current.data.annotations.issues


def test_mid_write_failure_restores_originals(tmp_path, monkeypatch):
    import os

    exp, proc, _ = annotated(tmp_path)
    doc = document(exp)
    original = {p.name: p.read_bytes() for p in proc.iterdir()}
    edit(doc)
    replace = os.replace
    failed = False

    def fail_once(src, dst):
        nonlocal failed
        if dst == proc / "peaklist.xml" and not failed:
            failed = True
            raise PermissionError
        return replace(src, dst)

    monkeypatch.setattr(os, "replace", fail_once)
    with pytest.raises(InputError) as failure:
        store(doc)
    assert failure.value.code == "markup_save_failed" and failed
    assert all((proc / name).read_bytes() == blob for name, blob in original.items())
    assert not (proc / EXTRA).exists() and not (proc / ".nmr-go-save.lock").exists()
    assert doc.integration_modified


def test_native_annotation_files_work_without_nmr_go_metadata(tmp_path):
    exp, proc, _ = annotated(tmp_path)
    doc = document(exp)
    edit(doc)
    store(doc)
    (proc / EXTRA).unlink()
    reopened = document(exp)
    old = doc.current
    new = reopened.current
    assert new.integrals.normalization.kind == "imported"
    assert [(r.left, r.right) for r in new.integrals.regions] == [
        (r.left, r.right) for r in old.integrals.regions
    ]
    assert [v.value for v in integral_snapshot(new.integrals, new.grid)] == pytest.approx(
        [v.value for v in integral_snapshot(old.integrals, old.grid)]
    )
    assert [p.ppm for p in new.peaks.records] == [p.ppm for p in old.peaks.records]


def test_refuses_save_for_changed_processing_grid(tmp_path):
    from bruker_nmr.processing.phase import Phase

    exp, _, _ = annotated(tmp_path)
    doc = document(exp)
    doc.apply_phase(Phase(10, 0, 0.5))
    assert not doc.can_save_markup
    with pytest.raises(InputError) as failure:
        store(doc)
    assert failure.value.code == "markup_processed_required"
    doc.undo()
    assert doc.can_save_markup
