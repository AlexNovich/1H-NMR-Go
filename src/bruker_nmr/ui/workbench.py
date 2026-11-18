"""Native workbench shell; existing document commands retain scientific ownership."""

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QAction, QColor, QKeySequence, QPalette
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QApplication,
    QDockWidget,
    QFormLayout,
    QLineEdit,
    QMenu,
    QMessageBox,
    QSizePolicy,
    QSystemTrayIcon,
    QTabWidget,
    QToolBar,
)

from bruker_nmr.ui.icons import NAMES, icon


def embed(dialog, parent):
    dialog.setParent(parent, Qt.WindowType.Widget)
    dialog.setWindowModality(Qt.WindowModality.NonModal)
    dialog.setMinimumSize(0, 0)
    dialog.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
    dialog.setMaximumSize(16777215, 16777215)
    for form in dialog.findChildren(QFormLayout):
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
    return dialog


class Workbench:
    def __init__(self, owner):
        self.owner = owner
        palette = QPalette(owner.palette())
        for role, colour in [
            (QPalette.ColorRole.Window, "#f5f6f8"),
            (QPalette.ColorRole.WindowText, "#17212f"),
            (QPalette.ColorRole.Base, "#ffffff"),
            (QPalette.ColorRole.Text, "#17212f"),
            (QPalette.ColorRole.Button, "#f5f6f8"),
            (QPalette.ColorRole.ButtonText, "#17212f"),
            (QPalette.ColorRole.Highlight, "#d4e5f4"),
            (QPalette.ColorRole.HighlightedText, "#17212f"),
        ]:
            palette.setColor(role, QColor(colour))
        owner.setPalette(palette)
        owner.setStyleSheet(
            "QToolButton { padding: 1px 4px; border: 1px solid transparent; border-radius: 3px; } "
            "QToolButton:hover { background: #e4edf5; border-color: #8298ad; } "
            "QToolButton:checked, QToolButton:pressed { background: #d4e5f4; border: 2px solid #365d80; } "
            "QToolButton:disabled { color: #687482; }"
        )
        owner.view_box.anchor_created.connect(self.canvas_anchor)
        self.inspector = QDockWidget("Inspector", owner)
        self.inspector.setObjectName("inspector")
        self.inspector.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable)
        self.tabs = QTabWidget()
        self.inspector.setWidget(self.tabs)
        owner.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.inspector)
        for label, editor in [("Integrals", owner.integrals), ("Peaks", owner.peaks)]:
            self.tabs.addTab(embed(editor, self.tabs), label)
        self.inspector.hide()
        self.document_id = owner.document.document_id
        self.process = QDockWidget("Process — preview", owner)
        self.process.setObjectName("processing")
        self.process.setFeatures(QDockWidget.DockWidgetFeature.DockWidgetClosable)
        owner.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self.process)
        self.process.hide()
        self.process.visibilityChanged.connect(self.process_visibility)
        for toolbar in owner.findChildren(QToolBar):
            owner.removeToolBar(toolbar)
            toolbar.hide()
        bar = QToolBar("Spectrum", owner)
        bar.setObjectName("spectrum_tools")
        bar.setMovable(False)
        owner.addToolBar(bar)
        bar.setIconSize(QSize(24, 24))
        owner.setWindowIcon(icon("app"))
        self.bar = bar
        owner.export_action.setIconText("PDF")
        self.integrals_action = QAction("Integrals", owner)
        self.integrals_action.triggered.connect(lambda: self.show_table(owner.integrals))
        self.peaks_action = QAction("Peaks", owner)
        self.peaks_action.triggered.connect(lambda: self.show_table(owner.peaks))
        self.appearance_action = QAction("Appearance…", owner)
        self.appearance_action.triggered.connect(owner.appearance_dialog)
        groups = [
            [owner.open_zip_action, owner.open_folder_action, owner.save_action],
            [owner.undo_action, owner.redo_action],
            [owner.zoom_action, owner.pan_action, owner.full_action, owner.previous_action],
            [owner.editing_actions["Phase"], owner.editing_actions["Baseline"]],
            [owner.integrate_action, owner.pick_action, owner.editing_actions["Reference"],
             self.integrals_action, self.peaks_action],
            [owner.export_action],
        ]
        count = 0
        for group in groups:
            if count:
                bar.addSeparator()
            for action in group:
                action.setIcon(icon(NAMES[count]))
                count += 1
                shortcut = action.shortcut().toString(QKeySequence.SequenceFormat.NativeText)
                action.setToolTip(
                    action.text().replace("&", "") + (f" ({shortcut})" if shortcut else "")
                )
                bar.addAction(action)
                if action in (
                    owner.editing_actions["Phase"],
                    owner.editing_actions["Baseline"],
                    owner.export_action,
                ):
                    bar.widgetForAction(action).setToolButtonStyle(
                        Qt.ToolButtonStyle.ToolButtonTextBesideIcon
                    )
        menus = owner.menuBar()
        self.menus = []
        for name, actions in [
            (
                "File",
                [
                    owner.open_zip_action,
                    owner.open_folder_action,
                    owner.demo_action,
                    owner.save_action,
                    owner.export_action,
                ],
            ),
            ("Edit", [owner.undo_action, owner.redo_action, owner.reset_action]),
            (
                "View",
                [
                    self.integrals_action,
                    self.peaks_action,
                    self.appearance_action,
                    owner.grid_action,
                    owner.full_action,
                    owner.previous_action,
                ],
            ),
            (
                "Process",
                [
                    owner.editing_actions["Process from FID"],
                    owner.editing_actions["Phase"],
                    owner.editing_actions["Baseline"],
                ],
            ),
            (
                "Annotate",
                [owner.integrate_action, owner.pick_action, owner.editing_actions["Reference"]],
            ),
        ]:
            menu = menus.addMenu(name)
            self.menus.append(menu)
            menu.addActions(actions)
        self.menus[0].addAction("Exit", owner.close, QKeySequence("Ctrl+Q"))
        help_menu = menus.addMenu("Help")
        self.menus.append(help_menu)
        help_menu.addAction("Controls and scientific notes", self.help)
        help_menu.addAction("About NMR-go", self.about)
        help_menu.addAction(
            "Spectrum details",
            self.details,
        )
        self.delete_action = QAction("Delete selected annotation", owner)
        self.delete_action.setShortcut(QKeySequence("Delete"))
        self.delete_action.triggered.connect(self.delete_selected)
        owner.addAction(self.delete_action)
        self.menus[1].addAction(self.delete_action)
        self.cancel_action = QAction("Cancel / clear selection", owner)
        self.cancel_action.setShortcut(QKeySequence("Escape"))
        self.cancel_action.triggered.connect(self.escape)
        owner.cancel_action.setShortcut(QKeySequence())
        owner.addAction(self.cancel_action)
        self.tray = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(owner.windowIcon(), owner)
            self.tray.setToolTip('NMR-go')
            menu = QMenu(owner)
            menu.addAction('Open NMR-go', self.restore)
            menu.addAction('Quit NMR-go', owner.close)
            self.tray.setContextMenu(menu)
            self.tray.activated.connect(self.tray_activated)
            QApplication.instance().aboutToQuit.connect(self.tray.hide)
            self.tray.show()
        owner.export_button = bar.widgetForAction(owner.export_action)
        owner.integrals_button = bar.widgetForAction(self.integrals_action)
        owner.peaks_button = bar.widgetForAction(self.peaks_action)
        owner.integrals.table.cellDoubleClicked.connect(lambda *args: owner.integrals.edit_dialog())
        owner.peaks.table.cellDoubleClicked.connect(lambda *args: owner.peaks.edit_dialog())

    def set_file_busy(self, saving):
        for action in (self.owner.open_zip_action, self.owner.open_folder_action, self.owner.demo_action):
            action.setEnabled(not saving)

    def details(self):
        from bruker_nmr.ui.details import show_details
        show_details(self.owner)

    def restore(self):
        self.owner.showNormal()
        self.owner.raise_()
        self.owner.activateWindow()

    def tray_activated(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self.restore()

    def sync_document(self):
        if self.document_id != self.owner.document.document_id:
            self.document_id = self.owner.document.document_id
            self.owner.selected_editor = None

    def show_table(self, editor):
        self.owner.cancel_processing()
        editor.refresh_table()
        self.tabs.setCurrentWidget(editor)
        self.inspector.show()
        self.inspector.raise_()
        self.owner.resizeDocks([self.inspector], [420], Qt.Orientation.Horizontal)

    def show_appearance(self, panel=None):
        self.owner.cancel_processing()
        if panel is not None:
            panel.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Expanding)
            panel.layout().setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapAllRows)
            self.appearance = panel
            self.tabs.addTab(panel, "Appearance")
        self.tabs.setCurrentWidget(self.appearance)
        self.inspector.show()
        self.owner.resizeDocks([self.inspector], [370], Qt.Orientation.Horizontal)

    def show_process(self, panel):
        if self.process.widget() is panel and panel.isVisible():
            self.process.raise_()
            return
        old = self.process.widget()
        if old is not None and old is not panel:
            old.hide()
            if old is self.owner.phase_dialog:
                self.owner.phase_dialog = None
            if old is self.owner.baseline_dialog:
                self.owner.baseline_dialog = None
            old.setParent(self.owner)
            old.deleteLater()
        self.process.setWindowTitle(
            "Phase — preview" if panel is self.owner.phase_dialog else "Baseline — preview"
        )
        self.process.setWidget(embed(panel, self.process))
        self.inspector.hide()
        self.process.show()
        panel.show()
        self.owner.resizeDocks([self.process], [370], Qt.Orientation.Horizontal)
        panel.finished.connect(lambda *_: self.process.hide())

    def process_visibility(self, visible):
        if not visible:
            panel = self.process.widget()
            if panel is not None and panel.document is not None:
                panel.reject()

    def delete_selected(self):
        focus = QApplication.focusWidget()
        if isinstance(focus, (QLineEdit, QAbstractSpinBox)) or self.owner.loading:
            return
        editor = self.tabs.currentWidget() if self.inspector.isVisible() else None
        if editor not in (self.owner.integrals, self.owner.peaks):
            editor = getattr(self.owner, "selected_editor", None)
        if editor is not None and editor.selected is not None:
            editor.delete_selected()

    def canvas_anchor(self, start, end):
        panel = self.owner.baseline_dialog
        if panel is None or not panel.isVisible() or not panel.valid():
            self.owner.view_box.anchoring = False
            return
        import pyqtgraph as pg

        box = self.owner.view_box
        a, b = box.mapToView(pg.Point(start, 0)).x(), box.mapToView(pg.Point(end, 0)).x()
        panel.high.setValue(max(a, b))
        panel.low.setValue(min(a, b))
        panel.add_anchor()

    def escape(self):
        o = self.owner
        if o.loading:
            o.cancel_loading()
        elif o.phase_dialog is not None and o.phase_dialog.isVisible():
            o.phase_dialog.reject()
        elif o.baseline_dialog is not None and o.baseline_dialog.isVisible():
            if o.baseline_dialog.draw_anchor.isChecked():
                o.view_box.cancel_pending()
                o._pointer_pressed = False
                o.baseline_dialog.draw_anchor.setChecked(False)
            else:
                o.baseline_dialog.reject()
        else:
            o.view_box.cancel_pending()
            o._pointer_pressed = False
            o.selected_editor = None
            o.peaks.selected = None
            o.integrals.selected = None
            o.zoom_action.trigger()
            o._render_document(select_first=False)

    def about(self):
        o = self.owner
        box = QMessageBox(o)
        box.setWindowTitle("About NMR-go")
        box.setTextFormat(Qt.TextFormat.PlainText)
        box.setText(
            f"NMR-go\nVersion {o.identity['version']}\n\n"
            "A desktop application for processing and annotating Bruker 1D proton NMR spectra.\n\n"
            "Open Bruker ZIP archives or folders, work with processed spectra or raw FIDs, "
            "adjust phase and baseline, integrate signals, pick peaks, and export "
            "publication-style PDF plots.\n\n"
            "All spectrum processing takes place locally on your computer. "
            "No internet connection is required.\n\n"
            "Created by Alexander Novichkov.\n"
            "Built for everyday work at the chemistry bench."
        )
        box.setDetailedText(f"Source commit: {o.identity['source_commit']}")
        box.exec()

    def help(self):
        QMessageBox.information(
            self.owner,
            "Controls and scientific notes",
            "Wheel over ppm axis or Ctrl+wheel: X zoom; elsewhere: Y zoom.\n"
            "Ctrl+drag: pan; Shift+wheel: X pan; Backspace/Alt+Left: previous view.\n"
            "Ctrl+Z / Ctrl+Y: edit Undo / Redo. I: Integrate; K: Pick Peaks; Z: Zoom; P: Pan.\n"
            "Ctrl+S: Save annotations. Ctrl+E: PDF. Escape: cancel active work or return to Zoom.\n"
            "Phase is relative; automatic proposals require inspection and explicit Apply.\n"
            "Baseline requires user-selected quiet anchors. No edge is assumed quiet.\n"
            "Inspect normalization/review notices in the inspector. Sources are immutable.\n"
            "Appearance controls trace colour/width and peak crosses can be hidden in Peaks.",
        )
