from __future__ import annotations

import importlib
import sys
import types
from pathlib import PurePosixPath
from types import SimpleNamespace

import pytest

from cryoremote_bundle.actions import (
    ACTION_OPEN_HALF_MAPS,
    ACTION_OPEN_LATEST_REFINE,
    ACTION_SHOW,
    COMMAND_METADATA,
    compute_action_availability,
)


class FakeCmdDesc:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _FakeSettingsBase:
    def __init__(self, *_args, **_kwargs):
        for key, value in getattr(type(self), "AUTO_SAVE", {}).items():
            setattr(self, key, value)


class _FakeLogger:
    def __init__(self):
        self.infos: list[str] = []
        self.warnings: list[str] = []
        self.errors: list[str] = []

    def info(self, message: str):
        self.infos.append(message)

    def warning(self, message: str):
        self.warnings.append(message)

    def error(self, message: str):
        self.errors.append(message)


def _install_fake_runtime(monkeypatch, tmp_path):
    calls = []
    commands_module = types.ModuleType("chimerax.core.commands")
    commands_module.CmdDesc = FakeCmdDesc
    commands_module.StringArg = object
    commands_module.IntArg = object
    commands_module.run = lambda *_args, **_kwargs: None

    def fake_register(name, desc, func, logger=None):
        calls.append((name, desc, func, logger))

    commands_module.register = fake_register
    configfile_module = types.ModuleType("chimerax.core.configfile")
    configfile_module.Value = lambda default, *_args, **_kwargs: default
    settings_module = types.ModuleType("chimerax.core.settings")
    settings_module.Settings = _FakeSettingsBase
    errors_module = types.ModuleType("chimerax.core.errors")
    errors_module.UserError = RuntimeError
    core_module = types.ModuleType("chimerax.core")
    core_module.commands = commands_module
    core_module.configfile = configfile_module
    core_module.settings = settings_module
    core_module.errors = errors_module
    chimerax_module = types.ModuleType("chimerax")
    chimerax_module.core = core_module
    chimerax_module.app_dirs = SimpleNamespace(user_cache_dir=str(tmp_path / "cache-home"))

    monkeypatch.setitem(sys.modules, "chimerax", chimerax_module)
    monkeypatch.setitem(sys.modules, "chimerax.core", core_module)
    monkeypatch.setitem(sys.modules, "chimerax.core.commands", commands_module)
    monkeypatch.setitem(sys.modules, "chimerax.core.configfile", configfile_module)
    monkeypatch.setitem(sys.modules, "chimerax.core.settings", settings_module)
    monkeypatch.setitem(sys.modules, "chimerax.core.errors", errors_module)
    return calls


def _import_cmd_module(monkeypatch, tmp_path):
    calls = _install_fake_runtime(monkeypatch, tmp_path)
    for name in (
        "cryoremote_bundle.settings",
        "cryoremote_bundle.session_ops",
        "cryoremote_bundle.controller",
        "cryoremote_bundle.cmd",
    ):
        sys.modules.pop(name, None)
    return importlib.import_module("cryoremote_bundle.cmd"), calls


def test_compute_action_availability_requires_project_for_latest_refine():
    availability = compute_action_availability(
        connected=True,
        has_project=False,
        has_job=True,
        has_tree_entry=True,
        is_openable_file=True,
    )

    assert availability[ACTION_SHOW] is True
    assert availability[ACTION_OPEN_LATEST_REFINE] is False
    assert availability[ACTION_OPEN_HALF_MAPS] is True


def test_compute_action_availability_disables_all_runtime_actions_when_disconnected():
    availability = compute_action_availability(
        connected=False,
        has_project=True,
        has_job=True,
        has_tree_entry=True,
        is_openable_file=True,
    )

    assert availability[ACTION_SHOW] is True
    assert all(not enabled for action, enabled in availability.items() if action != ACTION_SHOW)


def test_register_command_builds_keyword_descriptor_for_connect(monkeypatch, tmp_path):
    cmd_module, calls = _import_cmd_module(monkeypatch, tmp_path)
    cmd_module._REGISTERED_COMMANDS.clear()

    cmd_module.register_command("cryoremote connect", logger="logger")

    assert len(calls) == 1
    name, desc, _func, logger = calls[0]
    assert name == "cryoremote connect"
    assert desc.kwargs["synopsis"] == COMMAND_METADATA["cryoremote connect"]
    assert [item[0] for item in desc.kwargs["keyword"]] == ["alias", "host", "user", "port", "root"]
    assert logger == "logger"


def test_register_command_builds_required_descriptor_for_preview_path(monkeypatch, tmp_path):
    cmd_module, calls = _import_cmd_module(monkeypatch, tmp_path)
    cmd_module._REGISTERED_COMMANDS.clear()

    cmd_module.register_command("cryoremote preview path", logger="logger")

    assert len(calls) == 1
    name, desc, _func, _logger = calls[0]
    assert name == "cryoremote preview path"
    assert desc.kwargs["synopsis"] == COMMAND_METADATA["cryoremote preview path"]
    assert desc.kwargs["required"] == [("target", object)]


