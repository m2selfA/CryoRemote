from __future__ import annotations

from .actions import (
    ACTION_CLEAR_CACHE,
    ACTION_FIND_IN_TREE,
    ACTION_OPEN_HALF_MAPS,
    ACTION_OPEN_LAST_COMPLETED,
    ACTION_OPEN_LATEST_REFINE,
    ACTION_OPEN_POSTPROCESS_MODEL,
    ACTION_REFRESH,
    ACTION_REFRESH_PIPELINE,
    ACTION_SHOW,
    COMMAND_METADATA,
    COMMAND_TO_ACTION,
    PROVIDER_TO_ACTION,
)
from .controller import (
    CryoRemoteController,
    format_browse_lines,
    format_open_lines,
    format_preview_lines,
    format_status_lines,
)
from .sftp_fs import SFTPConnectionError
from .ssh_config import normalize_connection_overrides, resolve_host


_REGISTERED_COMMANDS: set[str] = set()
_GUI_ONLY_COMMANDS = {"cryoremote show", "cryoremote find in-tree"}
_CONTROLLER_ACTIONS = {
    ACTION_REFRESH: lambda controller: controller.refresh_current_root(),
    ACTION_REFRESH_PIPELINE: lambda controller: controller.refresh_pipeline(),
    ACTION_OPEN_LATEST_REFINE: lambda controller: controller.open_latest_refine(),
    ACTION_OPEN_LAST_COMPLETED: lambda controller: controller.open_last_completed(),
    ACTION_OPEN_HALF_MAPS: lambda controller: controller.open_half_maps(),
    ACTION_OPEN_POSTPROCESS_MODEL: lambda controller: controller.open_postprocess_model(),
    ACTION_CLEAR_CACHE: lambda controller: controller.clear_cache(),
}


def register_command(command_name: str, logger):
    if command_name in _REGISTERED_COMMANDS:
        return
    if command_name not in COMMAND_METADATA:
        return

    from chimerax.core import commands as commands_module

    desc = _command_desc(command_name, commands_module)
    runner = _command_runner(command_name)
    commands_module.register(command_name, desc, runner, logger=logger)
    _REGISTERED_COMMANDS.add(command_name)


def run_provider(session, provider_name: str, display_name: str | None = None):
    try:
        action_id = PROVIDER_TO_ACTION[provider_name]
    except KeyError as exc:
        raise ValueError(f"Unsupported CryoRemote toolbar provider: {provider_name}") from exc
    return _dispatch_toolbar_action(session, action_id, display_name=display_name)


def _command_desc(command_name: str, commands_module):
    CmdDesc = commands_module.CmdDesc
    StringArg = getattr(commands_module, "StringArg", object)
    IntArg = getattr(commands_module, "IntArg", object)
    synopsis = COMMAND_METADATA[command_name]

    if command_name == "cryoremote connect":
        return CmdDesc(
            keyword=[
                ("alias", StringArg),
                ("host", StringArg),
                ("user", StringArg),
                ("port", IntArg),
                ("root", StringArg),
            ],
            synopsis=synopsis,
        )
    if command_name == "cryoremote browse":
        return CmdDesc(
            keyword=[("path", StringArg)],
            synopsis=synopsis,
        )
    if command_name in {"cryoremote preview path", "cryoremote open path"}:
        return CmdDesc(
            required=[("target", StringArg)],
            synopsis=synopsis,
        )
    return CmdDesc(synopsis=synopsis)


def _command_runner(command_name: str):
    if command_name == "cryoremote connect":
        return _run_connect
    if command_name == "cryoremote disconnect":
        return _run_disconnect
    if command_name == "cryoremote status":
        return _run_status
    if command_name == "cryoremote browse":
        return _run_browse
    if command_name == "cryoremote preview path":
        return _run_preview_path
    if command_name == "cryoremote open path":
        return _run_open_path
    if command_name in _GUI_ONLY_COMMANDS:
        action_id = COMMAND_TO_ACTION[command_name]

        def _runner(session, *, _action_id=action_id):
            return _dispatch_gui_command(session, _action_id)

        return _runner
    action_id = COMMAND_TO_ACTION[command_name]

    def _runner(session, *, _action_id=action_id):
        return _dispatch_controller_action(session, _action_id)

    return _runner


def _run_connect(session, alias=None, host=None, user=None, port=None, root=None):
    alias_text = (alias or "").strip()
    host_text = (host or "").strip()
    user_text = (user or "").strip()
    root_text = (root or "/").strip() or "/"
    if not alias_text and not host_text:
        raise _user_error("CryoRemote connect requires either alias or host.")

    try:
        host_override, user_override, port_override = normalize_connection_overrides(
            alias_text,
            host_input=host_text,
            user_input=user_text,
            port_input=port,
        )
        config = resolve_host(
            alias_text,
            root=root_text,
            host_override=host_override,
            user_override=user_override,
            port_override=port_override,
        )
        controller = _controller(session)
        for warning in getattr(config, "warnings", ()):
            _warn(session, warning)
        controller.connect(config)
    except SFTPConnectionError as exc:
        raise _user_error(_connect_error_text(exc)) from exc
    except RuntimeError as exc:
        raise _user_error(str(exc)) from exc
    return _emit_lines(session, format_status_lines(_controller(session).status_snapshot()))


