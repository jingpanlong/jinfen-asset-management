"""Centred, consistent presentation for every table in the application.

The asset ledger, the chart detail sheets, the import preview, the attachment
list and the permission matrices all render data in a ``QTableWidget``.  Rather
than editing every ``setItem`` call (and forgetting the ones added later), the
alignment is enforced while the cell is painted by a delegate, so text, numbers
and thumbnails stay centred no matter where or when the item was created.

Cell widgets (check boxes and combo boxes) are real widgets, not items, so they
are not touched by the delegate; wrap them with :func:`cell_box` to centre them
inside their cell.
"""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QHBoxLayout,QStyledItemDelegate,QWidget


class CenterDelegate(QStyledItemDelegate):
    """Draw every cell - text, number, icon or wrapped note - centred."""

    def initStyleOption(self,option,index):
        super().initStyleOption(option,index);option.displayAlignment=Qt.AlignCenter


def center_cells(table):
    """Centre all cell contents; covers items added later as well."""
    if not isinstance(table.itemDelegate(),CenterDelegate):table.setItemDelegate(CenterDelegate(table))
    return table


def center_header(table):
    """Centre the column titles, including titles replaced on every refresh."""
    table.horizontalHeader().setDefaultAlignment(Qt.AlignCenter)
    for index in range(table.columnCount()):
        item=table.horizontalHeaderItem(index)
        if item is not None:item.setTextAlignment(Qt.AlignCenter)
    return table


def center_table(table):
    """Centre both the header row and the data cells."""
    center_cells(table);center_header(table)
    return table


def cell_box(widget,max_width=None,pad=8):
    """Wrap a cell widget so it is centred inside its cell."""
    if max_width:widget.setMaximumWidth(max_width)
    box=QWidget();layout=QHBoxLayout(box);layout.setContentsMargins(pad,0,pad,0);layout.setSpacing(0)
    layout.addStretch(1);layout.addWidget(widget);layout.addStretch(1)
    return box
