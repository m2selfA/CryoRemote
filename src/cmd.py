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


_REGISTERED_COMMANDS: set[str] = set()
_ACTION_METHODS = {
    ACTION_REFRESH: "action_refresh_all",
    ACTION_REFRESH_PIPELINE: "action_refresh_pipeline",
    ACTION_OPEN_LATEST_REFINE: "action_open_latest_refine",
    ACTION_OPEN_LAST_COMPLETED: "action_open_last_completed",
    ACTION_OPEN_HALF_MAPS: "action_open_half_maps",
    ACTION_OPEN_POSTPROCESS_MODEL: "action_open_postprocess_model",
    ACTION_FIND_IN_TREE: "action_find_in_tree",
    ACTION_CLEAR_CACHE: "action_clear_cache",
}


def register_command(command_name: str, logger):
    if command_name not in COMMAND_TO_ACTION or command_name in _REGISTERED_COMMANDS:
        return

    from chimerax.core.commands import CmdDesc, register

    action_id = COMMAND_TO_ACTION[command_name]
    synopsis = COMMAND_METADATA[command_name]
    desc = CmdDesc(synopsis=synopsis)

    def _runner(session, *, _action_id=action_id):
        _dispatch_action(session, _action_id)

    register(command_name, desc, _runner, logger=logger)
    _REGISTERED_COMMANDS.add(command_name)


def run_provider(session, provider_name: str, display_name: str | None = None):
    try:
        action_id = PROVIDER_TO_ACTION[provider_name]
    except KeyError as exc:
        raise ValueError(f"Unsupported CryoRemote toolbar provider: {provider_name}") from exc
    return _dispatch_action(session, action_id, display_name=display_name)


def _dispatch_action(session, action_id: str, *, display_name: str | None = None):
    tool = _get_tool(session, create=True, display=True)
    if tool is None:
        _warn(session, "CryoRemote requires the ChimeraX GUI.")
        return None

    if action_id == ACTION_SHOW:
        tool.display(True)
        return tool

    method_name = _ACTION_METHODS[action_id]
    method = getattr(tool, method_name, None)
    if method is None:
        _warn(session, f"CryoRemote action is unavailable: {display_name or action_id}")
        return tool
    method()
    return tool


def _get_tool(session, *, create: bool, display: bool):
    if not getattr(getattr(session, "ui", None), "is_gui", False):
        return None
    from .tool import CryoRemoteTool

    return CryoRemoteTool.get_singleton(session, create=create, display=display)


def _warn(session, message: str):
    logger = getattr(session, "logger", None)
    if logger is not None and hasattr(logger, "warning"):
        logger.warning(message)
