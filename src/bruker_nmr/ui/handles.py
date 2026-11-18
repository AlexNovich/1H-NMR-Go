"""Give Control-drag navigation priority over scientific edit handles."""
from PySide6.QtCore import Qt


def allow_control_pan(item):
    original_drag = item.mouseDragEvent
    original_hover = item.hoverEvent

    def drag(event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            event.ignore()
        else:
            original_drag(event)

    def hover(event):
        if not event.isExit() and event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            return
        original_hover(event)

    item.mouseDragEvent = drag
    item.hoverEvent = hover


def menu_action(menu, text, owner, callback):
    """A menu opened on an old document/revision never edits a replacement."""
    ticket = (owner.document.document_id, owner.document.revision)
    def invoke(*args):
        if ticket == (owner.document.document_id, owner.document.revision):
            callback()
        else:
            owner.statusBar().showMessage('Menu expired after a document change; reopen it.')
    action = menu.addAction(text)
    action.triggered.connect(invoke)
    return action
