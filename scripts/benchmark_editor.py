"""Synthetic-only Qt preview feedback benchmark; no file import or user inputs."""
import hashlib
import importlib.metadata
import json
import platform
import sys
import time
from pathlib import Path

import numpy as np
from PySide6.QtWidgets import QApplication

from bruker_nmr.domain.document import Document, DocumentData
from bruker_nmr.domain.integrals import Peak
from bruker_nmr.domain.peaks import PeakState
from bruker_nmr.processing.fid import immutable
from bruker_nmr.ui.main_window import ViewerWindow


def main(destination):
    app = QApplication.instance() or QApplication([])
    x = np.linspace(12, 0, 65536, endpoint=False)
    centers = np.array([1+9*(i//14)/10+.005*(i%14) for i in range(147)])
    real = .02+sum((.3+(i%7)/10)*.001/(.001**2+(x-c)**2)*.001
                   for i,c in enumerate(centers))
    data = DocumentData(immutable(x), immutable(real), 'demo')
    window = ViewerWindow({'version':'0.7.0', 'source_commit':'synthetic-benchmark'})
    window.document = Document(data)
    window.confirm_discard = lambda: True
    window.document.apply_peaks(PeakState(tuple(Peak(f'fake-{i}',float(c),1.,0,'manual')
                                                  for i,c in enumerate(centers))))
    for i in range(18):
        high = 11.4-i*.57
        window.document.add_integral(high,high-.4)
    window._render_document()
    window.show()
    window.integrals.show_table()
    app.processEvents()
    timings = []
    for i in range(105):
        start = time.perf_counter()
        delta = .01*np.sin(i*.1)
        window.view_box.setRange(xRange=(delta,12+delta),padding=0)
        window._draw_curve()
        window._redraw_timer.stop()
        app.processEvents()
        if i >= 5:
            timings.append((time.perf_counter()-start)*1000)
    root = Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for path in sorted((root/'src').rglob('*.py')):
        digest.update(path.relative_to(root).as_posix().encode())
        digest.update(path.read_bytes())
    result = {'scope':'Synthetic source Qt preview +native paint, not frozen or Windows11 user latency',
              'points':len(x),'integrals':18,'peaks':147,'warmup_frames':5,'frames':100,
              'median_ms':float(np.median(timings)),'p95_ms':float(np.percentile(timings,95)),
              'max_ms':max(timings),'python':platform.python_version(),'machine':platform.machine(),
              'platform':platform.system(),'source_tree_sha256':digest.hexdigest(),
              'versions':{p:importlib.metadata.version(p) for p in ('numpy','PySide6-Essentials','pyqtgraph')},
              'qt_platform':app.platformName(),'raw_milliseconds':timings}
    Path(destination).write_text(json.dumps(result,indent=2),encoding='utf-8')
    window.integrals.close()
    window.close()
    print(json.dumps({k:v for k,v in result.items() if k!='raw_milliseconds'}))


if __name__ == '__main__':
    main(sys.argv[1])
