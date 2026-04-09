"""Preview dialog showing what sync will do before executing."""

from __future__ import annotations

from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from fgc_sync.models import SyncAction, SyncPlan
from fgc_sync.views.styles import DANGER, SUCCESS, WARNING


class PreviewDialog(QDialog):
    def __init__(self, plan: SyncPlan, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Sync Preview")
        self.setMinimumSize(650, 400)

        layout = QVBoxLayout(self)

        summary = (
            f"{len(plan.creates)} to create, "
            f"{len(plan.updates)} to update, "
            f"{len(plan.deletes)} to delete"
        )
        layout.addWidget(QLabel(f"<b>Sync Plan:</b> {summary}"))

        for err in plan.errors:
            layout.addWidget(QLabel(f"<span style='color:{DANGER}'>Error: {err}</span>"))

        if plan.entries:
            table = QTableWidget(len(plan.entries), 5)
            table.setHorizontalHeaderLabels(["Action", "Title", "Date", "Time", "Participants"])
            table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
            table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
            table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)

            action_colors = {
                SyncAction.CREATE: SUCCESS,
                SyncAction.UPDATE: WARNING,
                SyncAction.DELETE: DANGER,
            }

            for row, entry in enumerate(plan.entries):
                color = action_colors.get(entry.action, "")
                action_item = QTableWidgetItem(entry.action.value.capitalize())
                if color:
                    from PySide6.QtGui import QColor
                    action_item.setForeground(QColor(color))
                table.setItem(row, 0, action_item)
                table.setItem(row, 1, QTableWidgetItem(entry.title))
                table.setItem(row, 2, QTableWidgetItem(entry.date))
                table.setItem(row, 3, QTableWidgetItem(entry.time))
                table.setItem(row, 4, QTableWidgetItem(entry.participants_info))

            layout.addWidget(table)
        else:
            layout.addWidget(QLabel("No changes to sync."))

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        if plan.entries:
            sync_btn = QPushButton("Sync Now")
            sync_btn.setProperty("primary", True)
            sync_btn.clicked.connect(self.accept)
            btn_row.addWidget(sync_btn)
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        layout.addLayout(btn_row)

    def showEvent(self, event):
        super().showEvent(event)
        from fgc_sync.views.styles import apply_acrylic
        apply_acrylic(self)
