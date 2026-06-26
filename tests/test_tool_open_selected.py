from __future__ import annotations

import importlib
import sys
import types
from pathlib import PurePosixPath
from types import SimpleNamespace

from cryoremote_bundle.models import RemoteEntry


class _DummyMeta(type):
    def __getattr__(cls, _name):
        return 0


class _Dummy(metaclass=_DummyMeta):
    def __init__(self, *args, **kwargs):
        pass

    def __call__(self, *args, **kwargs):
        return _Dummy()

    def __getattr__(self, _name):
        return _Dummy()


class _FakeLogger:
    def __init__(self):
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.infos: list[str] = []

    def error(self, message: str):
        self.errors.append(message)

    def warning(self, message: str):
        self.warnings.append(message)

    def info(self, message: str):
        self.infos.append(message)


class _FakeWidget:
    def __init__(self, entry: RemoteEntry, *, confirm: bool = True):
        self._entry = entry
        self._confirm = confirm
        self.status_calls: list[tuple[str, bool, bool]] = []
        self.confirmed_paths: list[PurePosixPath] = []

    def current_entry(self) -> RemoteEntry:
        return self._entry

    def confirm_command_file_open(self, path: PurePosixPath) -> bool:
        self.confirmed_paths.append(path)
        return self._confirm

    def show_status(self, message: str, *, warning: bool = False, error: bool = False):
        self.status_calls.append((message, warning, error))


def _import_tool_module(monkeypatch):
    def make_qt_module(name: str) -> types.ModuleType:
        module = types.ModuleType(name)
        module.__getattr__ = lambda _attr: _Dummy
        return module

    qt_package = types.ModuleType("Qt")
    qt_core = make_qt_module("Qt.QtCore")
    qt_gui = make_qt_module("Qt.QtGui")
    qt_widgets = make_qt_module("Qt.QtWidgets")
    qt_package.QtCore = qt_core
    qt_package.QtGui = qt_gui
    qt_package.QtWidgets = qt_widgets

    commands_module = types.ModuleType("chimerax.core.commands")
    commands_module.run = lambda *_args, **_kwargs: None
    configfile_module = types.ModuleType("chimerax.core.configfile")
    configfile_module.Value = lambda default, *_args, **_kwargs: default
    settings_module = types.ModuleType("chimerax.core.settings")
    settings_module.Settings = _Dummy
    tools_module = types.ModuleType("chimerax.core.tools")
    tools_module.ToolInstance = _Dummy
    tools_module.get_singleton = lambda *_args, **_kwargs: None
    ui_module = types.ModuleType("chimerax.ui")
    ui_module.MainToolWindow = _Dummy
    core_module = types.ModuleType("chimerax.core")
    core_module.commands = commands_module
    core_module.configfile = configfile_module
    core_module.settings = settings_module
    core_module.tools = tools_module
    chimerax_module = types.ModuleType("chimerax")
    chimerax_module.core = core_module
    chimerax_module.ui = ui_module

    monkeypatch.setitem(sys.modules, "Qt", qt_package)
    monkeypatch.setitem(sys.modules, "Qt.QtCore", qt_core)
    monkeypatch.setitem(sys.modules, "Qt.QtGui", qt_gui)
    monkeypatch.setitem(sys.modules, "Qt.QtWidgets", qt_widgets)
    monkeypatch.setitem(sys.modules, "chimerax", chimerax_module)
    monkeypatch.setitem(sys.modules, "chimerax.core", core_module)
    monkeypatch.setitem(sys.modules, "chimerax.core.commands", commands_module)
    monkeypatch.setitem(sys.modules, "chimerax.core.configfile", configfile_module)
    monkeypatch.setitem(sys.modules, "chimerax.core.settings", settings_module)
    monkeypatch.setitem(sys.modules, "chimerax.core.tools", tools_module)
    monkeypatch.setitem(sys.modules, "chimerax.ui", ui_module)

    for name in (
        "cryoremote_bundle.ui.assets",
        "cryoremote_bundle.ui.main_widget",
        "cryoremote_bundle.ui.pipeline_table_model",
        "cryoremote_bundle.ui.pipeline_view",
        "cryoremote_bundle.ui.tree_model",
        "cryoremote_bundle.tool",
    ):
        sys.modules.pop(name, None)

    return importlib.import_module("cryoremote_bundle.tool")


def _make_tool(tool_module, entry: RemoteEntry, *, confirm: bool = True):
    tool = object.__new__(tool_module.CryoRemoteTool)
    tool.session = SimpleNamespace(logger=_FakeLogger())
    tool._widget = _FakeWidget(entry, confirm=confirm)
    tool._controller = SimpleNamespace(
        fs=None,
        config=None,
        project_index=None,
        active_project_root=None,
        cache_manager=None,
        open_path=lambda _path: None,
    )
    tool._fs = object()
    tool._config = SimpleNamespace(alias="gm00")
    return tool


def test_open_selected_routes_command_files_to_command_file_helper(monkeypatch):
    tool_module = _import_tool_module(monkeypatch)
    entry = RemoteEntry(PurePosixPath("/share/project/open_maps.cxc"), "file", size=12, mtime=1.0)
    tool = _make_tool(tool_module, entry)
    dispatched: list[RemoteEntry] = []
    tool._open_selected_command_file = lambda actual: dispatched.append(actual)

    tool._open_selected()

    assert dispatched == [entry]


def test_open_selected_routes_map_opening_through_controller(monkeypatch):
    tool_module = _import_tool_module(monkeypatch)
    entry = RemoteEntry(PurePosixPath("/share/project/map.mrc"), "file", size=32, mtime=1.0)
    tool = _make_tool(tool_module, entry)
    calls: list[PurePosixPath] = []
    tool._controller.open_path = lambda actual: calls.append(actual)

    tool._open_selected()

    assert calls == [entry.path]
    assert tool._widget.status_calls == [("Opened map.mrc", False, False)]


def test_open_selected_command_file_cancellation_stops_before_controller_open(monkeypatch):
    tool_module = _import_tool_module(monkeypatch)
    entry = RemoteEntry(PurePosixPath("/share/project/open_maps.cxc"), "file", size=12, mtime=1.0)
    tool = _make_tool(tool_module, entry, confirm=False)

    def fail_if_called(_path):
        raise AssertionError("command file should not be opened when confirmation is declined")

    tool._controller.open_path = fail_if_called

    tool._open_selected_command_file(entry)

    assert tool._widget.confirmed_paths == [entry.path]
    assert tool._widget.status_calls == [("Command file execution cancelled.", False, False)]


def test_open_selected_command_file_delegates_execution_to_controller(monkeypatch):
    tool_module = _import_tool_module(monkeypatch)
    entry = RemoteEntry(PurePosixPath("/share/project/scripts/open_maps.cxc"), "file", size=20, mtime=1.0)
    tool = _make_tool(tool_module, entry, confirm=True)
    calls: list[PurePosixPath] = []
    tool._controller.open_path = lambda actual: calls.append(actual)

    tool._open_selected_command_file(entry)

    assert calls == [entry.path]
    assert tool._widget.status_calls == [("Executed open_maps.cxc.", False, False)]
