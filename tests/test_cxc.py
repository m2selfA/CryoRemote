from __future__ import annotations

import importlib
import sys
import types
from pathlib import Path, PurePosixPath

import pytest

from cryoremote_bundle.cxc import NestedCommandFileError, rewrite_command_file_text
from cryoremote_bundle.models import RemoteEntry
from cryoremote_bundle.opening import is_command_file_path, is_openable_path
from cryoremote_bundle.preview import is_text_previewable, preview_for_text


def test_cxc_files_are_previewable_and_openable():
    path = PurePosixPath("/remote/project/open_maps.cxc")

    assert is_command_file_path(path) is True
    assert is_openable_path(path) is True
    assert is_text_previewable(path) is True


def test_preview_for_cxc_warns_about_execution():
    entry = RemoteEntry(PurePosixPath("/remote/project/open_maps.cxc"), "file", size=12, mtime=1.0)

    preview = preview_for_text(entry, b"open map.mrc\n")

    assert "opening this file will execute its commands" in preview.body
    assert "open map.mrc" in preview.body


def test_rewrite_command_file_rewrites_relative_and_absolute_open_targets(tmp_path):
    calls: list[PurePosixPath] = []
    local_root = tmp_path / "cache"
    local_root.mkdir()

    def rewrite_remote_target(remote_path: PurePosixPath) -> Path:
        calls.append(remote_path)
        target = local_root / remote_path.name
        target.write_text("cached", encoding="utf-8")
        return target

    script = 'open "maps/half1.mrc"\nopen /share/models/model.cif name refined\n'

    rewritten = rewrite_command_file_text(
        script,
        PurePosixPath("/share/project/scripts/open_maps.cxc"),
        rewrite_remote_target,
    )

    assert calls == [
        PurePosixPath("/share/project/scripts/maps/half1.mrc"),
        PurePosixPath("/share/models/model.cif"),
    ]
    assert f'open "{(local_root / "half1.mrc").as_posix()}"' in rewritten
    assert f'open "{(local_root / "model.cif").as_posix()}" name refined' in rewritten


def test_rewrite_command_file_stops_rewriting_after_first_non_path_token(tmp_path):
    calls: list[PurePosixPath] = []

    def rewrite_remote_target(remote_path: PurePosixPath) -> Path:
        calls.append(remote_path)
        return tmp_path / remote_path.name

    rewritten = rewrite_command_file_text(
        "open map.mrc name foo.map\n",
        PurePosixPath("/share/project/open_maps.cxc"),
        rewrite_remote_target,
    )

    assert calls == [PurePosixPath("/share/project/map.mrc")]
    assert "name foo.map" in rewritten


def test_rewrite_command_file_rejects_nested_command_files(tmp_path):
    def rewrite_remote_target(remote_path: PurePosixPath) -> Path:  # pragma: no cover - not reached
        return tmp_path / remote_path.name

    with pytest.raises(NestedCommandFileError, match="Nested .cxc open is not supported"):
        rewrite_command_file_text(
            "open scripts/other.cxc\n",
            PurePosixPath("/share/project/open_maps.cxc"),
            rewrite_remote_target,
        )


def test_rewrite_command_file_preserves_semicolon_comments(tmp_path):
    def rewrite_remote_target(remote_path: PurePosixPath) -> Path:
        return tmp_path / remote_path.name

    rewritten = rewrite_command_file_text(
        "open map.mrc ; # comment stays here\n",
        PurePosixPath("/share/project/open_maps.cxc"),
        rewrite_remote_target,
    )

    assert rewritten.endswith("; # comment stays here\n")


def test_run_command_file_uses_chimerax_open_command(monkeypatch, tmp_path):
    calls: list[tuple[object, str]] = []
    commands_module = types.ModuleType("chimerax.core.commands")
    commands_module.run = lambda session, command: calls.append((session, command))
    core_module = types.ModuleType("chimerax.core")
    core_module.commands = commands_module
    chimerax_module = types.ModuleType("chimerax")
    chimerax_module.core = core_module

    monkeypatch.setitem(sys.modules, "chimerax", chimerax_module)
    monkeypatch.setitem(sys.modules, "chimerax.core", core_module)
    monkeypatch.setitem(sys.modules, "chimerax.core.commands", commands_module)
    sys.modules.pop("cryoremote_bundle.session_ops", None)

    session_ops = importlib.import_module("cryoremote_bundle.session_ops")
    session_ops.run_command_file("session", tmp_path / "open_maps.cxc")

    assert calls == [("session", f'open "{(tmp_path / "open_maps.cxc").as_posix()}"')]
