"""Captured-page preview and explicit local PDF destination/overwrite workflow."""

from dataclasses import replace
from pathlib import Path

from PySide6.QtCore import QRectF, Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from bruker_nmr.export.current_pdf import (
    PAGE_HEIGHT,
    PAGE_WIDTH,
    SpectrumPage,
    proposed_header,
    write_page,
)
from bruker_nmr.ui.integrals import fit_dialog


class PagePreview(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.image = QImage()
        self.setMinimumSize(160, 110)

    def set_page(self, page):
        rgba, (width, height) = page.preview_rgba()
        self.image = QImage(rgba, width, height, width * 4, QImage.Format.Format_RGBA8888).copy()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#e5e7eb"))
        if not self.image.isNull():
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            scale = min(
                max(1, self.width() - 16) / self.image.width(),
                max(1, self.height() - 16) / self.image.height(),
            )
            width, height = self.image.width() * scale, self.image.height() * scale
            painter.drawImage(
                QRectF((self.width() - width) / 2, (self.height() - height) / 2, width, height),
                self.image,
            )


class PdfExportDialog(QDialog):
    def __init__(self, owner, snapshot):
        super().__init__(owner)
        self.header_shortened = False
        if not snapshot.caption and snapshot.state.data.print_header:
            try:
                header, self.header_shortened = proposed_header(snapshot.state.data.print_header)
            except (OSError, RuntimeError, ValueError):
                # The preview below presents the normal recoverable font/layout error.
                header = snapshot.state.data.print_header
            snapshot = replace(snapshot, caption=header)
        self.snapshot = snapshot
        self.page = None
        self.saved = False
        self.setWindowTitle("Export current view — landscape A4")
        self.setWindowModality(Qt.WindowModality.WindowModal)
        outer = QVBoxLayout(self)
        body = QWidget()
        layout = QVBoxLayout(body)
        self.controls_scroll = QScrollArea()
        self.controls_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.controls_scroll.setMinimumSize(0, 0)
        self.controls_scroll.setWidgetResizable(True)
        self.controls_scroll.setWidget(body)
        outer.addWidget(self.controls_scroll, 1)
        self.caption = QLineEdit(snapshot.caption)
        self.caption.setMaxLength(1024)
        self.caption.setPlaceholderText("Prefilled local header — edit or erase to omit")
        form = QFormLayout()
        form.addRow("Header", self.caption)
        self.zoom = QComboBox()
        self.zoom.addItems(["Fit page", "100%", "150%", "200%"])
        form.addRow("Preview zoom", self.zoom)
        layout.addLayout(form)
        self.preview = PagePreview(self)
        self.scroll = QScrollArea()
        self.scroll.setMinimumSize(0, 0)
        self.scroll.setWidgetResizable(True)
        self.scroll.setWidget(self.preview)
        self.zoom.currentIndexChanged.connect(self.change_zoom)
        layout.addWidget(self.scroll, 1)
        self.note = QLabel()
        self.note.setWordWrap(True)
        layout.addWidget(self.note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        self.save_button = QPushButton("Save PDF…")
        buttons.addButton(self.save_button, QDialogButtonBox.ButtonRole.ActionRole)
        self.save_button.setObjectName("save_current_pdf")
        self.save_button.clicked.connect(self.save_pdf)
        buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.setInterval(180)
        self.timer.timeout.connect(self.rebuild_preview)
        self.caption.textChanged.connect(self.caption_changed)
        fit_dialog(self, 1000, 750)
        self.rebuild_preview()

    def change_zoom(self):
        zoom = (1.0, 1.0, 1.5, 2.0)[self.zoom.currentIndex()]
        if self.zoom.currentIndex() == 0:
            self.preview.setMinimumSize(160, 110)
            self.preview.setMaximumSize(16777215, 16777215)
            self.scroll.setWidgetResizable(True)
        else:
            self.scroll.setWidgetResizable(False)
            self.preview.setFixedSize(
                round(PAGE_WIDTH / 72 * 96 * zoom), round(PAGE_HEIGHT / 72 * 96 * zoom)
            )
        self.preview.update()

    def caption_changed(self):
        if self.sender() is self.caption:
            self.header_shortened = False
        if self.snapshot is None:
            return
        self.save_button.setEnabled(False)
        self.timer.start()

    def rebuild_preview(self):
        self.timer.stop()
        if self.snapshot is None:
            return
        if self.page is not None:
            self.page.close()
            self.page = None
        self.save_button.setEnabled(False)
        try:
            self.page = SpectrumPage(
                replace(
                    self.snapshot,
                    caption=self.caption.text(),
                )
            )
            self.preview.set_page(self.page)
        except OSError:
            if self.page is not None:
                self.page.close()
                self.page = None
            self.note.setText(
                "Could not prepare the PDF preview. Keep the complete application folder and try again."
            )
            return
        except (ValueError, ArithmeticError, RuntimeError) as error:
            if self.page is not None:
                self.page.close()
                self.page = None
            self.preview.image = QImage()
            self.preview.update()
            self.note.setText(str(error))
            return
        self.save_button.setEnabled(True)
        notes = " ".join(self.page.preflight)
        if self.header_shortened:
            notes = (
                "The proposed name line was shortened with …; edit or erase the header. " + notes
            )
        self.note.setText(
            "A4 landscape; all visible integral values are full-range values. "
            + notes
            + " Close preview to change the spectrum. PDF is not an editable project."
        )

    def save_pdf(self):
        if self.timer.isActive():
            self.rebuild_preview()
        if self.page is None:
            return False
        filename, _ = QFileDialog.getSaveFileName(
            self,
            "Save current-view PDF",
            "NMR-go-spectrum.pdf",
            "PDF files (*.pdf)",
            options=QFileDialog.Option.DontConfirmOverwrite,
        )
        if not filename:
            return False
        destination = Path(filename)
        if destination.suffix.lower() != ".pdf":
            destination = destination.with_suffix(destination.suffix + ".pdf")
        overwrite = destination.exists()
        if overwrite:
            answer = QMessageBox.question(
                self,
                "Replace existing PDF?",
                "The chosen file already exists. Replace it? Keep original measurement and reference PDFs separately.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                return False
        try:
            write_page(destination, self.page, overwrite=overwrite)
        except (OSError, ValueError, ArithmeticError, RuntimeError):
            QMessageBox.warning(
                self,
                "PDF export failed",
                "Could not save the PDF. Choose a writable folder and check that the file is not open or protected. "
                "The spectrum remains open.",
            )
            return False
        self.saved = True
        self.note.setText(
            "PDF saved. Use File → Save annotations to store editable integral and peak records. "
            "Close this preview to continue working."
        )
        return True

    def done(self, result):
        self.timer.stop()
        self.caption.blockSignals(True)
        self.save_button.setEnabled(False)
        if self.page is not None:
            self.page.close()
            self.page = None
        self.preview.image = QImage()
        self.snapshot = None
        super().done(result)
