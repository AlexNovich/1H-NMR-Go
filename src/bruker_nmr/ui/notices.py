"""Expandable processing detail with an always-visible interpretation status."""
from PySide6.QtWidgets import QLabel


class ProcessingNotice(QLabel):
    def __init__(self, text):
        super().__init__(text)
        self.brief = QLabel('Preview only — Apply or Cancel explicitly.')
        self.brief.setWordWrap(True)
        self.brief.setToolTip(text)

    def set_expanded(self, expanded):
        self.setVisible(expanded)
        self.brief.setVisible(not expanded)

    def setText(self, text):
        super().setText(text)
        if 'LOW CONFIDENCE' in text:
            short = 'LOW CONFIDENCE — inspect the proposal before Apply.'
        elif len(text) > 110:
            short = text[:107].rstrip() + '…'
        else:
            short = text
        self.brief.setText(short)
        self.brief.setToolTip(text)
