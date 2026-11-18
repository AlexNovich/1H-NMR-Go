"""Analytic absolute arbitrary-unit truth and raw/import lifecycle checks."""

import threading

import numpy as np
import pytest

from bruker_nmr.domain.document import Document, DocumentData
from bruker_nmr.io.errors import InputError
from bruker_nmr.io.fid import read_fid
from bruker_nmr.io.source import Source
from bruker_nmr.processing.fid import Recipe, process
from tests.synthetic_bruker import experiment, status
from tests.test_viewer import window


def test_raw_worker_cancel_reprocessing_and_modal_options(qtbot, monkeypatch, tmp_path):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication

    from tests.test_viewer import QueuedPool
    exp, _, _, _ = experiment(tmp_path)
    pool = QueuedPool()
    ui = window(qtbot, pool=pool)
    QTimer.singleShot(20, lambda: QApplication.activeModalWidget().accept())
    default = ui.processing_options()
    assert default == Recipe(0, .3)
    QTimer.singleShot(20, lambda: QApplication.activeModalWidget().reject())
    assert ui.processing_options() is None
    monkeypatch.setattr(ui, 'processing_options', lambda: Recipe(8))
    monkeypatch.setattr(ui, 'confirm_discard', lambda: True)
    ui.begin_open(exp)
    pool.run_next()
    pool.run_next()
    original = ui.document.current
    ui.process_from_fid()
    ui.cancel_loading()
    pool.run_next()
    assert ui.document.current == original
    ui.process_from_fid()
    pool.run_next()
    first_raw = ui.document.current.data
    monkeypatch.setattr(ui, 'processing_options', lambda: Recipe(16, .6))
    ui.process_from_fid()
    pool.run_next()
    assert ui.document.current.data.operator.fid is first_raw.operator.fid
    assert ui.document.current.data.operator.recipe == Recipe(16, .6)
    ui.reset()
    assert ui.document.current.data is first_raw
    ui.close()


def parameters(n=32, delay=0):
    return dict(SW_h=3200.0, BF1=300.0, O1=600.0, GRPDLY=delay, NC=0, TD=2 * n)


@pytest.mark.parametrize("delay", [0, 3, 3.375])
def test_analytic_orientation_absolute_units_and_delay_once(delay):
    n, m = 32, 5
    k = n // 2 - m
    phase = 2 * np.pi * delay * k / n
    fid = 7 * np.exp(2j * np.pi * m * np.arange(n) / n + 1j * phase)
    result = process(fid, parameters(delay=delay), Recipe(n, 0))
    spectrum = result.real + 1j * result.imaginary
    assert np.argmax(abs(spectrum)) == k
    assert spectrum[k] == pytest.approx(7, abs=1e-12)
    assert result.ppm[k] == pytest.approx((600 + m * 100) / 300)
    assert not result.real.flags.writeable and not result.fid.flags.writeable
    assert result.annotations.integrals.normalization.kind == "absent"


def test_em_direct_dft_and_known_phase_explicit():
    fid = np.array([1 + 2j, 3 - 4j, -2 + 1j, 5 + 3j])
    n, lb = 8, 0.5
    p = parameters(4)
    windowed = [v * np.exp(-np.pi * lb * j / p["SW_h"]) for j, v in enumerate(fid)]
    truth = [
        sum(v * np.exp(2j * np.pi * j * k / n) for j, v in enumerate(windowed)) / n
        for k in range(n)
    ]
    truth = np.conj(np.array(truth)[[4, 5, 6, 7, 0, 1, 2, 3]])
    r = process(fid, p, Recipe(n, lb))
    assert np.allclose(r.real + 1j * r.imaginary, truth, atol=1e-14)
    with pytest.raises(InputError):
        process(fid, p, Recipe(n, lb, phase0=30))
    rotated = process(fid, p, Recipe(n, lb, phase0=30, known_phase=True))
    assert np.allclose(rotated.real + 1j * rotated.imaginary, truth * np.exp(1j * np.pi / 6))


@pytest.mark.parametrize("dtype", [0, 2])
@pytest.mark.parametrize("order", [0, 1])
def test_raw_status_binary_padding_and_missing_pdata(tmp_path, dtype, order):
    exp, proc, a, p = experiment(tmp_path, dtype=dtype, endian=order, padded=True)
    (proc / "procs").unlink()
    with Source(exp) as source:
        (choice,) = source.discover()
        assert choice.raw_available and not choice.processed_available
        fid, params = read_fid(source, choice)
        assert np.array_equal(fid, [2 - 4j, 6 + 8j, 10 - 12j, 14 + 16j])
        r = process(fid, params, Recipe(8))
        assert len(r.real) == 8
    a["GRPDLY"] = -1
    status(exp / "acqus", a)
    with Source(exp) as source:
        (choice,) = source.discover()
        assert not choice.raw_available


def test_invalid_recipe_cancellation_and_memory_state(tmp_path):
    exp, _, _, _ = experiment(tmp_path)
    with Source(exp) as source:
        (c,) = source.discover()
        fid, p = read_fid(source, c)
        operator = source.read_processed()
    d = Document(DocumentData.from_operator(operator, c))
    original = d.current
    raw = process(fid, p, Recipe(8))
    data = DocumentData(raw.ppm, raw.real, "raw", raw, True)
    d.apply_raw(data)
    assert not d.current.integrals.regions
    d.add_integral(data.ppm[0], data.ppm[3])
    d.reset()
    assert d.current.data is data and not d.current.integrals.regions
    while d.current.data is data:
        d.undo()
    assert d.current == original
    d.redo()
    for recipe in [Recipe(3), Recipe(2), Recipe(2**23), Recipe(8, -1), Recipe(8, float("nan"))]:
        with pytest.raises(InputError):
            process(fid, p, recipe)
    stop = threading.Event()
    stop.set()
    with pytest.raises(InputError):
        process(fid, p, Recipe(8), stop)


