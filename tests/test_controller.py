from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

from cryoremote_bundle.cxc import NestedCommandFileError
from cryoremote_bundle.models import RemoteEntry, ResolvedHostConfig


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


class _FakeRemoteFS:
    def __init__(self):
        self._entries: dict[str, RemoteEntry] = {}
        self._children: dict[str, list[RemoteEntry]] = {}
        self._payloads: dict[str, bytes] = {}
        self.closed = False

    def add_dir(self, path: str, *, mtime: float = 1.0):
        target = PurePosixPath(path)
        if str(target) in self._entries:
            return
        self._entries[str(target)] = RemoteEntry(target, "directory", size=None, mtime=mtime)
        self._children.setdefault(str(target), [])
        if target.parent != target:
            self.add_dir(str(target.parent), mtime=mtime)
            self._children.setdefault(str(target.parent), []).append(self._entries[str(target)])

    def add_file(self, path: str, payload: bytes | str, *, mtime: float = 1.0):
        target = PurePosixPath(path)
        self.add_dir(str(target.parent), mtime=mtime)
        data = payload.encode("utf-8") if isinstance(payload, str) else payload
        entry = RemoteEntry(target, "file", size=len(data), mtime=mtime)
        self._entries[str(target)] = entry
        self._payloads[str(target)] = data
        self._children.setdefault(str(target.parent), []).append(entry)

    def connect(self):
        return None

    def close(self):
        self.closed = True

    def info(self, path: str | PurePosixPath) -> RemoteEntry:
        target = PurePosixPath(str(path))
        return self._entries[str(target)]

    def ls(self, path: str | PurePosixPath) -> list[RemoteEntry]:
        target = PurePosixPath(str(path))
        return sorted(
            self._children.get(str(target), []),
            key=lambda item: (item.entry_type != "directory", item.name.lower()),
        )

    def read_bytes_head(self, path: str | PurePosixPath, limit: int = 65536) -> bytes:
        target = PurePosixPath(str(path))
        return self._payloads[str(target)][:limit]

    def read_text_head(self, path: str | PurePosixPath, limit: int = 65536, encoding: str = "utf-8") -> str:
        return self.read_bytes_head(path, limit=limit).decode(encoding, errors="replace")

    def get_file(self, remote_path: str | PurePosixPath, local_path: Path) -> None:
        target = PurePosixPath(str(remote_path))
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(self._payloads[str(target)])


def _install_fake_chimerax(monkeypatch, tmp_path):
    commands_module = types.ModuleType("chimerax.core.commands")
    commands_module.run = lambda *_args, **_kwargs: None
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


def _import_controller_module(monkeypatch, tmp_path):
    _install_fake_chimerax(monkeypatch, tmp_path)
    for name in (
        "cryoremote_bundle.settings",
        "cryoremote_bundle.session_ops",
        "cryoremote_bundle.controller",
    ):
        sys.modules.pop(name, None)
    return importlib.import_module("cryoremote_bundle.controller")


def _make_remote_fs() -> _FakeRemoteFS:
    fs = _FakeRemoteFS()
    fs.add_file(
        "/remote/project/default_pipeline.star",
        """
data_pipeline_processes
loop_
_rlnPipeLineProcessName #1
_rlnPipeLineProcessTypeLabel #2
_rlnPipeLineProcessStatusLabel #3
Refine3D/job001/ relion.refine3d succeeded

data_pipeline_input_edges
loop_
_rlnPipeLineEdgeProcess #1
_rlnPipeLineEdgeFromNode #2

data_pipeline_output_edges
loop_
_rlnPipeLineEdgeProcess #1
_rlnPipeLineEdgeToNode #2
Refine3D/job001/ Refine3D/job001/postprocess.mrc
""".strip()
        + "\n",
    )
    fs.add_file("/remote/project/Refine3D/job001/postprocess.mrc", b"map", mtime=2.0)
    fs.add_file("/remote/project/Refine3D/job001/model.cif", "data_model\n", mtime=2.0)
    fs.add_file("/remote/project/Refine3D/job001/run_half1_class001_unfil.mrc", b"half1", mtime=2.0)
    fs.add_file("/remote/project/Refine3D/job001/run_half2_class001_unfil.mrc", b"half2", mtime=2.0)
    fs.add_file("/remote/project/Refine3D/job001/run.out", "finished\n", mtime=2.0)
    fs.add_file("/remote/project/scripts/open_maps.cxc", 'open ../Refine3D/job001/postprocess.mrc\n', mtime=3.0)
    fs.add_file("/remote/project/scripts/nested.cxc", 'open ../scripts/other.cxc\n', mtime=3.0)
    fs.add_file("/remote/project/scripts/other.cxc", "open ../Refine3D/job001/postprocess.mrc\n", mtime=3.0)
    return fs


def _make_session():
    return SimpleNamespace(logger=_FakeLogger(), ui=SimpleNamespace(is_gui=False))