def test_status_browse_and_preview_commands_emit_stable_text(monkeypatch, tmp_path):
    cmd_module, _calls = _import_cmd_module(monkeypatch, tmp_path)
    session = SimpleNamespace(logger=_FakeLogger())

    fake_controller = SimpleNamespace(
        status_snapshot=lambda: SimpleNamespace(
            connected=True,
            alias="gm00",
            hostname="gm00.example",
            user="shark",
            port=22,
            root=PurePosixPath("/remote/project"),
            project_root=PurePosixPath("/remote/project"),
            project_source="pipeline",
            jobs=1,
        ),
        browse=lambda _path: SimpleNamespace(
            root_entry=SimpleNamespace(path=PurePosixPath("/remote/project")),
            entries=(
                SimpleNamespace(
                    entry_type="directory",
                    path=PurePosixPath("/remote/project/Refine3D"),
                    size=None,
                    mtime=None,
                ),
                SimpleNamespace(
                    entry_type="file",
                    path=PurePosixPath("/remote/project/default_pipeline.star"),
                    size=128,
                    mtime=1.0,
                ),
            ),
        ),
        refresh_current_root=lambda: SimpleNamespace(
            root_entry=SimpleNamespace(path=PurePosixPath("/remote/project")),
            entries=(),
        ),
        preview_path=lambda _target: SimpleNamespace(
            entry=SimpleNamespace(path=PurePosixPath("/remote/project/run.out")),
            preview=SimpleNamespace(
                title="run.out",
                is_text=True,
                body="Path: /remote/project/run.out\n\nfinished",
                related_files=(PurePosixPath("/remote/project/postprocess.mrc"),),
                notes=("Half maps are available.",),
            ),
        ),
    )
    monkeypatch.setattr(cmd_module, "_controller", lambda _session: fake_controller)

    status_text = cmd_module._run_status(session)
    browse_text = cmd_module._run_browse(session, path=".")
    preview_text = cmd_module._run_preview_path(session, "run.out")

    assert status_text.splitlines() == [
        "connected: true",
        "alias: gm00",
        "hostname: gm00.example",
        "user: shark",
        "port: 22",
        "root: /remote/project",
        "project_root: /remote/project",
        "project_source: pipeline",
        "jobs: 1",
    ]
    assert browse_text.splitlines() == [
        "path: /remote/project",
        "entries: 2",
        "entry\tdirectory\t/remote/project/Refine3D\t-\t-",
        "entry\tfile\t/remote/project/default_pipeline.star\t128\t1970-01-01T00:00:01+00:00",
    ]
    assert preview_text.splitlines() == [
        "title: run.out",
        "path: /remote/project/run.out",
        "is_text: true",
        "--- body ---",
        "Path: /remote/project/run.out",
        "",
        "finished",
        "--- related ---",
        "/remote/project/postprocess.mrc",
        "--- notes ---",
        "Half maps are available.",
    ]
    assert session.logger.infos == [status_text, browse_text, preview_text]


def test_connect_command_reports_non_interactive_auth_failure(monkeypatch, tmp_path):
    cmd_module, _calls = _import_cmd_module(monkeypatch, tmp_path)
    session = SimpleNamespace(logger=_FakeLogger())

    monkeypatch.setattr(cmd_module, "normalize_connection_overrides", lambda *args, **kwargs: (None, None, None))
    monkeypatch.setattr(
        cmd_module,
        "resolve_host",
        lambda *args, **kwargs: SimpleNamespace(alias="gm00", hostname="gm00.example", user="shark", port=22, root="/"),
    )

    class _FakeController:
        def connect(self, _config):
            raise cmd_module.SFTPConnectionError("Authentication failed.")

    monkeypatch.setattr(cmd_module, "_controller", lambda _session: _FakeController())

    with pytest.raises(RuntimeError, match="Non-interactive SSH authentication failed"):
        cmd_module._run_connect(session, alias="gm00")


def test_run_provider_maps_toolbar_button_to_action(monkeypatch, tmp_path):
    cmd_module, _calls = _import_cmd_module(monkeypatch, tmp_path)
    dispatched = []
    monkeypatch.setattr(
        cmd_module,
        "_dispatch_toolbar_action",
        lambda session, action_id, display_name=None: dispatched.append((session, action_id, display_name)),
    )

    cmd_module.run_provider("session", "button-open-half-maps", display_name="Open Half Maps")

    assert dispatched == [("session", ACTION_OPEN_HALF_MAPS, "Open Half Maps")]


def test_run_provider_rejects_unknown_toolbar_button(monkeypatch, tmp_path):
    cmd_module, _calls = _import_cmd_module(monkeypatch, tmp_path)

    with pytest.raises(ValueError, match="Unsupported CryoRemote toolbar provider"):
        cmd_module.run_provider("session", "button-not-real")
