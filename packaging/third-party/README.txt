THIRD-PARTY SOFTWARE IN NMR-GO 0.12.0 FOR WINDOWS

The NMR-go source is available under the MIT terms in LICENSE.txt.
Third-party components retain their own licenses. This folder contains
notices from the wheels used for the build and the additional notices for
the native components below. Bundled-Python-packages.txt lists the Python
distributions inspected in the packaged application.

Qt and Qt for Python
---------------------
NMR-go uses Qt 6.8.0, PySide6 Essentials 6.8.0.2 and Shiboken6 6.8.0.2
under the GNU Lesser General Public License, version 3, for the libraries
distributed here. The Qt-LICENSES folder contains the LGPL and GPL text
and license material from the upstream Qt Base source. The upstream source
contains component-specific notices and attribution information.

Exact version source access:
Qt Base 6.8.0:
https://download.qt.io/archive/qt/6.8/6.8.0/submodules/qtbase-everywhere-src-6.8.0.tar.xz
https://github.com/qt/qtbase/tree/v6.8.0
Qt SVG 6.8.0:
https://download.qt.io/archive/qt/6.8/6.8.0/submodules/qtsvg-everywhere-src-6.8.0.tar.xz
https://github.com/qt/qtsvg/tree/v6.8.0
PySide6 and Shiboken6 6.8.0.2 release source:
https://download.qt.io/official_releases/QtForPython/pyside6/PySide6-6.8.0.2-src/
https://code.qt.io/cgit/pyside/pyside-setup.git/tag/?h=v6.8.0.2
https://github.com/pyside/pyside-setup/tree/v6.8.0.2
Qt bundled component attributions:
https://doc.qt.io/qt-6.8/licenses-used-in-qt.html

The Qt, PySide6 and Shiboken parts are installed as separate DLLs, extension
modules and plugins in the _internal directory. You may use modified,
interface-compatible builds of these LGPL libraries with NMR-go. Close the
app, keep a backup of the extracted distribution and replace the matching
library files in the copy before launching again. Keep compatible DLLs,
plugins and Python extensions together. You may reverse engineer the
application to debug changes to the LGPL-covered libraries. The application
does not verify or enforce the original library file signatures at startup.
The build manifest is an inventory for checking the original archive.

Python and native libraries
---------------------------
CPython 3.11.9: https://www.python.org/downloads/release/python-3119/
OpenSSL 3.0.13 and 3.5.9 are shipped with Qt/Python. Their license notices
are included here. Source versions:
https://github.com/openssl/openssl/tree/openssl-3.0.13
https://github.com/openssl/openssl/tree/openssl-3.5.9

The optional Qt software OpenGL renderer includes Mesa 11.2.2 and LLVM
3.6.2. The upstream notices are included. Versioned source archives:
https://archive.mesa3d.org/older-versions/11.x/11.2.2/
https://releases.llvm.org/3.6.2/
Qt upstream Windows software renderer packages:
https://download.qt.io/development_releases/prebuilt/llvmpipe/windows/

Microsoft Visual C++ and Universal CRT redistributable libraries are supplied
with the Windows Python and wheel runtime. Microsoft redistribution terms:
https://learn.microsoft.com/visualstudio/releases/2022/redistribution

The NumPy and SciPy wheel notices include their bundled numerical libraries.
The Pillow wheel notice includes its bundled image and font dependencies.
Matplotlib font notices are included with its wheel material and in
_internal/matplotlib/mpl-data/fonts. PyInstaller's bootloader exception is
in the bundled PyInstaller COPYING file.

For source access questions or an unavailable upstream link, use the
NMR-go repository issue tracker:
https://github.com/AlexNovich/1H-NMR-Go/issues