def test_gui_raw_only_and_processed_transition(qtbot, monkeypatch, tmp_path):
    exp, proc, _, _ = experiment(tmp_path)
    ui = window(qtbot)
    monkeypatch.setattr(ui, "confirm_discard", lambda: True)
    monkeypatch.setattr(ui, "processing_options", lambda: Recipe(8))
    ui.begin_open(exp)
    qtbot.waitUntil(lambda: not ui.loading, timeout=15000)
    original = ui.document.current
    ui.process_from_fid()
    qtbot.waitUntil(lambda: not ui.loading, timeout=15000)
    assert ui.document.current.data.opening_mode == "raw"
    assert "unphased" in ui.summary.text()
    ui.undo()
    assert ui.document.current == original
    (proc / "procs").unlink()
    ui.begin_open(exp)
    qtbot.waitUntil(lambda: not ui.loading, timeout=15000)
    assert ui.document.opening.data.opening_mode == "raw"
    ui.close()


@pytest.mark.parametrize('points, expected, automatic', [
    (1, (2, 4, 8), 2),
    (3, (4, 8, 16), 8),
    (65536, (65536, 131072, 262144), 131072),
    (65537, (131072, 262144, 524288), 262144),
    (2097153, (4194304,), 4194304),
    (4194304, (4194304,), 4194304),
])
def test_si_menu_sizes_and_automatic_resource_boundary(points, expected, automatic):
    from bruker_nmr.processing.fid import automatic_si, si_choices

    assert si_choices(points) == expected
    assert automatic_si(points) == automatic


@pytest.mark.parametrize('points', [0, -1, True, 3.5, None, 4194305])
def test_si_menu_rejects_missing_invalid_or_oversize_lengths(points):
    from bruker_nmr.processing.fid import automatic_si, si_choices

    assert si_choices(points) == ()
    with pytest.raises(InputError) as failure:
        automatic_si(points)
    assert failure.value.code == 'unsupported_zero_fill_size'


def test_real_si_dropdown_processed_raw_only_and_current_recipe(qtbot, monkeypatch, tmp_path):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QComboBox

    from bruker_nmr.ui.loading import discover_choices

    exp, proc, _, _ = experiment(tmp_path)
    ui = window(qtbot)
    monkeypatch.setattr(ui, 'confirm_discard', lambda: True)
    ui.begin_open(exp)
    qtbot.waitUntil(lambda: not ui.loading, timeout=15000)
    assert ui.document.current.data.raw_source[1].raw_points == 4
    menus = []

    def choose(value):
        dialog = QApplication.activeModalWidget()
        combo = dialog.findChild(QComboBox, 'fid_si')
        assert not combo.isEditable()
        values = [combo.itemData(i) for i in range(combo.count())]
        menus.append(values)
        assert combo.itemText(0) == 'Automatic (8 points)'
        assert combo.findData(2) == -1
        combo.setCurrentIndex(combo.findData(value))
        dialog.accept()

    QTimer.singleShot(20, lambda: choose(4))
    ui.process_from_fid()
    qtbot.waitUntil(lambda: not ui.loading, timeout=15000)
    assert ui.document.current.data.operator.recipe.si == 4
    assert menus == [[0, 4, 8, 16]]
    # Preserve a valid existing larger recipe without offering other excessive sizes.
    data = ui.document.current.data
    from dataclasses import replace

    result = process(data.operator.fid, data.operator.acquisition, Recipe(64))
    ui.document.apply_raw(replace(data, ppm=result.ppm, real=result.real, operator=result))
    QTimer.singleShot(20, lambda: choose(64))
    assert ui.processing_options().si == 64
    assert menus[-1] == [0, 4, 8, 16, 64]
    (proc / 'procs').unlink()
    assert discover_choices(exp)[0].raw_points == 4
    show_options = ui.processing_options

    def choose_after_discovery():
        QTimer.singleShot(0, lambda: choose(0))
        return show_options()

    # Raw-only opening discovers the experiment asynchronously before showing SI.
    with monkeypatch.context() as context:
        context.setattr(ui, 'processing_options', choose_after_discovery)
        ui.begin_open(exp)
        qtbot.waitUntil(lambda: not ui.loading, timeout=15000)
    assert ui.document.current.data.operator.recipe.si == 8
    assert menus[-1] == [0, 4, 8, 16]
    assert ui._pending_raw_choice is None
    from PySide6.QtWidgets import QDialogButtonBox

    ui._pending_raw_choice = replace(discover_choices(exp)[0], raw_points=4194305)

    def reject_oversize():
        dialog = QApplication.activeModalWidget()
        assert dialog.findChild(QComboBox, 'fid_si').count() == 0
        buttons = dialog.findChild(QDialogButtonBox)
        assert not buttons.button(QDialogButtonBox.StandardButton.Ok).isEnabled()
        dialog.reject()

    QTimer.singleShot(20, reject_oversize)
    assert ui.processing_options() is None
    ui._pending_raw_choice = None
    ui.close()
