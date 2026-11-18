# Runtime-only inputs: no source data, project reports or credentials.
from pathlib import Path
from PyInstaller.utils.hooks import copy_metadata

root = Path(SPECPATH).parent
metadata = []
for name in ['numpy', 'scipy', 'nmrglue', 'PySide6-Essentials',
             'pyqtgraph', 'matplotlib', 'pypdf']:
    metadata += copy_metadata(name)

a = Analysis([str(root / 'scripts/entry.py')], pathex=[str(root / 'src')],
             binaries=[], datas=metadata + [
                 (str(root / 'build/package-inputs/build_identity.json'), 'bruker_nmr')],
             hiddenimports=[], hookspath=[],
             hooksconfig={'matplotlib': {'backends': ['pdf', 'agg']}}, runtime_hooks=[],
             excludes=['PyQt5', 'PyQt6', 'PySide2', 'IPython', 'pytest', 'pymupdf'])
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='NMR-go', console=False,
          icon=str(root / 'build/package-inputs/nmr-go.ico'))
coll = COLLECT(exe, a.binaries, a.datas, name='NMR-go')
