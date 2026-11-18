"""Independent release geometry, immutable metadata and native identity contracts."""

import numpy as np
import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QLineEdit, QPlainTextEdit, QTabWidget
from scipy.optimize import minimize

from bruker_nmr.domain.document import Document, DocumentData
from bruker_nmr.domain.integrals import assign_reference
from bruker_nmr.export.current_pdf import SpectrumPage
from bruker_nmr.export.snapshot import ViewSnapshot
from bruker_nmr.processing.fid import Recipe, immutable
from bruker_nmr.ui.icons import NAMES, icon
from bruker_nmr.ui.loading import RawJob, discover_choices, load_choice
from tests.synthetic_bruker import experiment, status
from tests.test_viewer import window

CASES = {
    'isolated': [(6.1,6)],
    'separate': [(10,9.5),(6,5.5),(2,1.5)],
    'dense': [(4.3-i*.025,4.28-i*.025) for i in range(8)],
    'merging': [(4.3-i*.02,4.29-i*.02) for i in range(4)] +
               [(4.15-i*.02,4.14-i*.02) for i in range(4)],
    'left': [(11.999-i*.004,11.997-i*.004) for i in range(8)],
    'right': [(.032-i*.004,.030-i*.004) for i in range(8)],
    'widths': [(10,5),(3,2.9992)],
}


def geometry_snapshot(ranges):
    x = immutable(np.linspace(12,0,65536,endpoint=False))
    y = immutable(.01+sum(.02**2/((x-(high+low)/2)**2+.02**2) for high,low in ranges))
    doc = Document(DocumentData(x,y,'demo'))
    for high,low in ranges:
        doc.add_integral(high,low)
    region = doc.current.integrals.regions[0]
    doc.apply_integrals(assign_reference(doc.current.integrals, doc.current.grid,region.id,3))
    return ViewSnapshot(doc.current)


@pytest.mark.parametrize('name', CASES)
def test_orthogonal_cluster_layout_against_independent_optimizer(name, tmp_path):
    snapshot = geometry_snapshot(CASES[name])
    page = SpectrumPage(snapshot)
    try:
        page.preview_rgba()
        p = page.geometry.integral_placements
        ideal = np.array([v.label.center for v in p])
        actual = np.array([v.left+v.label.width/2 for v in p])
        left,_bottom,width,_height = page.geometry.plot
        oracle = minimize(lambda x: np.sum((x-ideal)**2), actual,
                          jac=lambda x:2*(x-ideal), method='SLSQP',
                          bounds=[(left+6.5,left+width-6.5)]*len(p),
                          constraints=[{'type':'ineq','fun':lambda x:np.diff(x)-16}],
                          options={'ftol':1e-7,'maxiter':200}) if len(p)>1 else None
        if oracle is not None:
            assert np.sum((actual-ideal)**2) <= oracle.fun+1e-3
        assert len({round(t.get_position()[1],9) for t in page.integral_texts}) == 1
        assert len({v[2] for v in page.integral_brackets}) == 1
        assert len(p) == len(CASES[name])
        for route in page.integral_routes:
            assert all(a[0]==b[0] or a[1]==b[1] for a,b in zip(route,route[1:]))
        # Independently rasterize real vector PDF and inspect PDF label baselines/geometry.
        import pymupdf
        with pymupdf.open(stream=page.pdf_bytes(),filetype='pdf') as pdf:
            pdf[0].get_pixmap().save(tmp_path/(name+'.png'))
            baselines = [span['bbox'][3] for block in pdf[0].get_text('dict')['blocks']
                         for row in block.get('lines',[]) if row['dir'][1] < -.9
                         for span in row['spans']]
            assert len(baselines) == len(p)
            assert max(baselines)-min(baselines) < .5
        # Isolated range remains centered; tight clusters necessarily have active spacing.
        if len(p)==1:
            assert actual[0] == pytest.approx(ideal[0])
        if name in ('dense','merging','left','right'):
            assert np.min(np.diff(actual)) == pytest.approx(16)
        assert snapshot.state is page.snapshot.state
    finally:
        page.close()


def test_complete_metadata_survives_processed_raw_and_cached_recipes(qtbot, tmp_path):
    exp,_,a,_ = experiment(tmp_path)
    a.update(NS=64, P=[1.,2.,3.], USERA1='Synthetic private field')
    a.pop('P')
    status(exp/'acqus', a)
    acquisition = exp/'acqus'
    acquisition.write_text(acquisition.read_text().replace('##END=', '##$P= (0..2)\n1.0 2.0 3.0\n##END='))
    choice,=discover_choices(exp)
    data=load_choice(exp, choice)
    rows=dict(data.source_parameters.acquisition)
    assert rows['P']==(1.,2.,3.) and rows['NS']==64
    with pytest.raises(TypeError):
        data.operator.provenance.acquisition['P'][0]=99
    assert 'Synthetic private field' not in repr(data.source_parameters)
    job=RawJob(None,exp,choice,Recipe(8))
    result=[]
    job.signals.finished.connect(lambda _j,_t,r,e:result.append((r,e)))
    job.run()
    raw,error=result.pop()
    assert error is None and raw.source_parameters==data.source_parameters
    assert raw.source_parameters.processing
    cached=RawJob(None,exp,choice,raw.operator.recipe,cached=(raw.operator.fid,raw.operator.acquisition),
                  source_parameters=raw.source_parameters)
    cached.signals.finished.connect(lambda _j,_t,r,e:result.append((r,e)))
    cached.run()
    assert result[0][1] is None and result[0][0].source_parameters==data.source_parameters
    ui=window(qtbot)
    ui.document=Document(data)
    ui._render_document()
    inspected=[]

    def inspect():
        d=QApplication.activeModalWidget()
        tabs=d.findChild(QTabWidget)
        assert tabs.count()==4
        tab=tabs.widget(1)
        field=tab.findChild(QLineEdit)
        field.setText('NS')
        assert 'NS = 64' in tab.findChild(QPlainTextEdit).toPlainText()
        field.setText('P')
        assert 'P = (1.0, 2.0, 3.0)' in tab.findChild(QPlainTextEdit).toPlainText()
        inspected.append(True)
        d.reject()

    QTimer.singleShot(20, inspect)
    ui.workbench.details()
    assert inspected == [True]
    assert not ui.windowIcon().isNull()
    assert all(not action.icon().isNull() for action in ui.workbench.bar.actions()
               if not action.isSeparator())
    assert len(NAMES)==17
    assert all(not icon(n).pixmap(20,20).isNull() for n in (*NAMES,'app'))
    ui.close()


def test_per_render_integral_validation_rejects_corrupted_geometry():
    page=SpectrumPage(geometry_snapshot(CASES['separate']))
    try:
        page.integral_texts[0].set_x(-1)
        with pytest.raises(ValueError,match='bounds'):
            page.preview_rgba()
    finally:
        page.close()


def test_tray_restores_window_and_quit_retains_unsaved_close_guard(qtbot, monkeypatch):
    from PySide6.QtWidgets import QSystemTrayIcon

    monkeypatch.setattr(QSystemTrayIcon, 'isSystemTrayAvailable', lambda: True)
    ui=window(qtbot)
    tray=ui.workbench.tray
    assert tray is not None and not tray.icon().isNull()
    ui.confirm_discard=lambda:False
    tray.contextMenu().actions()[1].trigger()
    assert ui.isVisible()
    ui.showMinimized()
    ui.workbench.tray_activated(QSystemTrayIcon.ActivationReason.Trigger)
    assert not ui.isMinimized() and ui.isVisible()
    ui.confirm_discard=lambda:True
    tray.hide()
    ui.close()
