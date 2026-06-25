from __future__ import annotations

from Qt.QtCore import QAbstractTableModel, QModelIndex, Qt

from ..models import RelionJobNode, RelionProjectIndex
from ..preview import format_timestamp


class RelionPipelineTableModel(QAbstractTableModel):
    HEADERS = ("Job ID", "Type", "State", "Title", "Updated")

    def __init__(self, parent=None):
        super().__init__(parent)
        self._project: RelionProjectIndex | None = None
        self._jobs: list[RelionJobNode] = []

    def set_project(self, project: RelionProjectIndex | None):
        self.beginResetModel()
        self._project = project
        self._jobs = list(project.jobs) if project else []
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return len(self._jobs)

    def columnCount(self, parent=QModelIndex()):
        if parent.isValid():
            return 0
        return len(self.HEADERS)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        job = self._jobs[index.row()]
        if role == Qt.DisplayRole:
            if index.column() == 0:
                return job.job_id
            if index.column() == 1:
                return job.job_type or "unknown"
            if index.column() == 2:
                return job.state
            if index.column() == 3:
                return job.title
            if index.column() == 4:
                return format_timestamp(job.updated_at)
        if role == Qt.ToolTipRole:
            return "\n".join(job.notes) if job.notes else job.title
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation != Qt.Horizontal or role != Qt.DisplayRole:
            return None
        return self.HEADERS[section]

    def job_at(self, row: int) -> RelionJobNode | None:
        if row < 0 or row >= len(self._jobs):
            return None
        return self._jobs[row]

    def row_for_job(self, job_id: str) -> int:
        for row, job in enumerate(self._jobs):
            if job.job_id == job_id:
                return row
        return -1
