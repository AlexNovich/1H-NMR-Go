"""Generate an independent processed input ZIP for offline viewer acceptance."""
import math
import zipfile
from pathlib import Path

import numpy as np

from bruker_nmr.demo import make_demo


def write_demo_input(destination: Path):
    demo = make_demo()
    size = len(demo.ppm)
    # Match the independently specified demo grid's endpoints to a processed SI grid.
    acquisition = {"NUC1": "1H", "PARMODE": 0, "SFO1": 300, "SW_h": 3900,
                   "TD": size * 2, "DTYPA": 2, "BYTORDA": 0, "AQ_mod": 3, "GRPDLY": 0,
                   "BF1": 300, "O1": 1650, "NC": 0}
    processed = {"PPARMOD": 0, "SI": size, "DTYPP": 2, "BYTORDP": 0, "NC_proc": 0,
                 "OFFSET": 12, "SW_p": 13 * 300 * size / (size - 1), "SF": 300,
                 "STSR": 0, "STSI": size, "XDIM": 0, "FT_mod": 6}
    step = 13 / (size - 1)
    regions = [(7.6, 6.8), (4., 3.2), (1.6, .8)]
    raw = demo.amplitude[::-1]
    sums = []
    for high, low in regions:
        left, right = (int(np.rint((12 - v) / step)) for v in (high, low))
        sums.append(math.fsum(float(v) for v in raw[left:right]))
    # An arbitrary synthetic 2-H scale is declared; no imported reference ID is encoded.
    processed["INTSCL"] = 2 / sums[0]
    with zipfile.ZipFile(destination, "x", zipfile.ZIP_DEFLATED) as output:
        for name, params in [("synthetic/1/acqus", acquisition),
                             ("synthetic/1/pdata/1/procs", processed)]:
            lines = ["##TITLE= Independently generated NMR-go viewer input"]
            for key, value in params.items():
                rendered = f"<{value}>" if isinstance(value, str) else str(value)
                lines.append(f"##${key}= {rendered}")
            output.writestr(name, "\n".join(lines + ["##END="]) + "\n")
        output.writestr("synthetic/1/pdata/1/1r", np.asarray(demo.amplitude[::-1], dtype="<f8").tobytes())
        # Separate artificial FID, not reconstructed from processed or confidential data.
        t = np.arange(size)/3900
        fid = sum(height*np.exp(-np.pi*2*width*300*t)*np.exp(2j*np.pi*(center-5.5)*300*t)
                  for center,height,width in [(1.2,.65,.025),(3.6,1.,.035),(7.1,.8,.020),(7.3,.55,.020)])
        paired = np.empty(size*2, dtype='<f8')
        paired[::2], paired[1::2] = fid.real, fid.imag
        output.writestr('synthetic/1/fid', paired.tobytes())
        output.writestr("synthetic/1/pdata/1/intrng", "1D 3 #regions in PPM\n" +
                        "".join(f"{high} {low} 0 0\n" for high, low in regions))
        output.writestr("synthetic/1/pdata/1/integrals.txt", "# Number Region Integral\n" +
                        "".join(f"{i+1} {high:.5f} {low:.5f} {s*processed['INTSCL']:.5f}\n"
                                for i, ((high, low), s) in enumerate(zip(regions, sums))))
        output.writestr("synthetic/1/pdata/1/peaklist.xml", "<PeakList><PeakList1D>" +
                        "".join(f'<Peak1D F1="{ppm}" intensity="{height}" type="0"/>'
                                for ppm, height in [(1.2, .65), (3.6, 1.), (7.1, .8), (7.3, .55)]) +
                        "</PeakList1D></PeakList>")
