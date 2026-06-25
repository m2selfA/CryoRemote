from __future__ import annotations

from dataclasses import dataclass


TOOL_NAME = "CryoRemote"
TOOL_CATEGORY = "Volume Data"
TOOLBAR_TAB = "CryoRemote"

ACTION_SHOW = "show"
ACTION_REFRESH = "refresh"
ACTION_REFRESH_PIPELINE = "refresh-pipeline"
ACTION_OPEN_LATEST_REFINE = "open-latest-refine"
ACTION_OPEN_LAST_COMPLETED = "open-last-completed"
ACTION_OPEN_HALF_MAPS = "open-half-maps"
ACTION_OPEN_POSTPROCESS_MODEL = "open-postprocess-model"
ACTION_FIND_IN_TREE = "find-in-tree"
ACTION_CLEAR_CACHE = "clear-cache"
ACTION_OPEN_SELECTED = "open-selected"
ACTION_REVEAL_RELATED = "reveal-related"


@dataclass(frozen=True)
class ToolbarPlacement:
    section: str
    button: str


COMMAND_METADATA = {
    "cryoremote show": "Show the CryoRemote tool.",
    "cryoremote refresh": "Refresh the CryoRemote tree and project state.",
    "cryoremote refresh pipeline": "Refresh the active RELION pipeline.",
    "cryoremote open latest-refine": "Open the latest RELION refine map.",
    "cryoremote open last-completed": "Open artifacts from the latest completed RELION job.",
    "cryoremote open half-maps": "Open half maps from the current RELION job.",
    "cryoremote open postprocess-model": "Open the current RELION postprocess map and model.",
    "cryoremote find in-tree": "Focus the current RELION job in the remote tree.",
    "cryoremote cache clear": "Clear the CryoRemote local cache.",
}

ACTION_TO_COMMAND = {
    ACTION_SHOW: "cryoremote show",
    ACTION_REFRESH: "cryoremote refresh",
    ACTION_REFRESH_PIPELINE: "cryoremote refresh pipeline",
    ACTION_OPEN_LATEST_REFINE: "cryoremote open latest-refine",
    ACTION_OPEN_LAST_COMPLETED: "cryoremote open last-completed",
    ACTION_OPEN_HALF_MAPS: "cryoremote open half-maps",
    ACTION_OPEN_POSTPROCESS_MODEL: "cryoremote open postprocess-model",
    ACTION_FIND_IN_TREE: "cryoremote find in-tree",
    ACTION_CLEAR_CACHE: "cryoremote cache clear",
}

COMMAND_TO_ACTION = {command_name: action_id for action_id, command_name in ACTION_TO_COMMAND.items()}

PROVIDER_TO_ACTION = {
    "button-show": ACTION_SHOW,
    "button-refresh-all": ACTION_REFRESH,
    "button-refresh-pipeline": ACTION_REFRESH_PIPELINE,
    "button-open-latest-refine": ACTION_OPEN_LATEST_REFINE,
    "button-open-last-completed": ACTION_OPEN_LAST_COMPLETED,
    "button-open-half-maps": ACTION_OPEN_HALF_MAPS,
    "button-open-postprocess-model": ACTION_OPEN_POSTPROCESS_MODEL,
    "button-find-in-tree": ACTION_FIND_IN_TREE,
    "button-clear-cache": ACTION_CLEAR_CACHE,
}

TOOLBAR_BUTTONS = {
    ACTION_SHOW: ToolbarPlacement("Window", "Show CryoRemote"),
    ACTION_REFRESH: ToolbarPlacement("Project", "Refresh All"),
    ACTION_REFRESH_PIPELINE: ToolbarPlacement("Project", "Refresh Pipeline"),
    ACTION_OPEN_LATEST_REFINE: ToolbarPlacement("Project", "Open Latest Refine"),
    ACTION_OPEN_LAST_COMPLETED: ToolbarPlacement("Project", "Open Last Completed"),
    ACTION_OPEN_HALF_MAPS: ToolbarPlacement("Job", "Open Half Maps"),
    ACTION_OPEN_POSTPROCESS_MODEL: ToolbarPlacement("Job", "Open PostProcess + Model"),
    ACTION_FIND_IN_TREE: ToolbarPlacement("Job", "Find In Tree"),
    ACTION_CLEAR_CACHE: ToolbarPlacement("Maintenance", "Clear Cache"),
}


def compute_action_availability(
    *,
    connected: bool,
    has_project: bool,
    has_job: bool,
    has_tree_entry: bool,
    is_openable_file: bool,
) -> dict[str, bool]:
    return {
        ACTION_SHOW: True,
        ACTION_REFRESH: connected,
        ACTION_REFRESH_PIPELINE: connected and has_project,
        ACTION_OPEN_LATEST_REFINE: connected and has_project,
        ACTION_OPEN_LAST_COMPLETED: connected and has_project,
        ACTION_OPEN_HALF_MAPS: connected and has_job,
        ACTION_OPEN_POSTPROCESS_MODEL: connected and has_job,
        ACTION_FIND_IN_TREE: connected and has_job,
        ACTION_CLEAR_CACHE: connected,
        ACTION_OPEN_SELECTED: connected and is_openable_file,
        ACTION_REVEAL_RELATED: connected and (has_job or has_tree_entry),
    }
