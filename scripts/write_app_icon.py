"""Generate a project-owned multi-resolution Windows ICO in the build directory."""
import io
import sys
from pathlib import Path

from PIL import Image
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtWidgets import QApplication

from bruker_nmr.ui.icons import icon

app = QApplication.instance() or QApplication([])
image = icon('app').pixmap(256, 256).toImage()
buffer = QBuffer()
buffer.open(QIODevice.OpenModeFlag.WriteOnly)
if not image.save(buffer, 'PNG'):
    raise RuntimeError('Could not render application identity')
destination = Path(sys.argv[1])
destination.parent.mkdir(parents=True, exist_ok=True)
with Image.open(io.BytesIO(bytes(buffer.data()))) as source:
    source.save(destination, format='ICO', sizes=[(n,n) for n in (16,24,32,48,64,128,256)])
print('Generated project-owned multi-resolution application icon')