def _run_disconnect(session):
    controller = _controller(session)
    controller.disconnect()
    return _emit_lines(session, format_status_lines(controller.status_snapshot()))


def _run_status(session):
    return _emit_lines(session, format_status_lines(_controller(session).status_snapshot()))


def _run_browse(session, path=None):
    controller = _controller(session)
    try:
        result = controller.refresh_current_root() if path in (None, "") else controller.browse(path)
    except RuntimeError as exc:
        raise _user_error(str(exc)) from exc
    return _emit_lines(session, format_browse_lines(result))


def _run_preview_path(session, target):
    controller = _controller(session)
    try:
        payload = controller.preview_path(target)
    except RuntimeError as exc:
        raise _user_error(str(exc)) from exc
    return _emit_lines(session, format_preview_lines(payload))


def _run_open_path(session, target):
    controller = _controller(session)
    try:
        result = controller.open_path(target)
    except RuntimeError as exc:
        raise _user_error(str(exc)) from exc
    return _emit_lines(session, format_open_lines(result))


def _dispatch_controller_action(session, action_id: str):
    controller = _controller(session)
    try:
        result = _CONTROLLER_ACTIONS[action_id](controller)
    except RuntimeError as exc:
        raise _user_error(str(exc)) from exc

    if action_id == ACTION_REFRESH:
        return _emit_lines(session, format_browse_lines(result))
    if action_id == ACTION_REFRESH_PIPELINE:
        return _emit_lines(session, format_status_lines(controller.status_snapshot()))
    if action_id in {
        ACTION_OPEN_LATEST_REFINE,
        ACTION_OPEN_LAST_COMPLETED,
        ACTION_OPEN_HALF_MAPS,
        ACTION_OPEN_POSTPROCESS_MODEL,
    }:
        return _emit_lines(session, format_open_lines(result))
    if action_id == ACTION_CLEAR_CACHE:
        return _emit_lines(session, ["cache: cleared"])
    return None


def _dispatch_gui_command(session, action_id: str):
    tool = _get_tool(session, create=True, display=True)
    if tool is None:
        if action_id == ACTION_FIND_IN_TREE:
            raise _user_error("cryoremote find in-tree is only available in the ChimeraX GUI.")
        raise _user_error("CryoRemote requires the ChimeraX GUI.")

    if action_id == ACTION_SHOW:
        tool.display(True)
        return tool

    method_name = {
        ACTION_FIND_IN_TREE: "action_find_in_tree",
    }[action_id]
    method = getattr(tool, method_name, None)
    if method is None:
        raise _user_error(f"CryoRemote action is unavailable: {action_id}")
    method()
    return tool


def _dispatch_toolbar_action(session, action_id: str, *, display_name: str | None = None):
    tool = _get_tool(session, create=True, display=True)
    if tool is None:
        _warn(session, "CryoRemote requires the ChimeraX GUI.")
        return None

    if action_id == ACTION_SHOW:
        tool.display(True)
        return tool

    method_name = {
        ACTION_REFRESH: "action_refresh_all",
        ACTION_REFRESH_PIPELINE: "action_refresh_pipeline",
        ACTION_OPEN_LATEST_REFINE: "action_open_latest_refine",
        ACTION_OPEN_LAST_COMPLETED: "action_open_last_completed",
        ACTION_OPEN_HALF_MAPS: "action_open_half_maps",
        ACTION_OPEN_POSTPROCESS_MODEL: "action_open_postprocess_model",
        ACTION_FIND_IN_TREE: "action_find_in_tree",
        ACTION_CLEAR_CACHE: "action_clear_cache",
    }[action_id]
    method = getattr(tool, method_name, None)
    if method is None:
        _warn(session, f"CryoRemote action is unavailable: {display_name or action_id}")
        return tool
    method()
    return tool


def _controller(session) -> CryoRemoteController:
    controller = CryoRemoteController.get_for_session(session, create=True)
    assert controller is not None
    return controller


def _connect_error_text(exc: Exception) -> str:
    message = str(exc)
    if message == "Authentication failed.":
        return "Non-interactive SSH authentication failed; configure SSH key/agent first or use the GUI connection flow."
    return message


def _emit_lines(session, lines: list[str]):
    text = "\n".join(lines)
    logger = getattr(session, "logger", None)
    if logger is not None and hasattr(logger, "info"):
        logger.info(text)
    return text


def _get_tool(session, *, create: bool, display: bool):
    if not getattr(getattr(session, "ui", None), "is_gui", False):
        return None
    from .tool import CryoRemoteTool

    return CryoRemoteTool.get_singleton(session, create=create, display=display)


def _user_error(message: str) -> Exception:
    try:
        from chimerax.core.errors import UserError
    except Exception:
        return RuntimeError(message)
    return UserError(message)


def _warn(session, message: str):
    logger = getattr(session, "logger", None)
    if logger is not None and hasattr(logger, "warning"):
        logger.warning(message)
