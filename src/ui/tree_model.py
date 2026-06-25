from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import PurePosixPath

from Qt.QtCore import QAbstractItemModel, QModelIndex, Qt
from Qt.QtWidgets import QFileIconProvider

from ..models import RemoteEntry


@dataclass
class _TreeNode:
    entry: RemoteEntry
    parent: "_TreeNode | None" = None
    children: list["_TreeNode"] | None = None

    def child(self, row: int):
        if self.children is None:
            return None
        return self.children[row]


class RemoteTreeModel(QAbstractItemModel):
    def __init__(self, fs, root_entry: RemoteEntry, parent=None):
        super().__init__(parent)
        self.fs = fs
        self.root = _TreeNode(root_entry)
        self.icon_provider = QFileIconProvider()

    def columnCount(self, parent=QModelIndex()):
        return 3

    def rowCount(self, parent=QModelIndex()):
        node = self.root if not parent.isValid() else parent.internalPointer()
        self._ensure_loaded(node)
        return 0 if node.children is None else len(node.children)

    def index(self, row, column, parent=QModelIndex()):
        if not self.hasIndex(row, column, parent):
            return QModelIndex()
        node = self.root if not parent.isValid() else parent.internalPointer()
        self._ensure_loaded(node)
        if not node.children:
            return QModelIndex()
        child = node.children[row]
        return self.createIndex(row, column, child)

    def parent(self, index):
        if not index.isValid():
            return QModelIndex()
        node = index.internalPointer()
        if node is None or node.parent is None or node.parent is self.root:
            return QModelIndex()
        grandparent = node.parent.parent or self.root
        self._ensure_loaded(grandparent)
        row = grandparent.children.index(node.parent) if grandparent.children else 0
        return self.createIndex(row, 0, node.parent)

    def data(self, index, role=Qt.DisplayRole):
        if not index.isValid():
            return None
        node = index.internalPointer()
        entry = node.entry
        if role == Qt.DisplayRole:
            if index.column() == 0:
                return entry.name
            if index.column() == 1:
                return "" if entry.size is None else _format_size(entry.size)
            if index.column() == 2:
                return "" if entry.mtime is None else datetime.fromtimestamp(entry.mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
        if role == Qt.DecorationRole and index.column() == 0:
            if entry.is_dir:
                return self.icon_provider.icon(QFileIconProvider.IconType.Folder)
            return self.icon_provider.icon(QFileIconProvider.IconType.File)
        return None

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if orientation != Qt.Horizontal or role != Qt.DisplayRole:
            return None
        return ("Name", "Size", "MTime")[section]

    def flags(self, index):
        if not index.isValid():
            return Qt.NoItemFlags
        return Qt.ItemIsEnabled | Qt.ItemIsSelectable

    def hasChildren(self, parent=QModelIndex()):
        node = self.root if not parent.isValid() else parent.internalPointer()
        return node.entry.is_dir

    def entry_from_index(self, index: QModelIndex) -> RemoteEntry | None:
        if not index.isValid():
            return None
        node = index.internalPointer()
        return node.entry if node else None

    def index_for_path(self, path: PurePosixPath) -> QModelIndex:
        normalized = PurePosixPath(str(path))
        if normalized == self.root.entry.path:
            return QModelIndex()
        return self._index_for_node_path(self.root, normalized)

    def _ensure_loaded(self, node: _TreeNode):
        if node.children is not None or not node.entry.is_dir:
            return
        node.children = [_TreeNode(entry, parent=node) for entry in self.fs.ls(node.entry.path)]

    def _index_for_node_path(self, node: _TreeNode, path: PurePosixPath) -> QModelIndex:
        self._ensure_loaded(node)
        if not node.children:
            return QModelIndex()
        for row, child in enumerate(node.children):
            if child.entry.path == path:
                return self.createIndex(row, 0, child)
            if _is_ancestor(child.entry.path, path):
                nested = self._index_for_node_path(child, path)
                if nested.isValid():
                    return nested
        return QModelIndex()


def _format_size(size: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.2f} {unit}"
        value /= 1024
    return f"{size} B"


def _is_ancestor(parent: PurePosixPath, child: PurePosixPath) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True
