"""Generate Bruker fixtures independently, only in caller-selected temporary folders."""
import struct
from pathlib import Path

VALUES = (2, -4, 6, 8, 10, -12, 14, 16)


def status(path: Path, params: dict, *, encoding="utf-8"):
    lines = ["##TITLE= Independent synthetic generator", "##JCAMPDX= 5.00"]
    for key, value in params.items():
        rendered = f"<{value}>" if isinstance(value, str) else str(value)
        lines.append(f"##${key}= {rendered}")
    lines.append("##END=")
    path.write_text("\n".join(lines) + "\n", encoding=encoding)


def experiment(root, *, dtype=0, endian=0, scale=2, imaginary=True, padded=False):
    exp = Path(root) / "independent-fixture" / "1"
    proc = exp / "pdata" / "1"
    proc.mkdir(parents=True)
    acquisition = dict(NUC1="1H", PARMODE=0, SFO1=300.0, SW_h=2400.0,
                       TD=8, DTYPA=dtype, BYTORDA=endian, AQ_mod=3, GRPDLY=0.0,
                       BF1=300.0, O1=0.0, NC=0)
    processed = dict(PPARMOD=0, SI=8, DTYPP=dtype, BYTORDP=endian, NC_proc=scale,
                     OFFSET=12.0, SW_p=2400.0, SF=300.0, STSR=0, STSI=8, XDIM=0, FT_mod=6)
    status(exp / "acqus", acquisition)
    status(proc / "procs", processed)
    format_ = ("<" if endian == 0 else ">") + "8" + ("i" if dtype == 0 else "d")
    raw = struct.pack(format_, *VALUES)
    if padded:
        raw += bytes((-len(raw)) % 1024)
    (exp / "fid").write_bytes(raw)
    (proc / "1r").write_bytes(raw)
    if imaginary:
        (proc / "1i").write_bytes(raw)
    return exp, proc, acquisition, processed
