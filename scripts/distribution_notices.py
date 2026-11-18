"""Copy the license material for the packages present in the frozen executable."""

import shutil
import sys
from importlib.metadata import distribution
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader
from PySide6.QtCore import qVersion

IMPORT_DISTRIBUTIONS = {
    "PIL": "Pillow",
    "PySide6": "PySide6-Essentials",
    "_pyi_rth_utils": "PyInstaller",
    "dateutil": "python-dateutil",
    "fontTools": "fonttools",
    "mpl_toolkits": "matplotlib",
    "yaml": "PyYAML",
}
QT_PACKAGES = {"PySide6-Essentials", "shiboken6"}
NOTICE_PREFIXES = ("license", "copying", "notice", "copyright")
ROOT = Path(__file__).resolve().parents[1]


def copy_wheel_notices(name, target):
    installed = distribution(name)
    folder = target / f"{name}-{installed.version}"
    count = 0
    for entry in installed.files or ():
        path = Path(entry)
        if path.is_absolute() or ".." in path.parts:
            continue
        if ("licenses" in path.parts or path.name.lower().startswith(NOTICE_PREFIXES)):
            source = installed.locate_file(entry)
            if source.is_file():
                destination = folder / path
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, destination)
                count += 1
    if not count:
        raise RuntimeError(f"No wheel notices found for {name} {installed.version}")
    return installed.version


def write_release_notices(package):
    if qVersion() != "6.8.0":
        raise RuntimeError("Review the included Qt source information before changing Qt")
    for name in QT_PACKAGES:
        if distribution(name).version != "6.8.0.2":
            raise RuntimeError("Review the included Qt for Python source information before changing it")
    archive = CArchiveReader(str(package / "NMR-go.exe"))
    modules = archive.open_embedded_archive("PYZ.pyz").toc
    roots = {name.split(".", 1)[0] for name in modules}
    names = {IMPORT_DISTRIBUTIONS.get(name, name)
             for name in roots - sys.stdlib_module_names - {"bruker_nmr"}}
    names |= QT_PACKAGES | {"PyInstaller"}
    shutil.copyfile(ROOT / "LICENSE", package / "LICENSE.txt")
    shutil.copyfile(ROOT / "TERMS.md", package / "TERMS.md")
    shutil.copyfile(ROOT / "docs/ANNOTATION_SAVE.md", package / "ANNOTATION-SAVE.md")
    target = package / "THIRD-PARTY"
    shutil.copytree(ROOT / "packaging/third-party", target)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        shutil.copyfile(python_license, target / "CPython-Windows-LICENSE.txt")
    installed_packages = {
        name: distribution(name).version if name in QT_PACKAGES else copy_wheel_notices(name, target)
        for name in sorted(names)
    }
    (target / "Bundled-Python-packages.txt").write_text(
        "".join(f"{name} {version}\n" for name, version in installed_packages.items()),
        encoding="utf-8",
    )
    return installed_packages
