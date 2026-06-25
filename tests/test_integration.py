from __future__ import annotations

import sys
import types

import pytest

from cryoremote_bundle.actions import (
    ACTION_OPEN_HALF_MAPS,
    ACTION_OPEN_LATEST_REFINE,
    ACTION_SHOW,
    COMMAND_METADATA,
    compute_action_availability,
)
from cryoremote_bundle import cmd as cmd_module


class FakeCmdDesc:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def _install_fake_command_module(monkeypatch):
    calls = []
    commands_module = types.ModuleType("chimerax.core.commands")
    commands_module.CmdDesc = FakeCmdDesc

    def fake_register(name, desc, func, logger=None):
        calls.append((name, desc, func, logger))

    commands_module.register = fake_register
    core_module = types.ModuleType("chimerax.core")
    core_module.commands = commands_module
    chimerax_module = types.ModuleType("chimerax")
    chimerax_module.core = core_module

    monkeypatch.setitem(sys.modules, "chimerax", chimerax_module)
    monkeypatch.setitem(sys.modules, "chimerax.core", core_module)
    monkeypatch.setitem(sys.modules, "chimerax.core.commands", commands_module)
    return calls


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


def test_register_command_registers_and_dispatches_action(monkeypatch):
    calls = _install_fake_command_module(monkeypatch)
    dispatched = []
    monkeypatch.setattr(
        cmd_module,
        "_dispatch_action",
        lambda session, action_id, display_name=None: dispatched.append((session, action_id, display_name)),
    )
    cmd_module._REGISTERED_COMMANDS.clear()

    cmd_module.register_command("cryoremote show", logger="logger")

    assert len(calls) == 1
    name, desc, func, logger = calls[0]
    assert name == "cryoremote show"
    assert desc.kwargs["synopsis"] == COMMAND_METADATA["cryoremote show"]
    assert logger == "logger"

    func("session")

    assert dispatched == [("session", ACTION_SHOW, None)]


def test_register_command_ignores_duplicates(monkeypatch):
    calls = _install_fake_command_module(monkeypatch)
    cmd_module._REGISTERED_COMMANDS.clear()

    cmd_module.register_command("cryoremote show", logger="logger")
    cmd_module.register_command("cryoremote show", logger="logger")

    assert len(calls) == 1


def test_run_provider_maps_toolbar_button_to_action(monkeypatch):
    dispatched = []
    monkeypatch.setattr(
        cmd_module,
        "_dispatch_action",
        lambda session, action_id, display_name=None: dispatched.append((session, action_id, display_name)),
    )

    cmd_module.run_provider("session", "button-open-half-maps", display_name="Open Half Maps")

    assert dispatched == [("session", ACTION_OPEN_HALF_MAPS, "Open Half Maps")]


def test_run_provider_rejects_unknown_toolbar_button():
    with pytest.raises(ValueError, match="Unsupported CryoRemote toolbar provider"):
        cmd_module.run_provider("session", "button-not-real")
