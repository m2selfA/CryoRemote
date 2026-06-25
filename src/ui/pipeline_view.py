from __future__ import annotations

from Qt.QtCore import QPointF, Qt, Signal
from Qt.QtGui import QBrush, QColor, QPainter, QPainterPath, QPen
from Qt.QtWidgets import QGraphicsItem, QGraphicsPathItem, QGraphicsRectItem, QGraphicsScene, QGraphicsTextItem, QGraphicsView

from ..models import RelionProjectIndex
from ..relion import build_flowchart_layout


NODE_WIDTH = 210
NODE_HEIGHT = 72
X_SPACING = 80
Y_SPACING = 36
STATE_COLORS = {
    "succeeded": "#d9f2d9",
    "running": "#d9ecff",
    "scheduled": "#fff3cd",
    "failed": "#f8d7da",
    "aborted": "#eadcf8",
    "unknown": "#eeeeee",
}


class RelionPipelineFlowchart(QGraphicsView):
    job_selected = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setScene(QGraphicsScene(self))
        self.setRenderHints(self.renderHints() | QPainter.Antialiasing)
        self.setDragMode(QGraphicsView.ScrollHandDrag)
        self._project: RelionProjectIndex | None = None
        self._job_items: dict[str, QGraphicsRectItem] = {}
        self._selected_job_id: str | None = None
        self._suppress_signal = False
        self.scene().selectionChanged.connect(self._scene_selection_changed)

    def set_project(self, project: RelionProjectIndex | None):
        scene = self.scene()
        scene.clear()
        self._project = project
        self._job_items = {}
        self._selected_job_id = None

        if project is None or not project.jobs:
            self.job_selected.emit(None)
            return

        layout_map = {layout.job_id: layout for layout in build_flowchart_layout(project)}
        for job in project.jobs:
            layout = layout_map[job.job_id]
            x = layout.column * (NODE_WIDTH + X_SPACING)
            y = layout.row * (NODE_HEIGHT + Y_SPACING)
            item = QGraphicsRectItem(0, 0, NODE_WIDTH, NODE_HEIGHT)
            item.setPos(x, y)
            item.setBrush(QBrush(QColor(STATE_COLORS.get(job.state, STATE_COLORS["unknown"]))))
            item.setPen(QPen(QColor("#555555"), 1.2))
            item.setFlag(QGraphicsItem.ItemIsSelectable, True)
            item.setData(0, job.job_id)
            item.setToolTip(job.title)
            scene.addItem(item)

            label = QGraphicsTextItem(item)
            label.setDefaultTextColor(QColor("#202020"))
            label.setTextWidth(NODE_WIDTH - 16)
            label.setPlainText(f"{job.title}\n{job.job_id}\n{job.state}")
            label.setPos(8, 6)
            label.setAcceptedMouseButtons(Qt.NoButton)
            self._job_items[job.job_id] = item

        for job in project.jobs:
            child_item = self._job_items.get(job.job_id)
            if child_item is None:
                continue
            for parent_id in job.parents:
                parent_item = self._job_items.get(parent_id)
                if parent_item is None:
                    continue
                path = QPainterPath()
                start = QPointF(parent_item.pos().x() + NODE_WIDTH, parent_item.pos().y() + NODE_HEIGHT / 2)
                end = QPointF(child_item.pos().x(), child_item.pos().y() + NODE_HEIGHT / 2)
                mid_x = (start.x() + end.x()) / 2
                path.moveTo(start)
                path.cubicTo(QPointF(mid_x, start.y()), QPointF(mid_x, end.y()), end)
                edge = QGraphicsPathItem(path)
                edge.setPen(QPen(QColor("#8a8a8a"), 1.0))
                edge.setZValue(-1)
                scene.addItem(edge)

        scene.setSceneRect(scene.itemsBoundingRect().adjusted(-24, -24, 24, 24))
        first_job = project.jobs[0].job_id
        self.set_current_job(first_job, emit=False)

    def current_job_id(self) -> str | None:
        return self._selected_job_id

    def job_id_at(self, view_pos) -> str | None:
        item = self.itemAt(view_pos)
        while item is not None:
            job_id = item.data(0)
            if job_id:
                return str(job_id)
            item = item.parentItem()
        return None

    def set_current_job(self, job_id: str | None, *, emit: bool):
        self._suppress_signal = True
        self.scene().clearSelection()
        self._selected_job_id = None
        if job_id and job_id in self._job_items:
            item = self._job_items[job_id]
            item.setSelected(True)
            self._selected_job_id = job_id
            self.centerOn(item)
        self._refresh_pens()
        self._suppress_signal = False
        if emit:
            self.job_selected.emit(self._selected_job_id)

    def _scene_selection_changed(self):
        if self._suppress_signal:
            return
        selected = self.scene().selectedItems()
        self._selected_job_id = selected[0].data(0) if selected else None
        self._refresh_pens()
        self.job_selected.emit(self._selected_job_id)

    def _refresh_pens(self):
        for job_id, item in self._job_items.items():
            width = 2.8 if job_id == self._selected_job_id else 1.2
            color = "#1d5aa6" if job_id == self._selected_job_id else "#555555"
            item.setPen(QPen(QColor(color), width))
