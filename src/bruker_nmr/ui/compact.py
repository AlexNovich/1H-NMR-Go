"""Compact Qt controls for the workbench docks."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHeaderView, QTableWidgetItem


def fit_columns(table, *, compact=(0,)):
    """Size identifier columns to their contents and share room among the values."""
    table.verticalHeader().hide()
    header = table.horizontalHeader()
    header.setMinimumSectionSize(24)
    header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    for column in compact:
        header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)


def value_item(value, *, detail=None):
    item = QTableWidgetItem(value)
    item.setToolTip(value if detail is None else detail)
    return item


def vertical_scroll(scroll):
    scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
