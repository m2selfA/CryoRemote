from __future__ import annotations

import importlib
import sys
import types


def _import_assets_module(monkeypatch):
    qt_gui = types.ModuleType("Qt.QtGui")
    qt_gui.QIcon = type("QIcon", (), {})
    qt_gui.QPixmap = type("QPixmap", (), {})
    qt_package = types.ModuleType("Qt")
    qt_package.QtGui = qt_gui

    monkeypatch.setitem(sys.modules, "Qt", qt_package)
    monkeypatch.setitem(sys.modules, "Qt.QtGui", qt_gui)
    sys.modules.pop("cryoremote_bundle.ui.assets", None)
    return importlib.import_module("cryoremote_bundle.ui.assets")


def test_asset_path_prefers_packaged_assets(monkeypatch, tmp_path):
    assets_module = _import_assets_module(monkeypatch)
    monkeypatch.setattr(
        assets_module,
        "__file__",
        str(tmp_path / "site-packages" / "chimerax" / "cryoremote" / "ui" / "assets.py"),
        raising=False,
    )
    package_asset = tmp_path / "site-packages" / "chimerax" / "cryoremote" / "assets" / "brand" / "mark.png"
    root_asset = tmp_path / "site-packages" / "chimerax" / "assets" / "brand" / "mark.png"
    package_asset.parent.mkdir(parents=True, exist_ok=True)
    root_asset.parent.mkdir(parents=True, exist_ok=True)
    package_asset.write_text("package", encoding="utf-8")
    root_asset.write_text("root", encoding="utf-8")

    assert assets_module.asset_path("brand/mark.png") == package_asset


def test_asset_path_falls_back_to_repo_root_assets(monkeypatch, tmp_path):
    assets_module = _import_assets_module(monkeypatch)
    monkeypatch.setattr(
        assets_module,
        "__file__",
        str(tmp_path / "repo" / "src" / "ui" / "assets.py"),
        raising=False,
    )
    repo_asset = tmp_path / "repo" / "assets" / "illustrations" / "hero.png"
    repo_asset.parent.mkdir(parents=True, exist_ok=True)
    repo_asset.write_text("root", encoding="utf-8")

    assert assets_module.asset_path("illustrations/hero.png") == repo_asset
