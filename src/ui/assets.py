from __future__ import annotations

from pathlib import Path

from Qt.QtGui import QIcon, QPixmap


def asset_path(relative_path: str) -> Path:
    for base in _asset_roots():
        candidate = base / relative_path
        if candidate.exists():
            return candidate
    return _asset_roots()[0] / relative_path


def _asset_roots() -> tuple[Path, Path]:
    # Bundled installs keep assets next to the package; source checkouts keep them at repo root.
    resolved = Path(__file__).resolve()
    return resolved.parents[1] / "assets", resolved.parents[2] / "assets"


def load_icon(relative_path: str) -> QIcon:
    path = asset_path(relative_path)
    if not path.exists():
        return QIcon()
    return QIcon(str(path))


def load_pixmap(relative_path: str) -> QPixmap | None:
    path = asset_path(relative_path)
    if not path.exists():
        return None
    pixmap = QPixmap(str(path))
    return None if pixmap.isNull() else pixmap