def _make_config(root: str = "/remote/project") -> ResolvedHostConfig:
    return ResolvedHostConfig(
        alias="gm00",
        hostname="gm00.example",
        user="shark",
        port=22,
        root=PurePosixPath(root),
    )


def test_controller_connect_and_disconnect_use_session_scoped_state(monkeypatch, tmp_path):
    controller_module = _import_controller_module(monkeypatch, tmp_path)
    remote_fs = _make_remote_fs()
    created: list[_FakeRemoteFS] = []

    class _Factory(_FakeRemoteFS):
        def __init__(self, config, *, password=None, prompt_handler=None):
            super().__init__()
            self.__dict__.update(remote_fs.__dict__)
            self.config = config
            self.password = password
            self.prompt_handler = prompt_handler
            created.append(self)

    monkeypatch.setattr(controller_module, "ParamikoSFTPFileSystem", _Factory)
    session = _make_session()
    controller = controller_module.CryoRemoteController.get_for_session(session)
    assert controller is not None

    result = controller.connect(_make_config())

    assert result.root_entry.path == PurePosixPath("/remote/project")
    assert controller.status_snapshot().connected is True
    assert controller.project_index is not None
    assert controller.active_project_root == PurePosixPath("/remote/project")

    controller.disconnect()

    assert controller.status_snapshot().connected is False
    assert created[0].closed is True


def test_controller_resolves_relative_paths_and_current_project_commands(monkeypatch, tmp_path):
    controller_module = _import_controller_module(monkeypatch, tmp_path)
    session = _make_session()
    controller = controller_module.CryoRemoteController(session)
    remote_fs = _make_remote_fs()
    controller.attach_connected_fs(remote_fs, _make_config())

    browse = controller.browse("Refine3D/job001")
    assert browse.root_entry.path == PurePosixPath("/remote/project/Refine3D/job001")

    preview = controller.preview_path("run.out")
    assert preview.entry.path == PurePosixPath("/remote/project/Refine3D/job001/run.out")
    assert PurePosixPath("/remote/project/Refine3D/job001/postprocess.mrc") in preview.preview.related_files

    opened_artifacts: list[tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]] = []
    opened_half_maps: list[tuple[str, str]] = []

    monkeypatch.setattr(
        controller_module,
        "open_artifacts",
        lambda _session, *, map_paths=(), model_paths=(), hidden_map_paths=(): opened_artifacts.append(
            (
                tuple(str(path) for path in map_paths),
                tuple(str(path) for path in model_paths),
                tuple(str(path) for path in hidden_map_paths),
            )
        ),
    )
    monkeypatch.setattr(
        controller_module,
        "open_half_maps",
        lambda _session, first, second: opened_half_maps.append((str(first), str(second))),
    )

    direct_open = controller.open_path("/remote/project/Refine3D/job001/postprocess.mrc")
    latest = controller.open_latest_refine()
    last_completed = controller.open_last_completed()
    postprocess = controller.open_postprocess_model()
    half_maps = controller.open_half_maps()
    refreshed = controller.refresh_pipeline()

    assert direct_open.remote_paths == (PurePosixPath("/remote/project/Refine3D/job001/postprocess.mrc"),)
    assert latest.remote_paths[0] == PurePosixPath("/remote/project/Refine3D/job001/postprocess.mrc")
    assert last_completed.remote_paths[0] == PurePosixPath("/remote/project/Refine3D/job001/postprocess.mrc")
    assert postprocess.remote_paths[0] == PurePosixPath("/remote/project/Refine3D/job001/postprocess.mrc")
    assert half_maps.remote_paths == (
        PurePosixPath("/remote/project/Refine3D/job001/run_half1_class001_unfil.mrc"),
        PurePosixPath("/remote/project/Refine3D/job001/run_half2_class001_unfil.mrc"),
    )
    assert refreshed.jobs[0].job_id == "Refine3D/job001/"
    assert len(opened_artifacts) == 4
    assert len(opened_half_maps) == 1


def test_controller_open_path_executes_remote_cxc_and_rejects_nested_cxc(monkeypatch, tmp_path):
    controller_module = _import_controller_module(monkeypatch, tmp_path)
    session = _make_session()
    controller = controller_module.CryoRemoteController(session)
    remote_fs = _make_remote_fs()
    controller.attach_connected_fs(remote_fs, _make_config())

    run_calls: list[tuple[str, bool]] = []

    def fake_run_command_file(_session, path: Path):
        run_calls.append((path.read_text(encoding="utf-8"), path.exists()))

    monkeypatch.setattr(controller_module, "run_command_file", fake_run_command_file)

    result = controller.open_path("scripts/open_maps.cxc")

    assert result.opened_command_file is True
    assert result.remote_paths == (PurePosixPath("/remote/project/scripts/open_maps.cxc"),)
    assert len(run_calls) == 1
    assert "postprocess.mrc" in run_calls[0][0]
    assert run_calls[0][1] is True

    with pytest.raises(NestedCommandFileError, match="Nested .cxc open is not supported"):
        controller.open_path("scripts/nested.cxc")
