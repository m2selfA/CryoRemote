from __future__ import annotations

import tempfile
from pathlib import Path, PurePosixPath
from time import time

from Qt.QtCore import QTimer
from Qt.QtGui import QFont
from Qt.QtWidgets import QVBoxLayout
from chimerax.core.tools import ToolInstance
from chimerax.ui import MainToolWindow

from .actions import (
    ACTION_SHOW,
    TOOL_NAME,
    TOOLBAR_BUTTONS,
    TOOLBAR_TAB,
    compute_action_availability,
)
from .controller import CryoRemoteController
from .location_memory import (
    RememberedTargetState,
    directory_to_remember,
    remembered_targets_from_settings,
    remembered_targets_to_settings,
    root_candidates,
    target_key,
)
from .models import PreviewResult, RelionJobNode, RemoteEntry, ResolvedHostConfig
from .navigation import directory_target_for_entry, normalize_browse_path, session_target_text
from .opening import is_command_file_path, is_model_path, is_openable_path, OPENABLE_SUFFIXES
from .preview import (
    is_mrc_previewable,
    is_text_previewable,
    preview_for_directory,
    preview_for_job,
    preview_for_mrc,
    preview_for_project,
    preview_for_text,
)
from .relion import (
    build_job_node,
    classify_job_type,
    find_project_root,
    latest_completed_job,
    latest_refine_job,
    load_project_index,
)
from .session_ops import open_artifacts
from .settings import CryoRemoteSettings
from .sftp_fs import ParamikoSFTPFileSystem, SFTPConnectionError
from .ssh_config import load_aliases, normalize_connection_overrides, resolve_host
from .ui.assets import load_icon
from .ui.main_widget import InteractivePromptDialog, MainWidget
from .ui.tree_model import RemoteTreeModel


WATCH_INTERVAL_MS = 15000


class CryoRemoteTool(ToolInstance):
    SESSION_ENDURING = False
    SESSION_SAVE = False
    help = "help:user/tools/CryoRemote.html"

    @classmethod
    def get_singleton(cls, session, *, create: bool = True, display: bool = False):
        from chimerax.core.tools import get_singleton

        return get_singleton(session, cls, TOOL_NAME, create=create, display=display)

    def __init__(self, session, tool_name):
        super().__init__(session, tool_name)
        self.display_name = TOOL_NAME
        self.font = QFont("Arial", 8)
        self.settings = CryoRemoteSettings(session, TOOL_NAME, version="2")
        self._controller = CryoRemoteController.get_for_session(session)
        self._fs = self._controller.fs
        self._config = self._controller.config
        self._target_key: str | None = None
        self._project_index = self._controller.project_index
        self._active_project_root = self._controller.active_project_root
        self._cache_manager = self._controller.cache_manager
        self._remembered_targets = remembered_targets_from_settings(self.settings.remembered_targets)

        self.tool_window = MainToolWindow(self, close_destroys=False)
        self._build_ui()
        self._watch_timer = QTimer(self.tool_window.ui_area)
        self._watch_timer.setInterval(WATCH_INTERVAL_MS)
        self._watch_timer.timeout.connect(self._poll_project)
        self._sync_widget_from_controller()
        self._sync_toolbar_state()

    @property
    def _fs(self) -> ParamikoSFTPFileSystem | None:
        return self._controller.fs

    @_fs.setter
    def _fs(self, value: ParamikoSFTPFileSystem | None):
        self._controller.fs = value

    @property
    def _config(self) -> ResolvedHostConfig | None:
        return self._controller.config

    @_config.setter
    def _config(self, value: ResolvedHostConfig | None):
        self._controller.config = value

    @property
    def _project_index(self):
        return self._controller.project_index

    @_project_index.setter
    def _project_index(self, value):
        self._controller.project_index = value

    @property
    def _active_project_root(self) -> PurePosixPath | None:
        return self._controller.active_project_root

    @_active_project_root.setter
    def _active_project_root(self, value: PurePosixPath | None):
        self._controller.active_project_root = value

    @property
    def _cache_manager(self):
        return self._controller.cache_manager

    @_cache_manager.setter
    def _cache_manager(self, value):
        self._controller.cache_manager = value

    def delete(self):
        self._disconnect()
        super().delete()

    def action_show(self):
        self.display(True)
        if self._fs is not None and self._config is not None:
            self._sync_widget_from_controller()
            self._widget.show_browse_page()
        else:
            self._widget.show_connection_page()
        self._sync_toolbar_state()

    def action_refresh_all(self):
        self._refresh_all()

    def action_refresh_pipeline(self):
        self._refresh_project()

    def action_clear_cache(self):
        self._clear_cache()

    def action_open_selected(self):
        self._open_selected()

    def action_open_latest_refine(self):
        self._open_latest_refine()

    def action_open_last_completed(self):
        self._open_last_completed()

    def action_open_half_maps(self):
        self._open_half_maps()

    def action_open_postprocess_model(self):
        self._open_postprocess()

    def action_reveal_related(self):
        self._refresh_current_summary()

    def action_find_in_tree(self):
        self._find_in_tree()

    def _build_ui(self):
        self._layout = QVBoxLayout()
        self._widget = MainWidget(
            aliases=load_aliases(),
            preferred_alias=self.settings.preferred_alias,
            preferred_host=self.settings.preferred_host,
            preferred_user=self.settings.preferred_user,
            preferred_port=self.settings.preferred_port,
            preferred_root=self.settings.preferred_root,
            remembered_roots_by_alias=self._remembered_roots_by_alias(),
        )
        self._layout.addWidget(self._widget)
        self.tool_window.ui_area.setLayout(self._layout)
        icon = load_icon("brand/cryoremote-mark-1024.png")
        if not icon.isNull():
            self.tool_window.ui_area.setWindowIcon(icon)
        self.tool_window.manage(None)

        self._widget.connect_requested.connect(self._connect_requested)
        self._widget.disconnect_requested.connect(self._disconnect)
        self._widget.refresh_requested.connect(self.action_refresh_all)
        self._widget.refresh_pipeline_requested.connect(self.action_refresh_pipeline)
        self._widget.clear_cache_requested.connect(self.action_clear_cache)
        self._widget.preview_requested.connect(self._preview_entry)
        self._widget.pipeline_job_selected.connect(self._pipeline_job_selected)
        self._widget.open_selected_requested.connect(self.action_open_selected)
        self._widget.open_latest_refine_requested.connect(self.action_open_latest_refine)
        self._widget.open_last_completed_requested.connect(self.action_open_last_completed)
        self._widget.open_half_maps_requested.connect(self.action_open_half_maps)
        self._widget.open_postprocess_requested.connect(self.action_open_postprocess_model)
        self._widget.reveal_related_requested.connect(self.action_reveal_related)
        self._widget.find_in_tree_requested.connect(self.action_find_in_tree)
        self._widget.browse_path_requested.connect(self._browse_path_requested)
        self._widget.browse_up_requested.connect(self._browse_up_requested)
        self._widget.set_directory_requested.connect(self._set_directory_requested)
        self._widget.connection_page_requested.connect(self._widget.show_connection_page)

    def _connect_requested(self, payload: dict):
        alias = (payload.get("alias") or "").strip()
        port_text = (payload.get("port") or "").strip()
        root = (payload.get("root") or "/").strip() or "/"
        root_source = (payload.get("root_source") or "preferred").strip() or "preferred"
        password = (payload.get("password") or "").strip() or None

        host_text = (payload.get("host") or "").strip()
        user_text = (payload.get("user") or "").strip()

        if not alias and not host_text:
            self.session.logger.error("CryoRemote needs either an SSH alias or a host name.")
            self._widget.show_status("Missing SSH alias or host.", error=True)
            return

        try:
            host_override, user_override, port_override = normalize_connection_overrides(
                alias,
                host_input=host_text,
                user_input=user_text,
                port_input=port_text,
            )
        except ValueError:
            self._widget.show_status(f"Invalid port: {port_text}", error=True)
            return

        resolved_alias = alias or host_text or ""
        config = resolve_host(
            resolved_alias,
            root=root,
            host_override=host_override,
            user_override=user_override,
            port_override=port_override,
        )
        active_target_key = target_key(alias, config)
        remembered = self._remembered_targets.get(active_target_key)

        for warning in config.warnings:
            self.session.logger.warning(warning)

        fs = ParamikoSFTPFileSystem(
            config,
            password=password,
            prompt_handler=lambda title, instructions, prompts: InteractivePromptDialog.prompt(
                self.tool_window.ui_area, title, instructions, prompts
            ),
        )

        try:
            fs.connect()
            root_entry, root_note, root_warning, resolved_root_source = self._resolve_connection_root(
                fs,
                config,
                root_text=root,
                root_source=root_source,
                remembered=remembered,
            )
        except Exception as exc:
            fs.close()
            self.session.logger.error(f"CryoRemote connection failed: {exc}")
            self._widget.show_status(f"Connection failed: {exc}", error=True)
            return

        try:
            self._controller.attach_connected_fs(fs, config)
        except Exception as exc:
            fs.close()
            self.session.logger.error(f"CryoRemote connection failed: {exc}")
            self._widget.show_status(f"Connection failed: {exc}", error=True)
            return
        self._target_key = active_target_key
        self._widget.set_connected(True)
        self._widget.set_root_value(str(config.root), source=resolved_root_source)
        self._save_preferences(payload, config, root_source=root_source)
        self._widget.set_session_target(session_target_text(config))
        self._sync_widget_from_controller(root_entry=root_entry)
        self._widget.show_browse_page()

        message = f"Connected to {config.user or '?'}@{config.hostname}:{config.port}"
        if self._project_index is not None:
            message += " | RELION project loaded"
        elif config.warnings:
            message += " | no RELION project detected yet"
        if root_note:
            message += f" | {root_note}"
        self._widget.show_status(message, warning=self._project_index is None or root_warning)
        self._sync_toolbar_state()

    def _disconnect(self):
        self._watch_timer.stop()
        self._controller.disconnect()
        self._target_key = None
        self._widget.clear_project()
        self._widget.clear_model()
        self._widget.set_connected(False)
        self._widget.set_session_target("Not connected")
        self._widget.set_current_path("/")
        self._widget.show_connection_page()
        self._widget.show_status("Disconnected.")
        self._sync_toolbar_state()

    def _refresh_all(self):
        if self._fs is None or self._config is None:
            self._widget.show_status("CryoRemote is not connected.", warning=True)
            return
        try:
            self._sync_browse_result(self._controller.refresh_current_root())
        except Exception as exc:
            self.session.logger.warning(f"CryoRemote could not refresh {self._config.root}: {exc}")
            self._widget.show_status(f"Refresh failed: {exc}", warning=True)
            return
        self._widget.show_status("Tree and project refreshed.")
        self._sync_toolbar_state()

    def _refresh_project(self):
        if self._active_project_root is None:
            self._widget.show_status("No RELION project is active under the current root.", warning=True)
            self._sync_toolbar_state()
            return
        try:
            self._controller.refresh_pipeline()
            self._sync_project_widget(preferred_job_id=self._widget.current_job_id())
            self._sync_preview_for_current_root()
        except Exception as exc:
            self.session.logger.warning(f"CryoRemote could not refresh RELION project index: {exc}")
            self._widget.show_status(f"Pipeline refresh failed: {exc}", warning=True)
            return
        self._widget.show_status(f"RELION pipeline refreshed from {self._active_project_root}.")
        self._sync_toolbar_state()

    def _poll_project(self):
        if self._fs is None or self._active_project_root is None:
            return
        try:
            self._controller.refresh_pipeline()
        except Exception:
            return
        self._sync_project_widget(preferred_job_id=self._widget.current_job_id())

    def _clear_cache(self):
        self._controller.clear_cache()
        self._widget.show_status("Cache cleared.")
        self.session.logger.info("CryoRemote cache cleared.")
        self._sync_toolbar_state()

    def _preview_entry(self, entry: RemoteEntry | None):
        if entry is None or self._fs is None:
            return

        self._remember_entry_location(entry)
        self._sync_preview_payload(self._controller.preview_path(entry.path))
        self._sync_toolbar_state()

    def _pipeline_job_selected(self, job_id: str | None):
        if job_id is None or self._project_index is None:
            self._sync_toolbar_state()
            return
        job = self._project_index.job_by_id(job_id)
        if job is None:
            self._sync_toolbar_state()
            return
        self._widget.update_preview(self._job_preview(job))
        self._sync_toolbar_state()

    def _refresh_current_summary(self):
        job = self._selected_job()
        if job is not None:
            self._widget.update_preview(self._job_preview(job))
            return
        entry = self._widget.current_entry()
        if entry is not None:
            self._preview_entry(entry)

    def _open_selected(self):
        entry = self._widget.current_entry()
        if entry is None or self._fs is None or self._config is None:
            return
        if not is_openable_path(entry.path):
            self._widget.show_status("Selected item is not openable in phase 1.", warning=True)
            return
        if is_command_file_path(entry.path):
            self._open_selected_command_file(entry)
            return

        try:
            self._controller.open_path(entry.path)
            self._widget.show_status(f"Opened {entry.name}")
        except Exception as exc:
            self.session.logger.error(f"CryoRemote could not open {entry.path}: {exc}")
            self._widget.show_status(f"Open failed: {exc}", error=True)

    def _open_selected_command_file(self, entry: RemoteEntry):
        if not self._widget.confirm_command_file_open(entry.path):
            self._widget.show_status("Command file execution cancelled.")
            return

        try:
            self._controller.open_path(entry.path)
            self._widget.show_status(f"Executed {entry.name}.")
        except Exception as exc:
            self.session.logger.error(f"CryoRemote could not execute {entry.path}: {exc}")
            self._widget.show_status(f"Command file failed: {exc}", error=True)

    def _open_postprocess(self):
        job = self._selected_job()
        if job is None:
            self._widget.show_status("No RELION job is selected.", warning=True)
            return
        try:
            self._controller.open_postprocess_model(job_path=job.job_dir)
            self._widget.show_status(f"Opened {(job.artifacts.postprocess_map or job.artifacts.latest_map).name}.")
            self._sync_toolbar_state()
        except Exception as exc:
            self.session.logger.error(f"CryoRemote could not open job artifacts: {exc}")
            self._widget.show_status(f"Open failed: {exc}", error=True)

    def _open_half_maps(self):
        job = self._selected_job()
        if job is None:
            self._widget.show_status("Half maps were not found in this RELION job.", warning=True)
            return

        try:
            self._controller.open_half_maps(job_path=job.job_dir)
            self._widget.show_status("Opened half maps.")
        except Exception as exc:
            self.session.logger.error(f"CryoRemote could not open half maps: {exc}")
            self._widget.show_status(f"Open failed: {exc}", error=True)

    def _open_latest_refine(self):
        if self._project_index is None:
            self._widget.show_status("No RELION project is active.", warning=True)
            return
        job = latest_refine_job(self._project_index)
        if job is None:
            self._widget.show_status("No latest RELION refine map was found.", warning=True)
            return
        self._open_job_artifacts(job)

    def _open_last_completed(self):
        if self._project_index is None:
            self._widget.show_status("No RELION project is active.", warning=True)
            return
        job = latest_completed_job(self._project_index)
        if job is None:
            self._widget.show_status("No completed job was found.", warning=True)
            return
        if job.artifacts.postprocess_map or job.artifacts.latest_map:
            self._open_job_artifacts(job)
        else:
            self._widget.select_job(job.job_id, emit=False)
            self._widget.update_preview(self._job_preview(job))
            self._widget.show_status("Selected the latest completed job, but it has no openable map/model.", warning=True)
            self._sync_toolbar_state()

    def _find_in_tree(self):
        job = self._selected_job()
        if job is None:
            self._widget.show_status("No RELION job is selected.", warning=True)
            return
        self._widget.select_tree_path(job.job_dir)
        self._widget.show_status(f"Focused {job.job_id} in the remote tree.")

    def _browse_path_requested(self, path_text: str):
        if self._fs is None or self._config is None:
            self._widget.show_status("CryoRemote is not connected.", warning=True)
            return
        target = normalize_browse_path(self._config.root, path_text)
        try:
            self._sync_browse_result(self._controller.browse(target))
            self._widget.show_status(f"Browsing {target}")
        except Exception as exc:
            self.session.logger.warning(f"CryoRemote could not browse to {target}: {exc}")
            self._widget.show_status(f"Browse failed: {exc}", warning=True)

    def _browse_up_requested(self):
        if self._config is None:
            self._widget.show_status("CryoRemote is not connected.", warning=True)
            return
        current = self._config.root
        parent = current.parent if current.parent != current else current
        self._browse_path_requested(str(parent))

    def _set_directory_requested(self):
        if self._fs is None or self._config is None:
            self._widget.show_status("CryoRemote is not connected.", warning=True)
            return
        entry = self._widget.current_entry()
        target = directory_target_for_entry(entry)
        if target is None:
            self._widget.show_status("No tree item is selected.", warning=True)
            return
        if target == self._config.root:
            self._widget.show_status(f"Already browsing {target}")
            return
        try:
            self._sync_browse_result(self._controller.browse(target))
            self._widget.show_status(f"Browsing {target}")
        except Exception as exc:
            self.session.logger.warning(f"CryoRemote could not set current directory to {target}: {exc}")
            self._widget.show_status(f"Set Dir failed: {exc}", warning=True)

    def _selected_job(self) -> RelionJobNode | None:
        if self._project_index is not None:
            selected = self._project_index.job_by_id(self._widget.current_job_id())
            if selected is not None:
                return selected
        entry = self._widget.current_entry()
        return self._job_for_entry(entry)

    def _job_for_entry(self, entry: RemoteEntry | None) -> RelionJobNode | None:
        if entry is None or self._fs is None:
            return None
        if self._project_index is not None:
            job = self._project_index.find_job_for_path(entry.path)
            if job is not None:
                return job
        target_dir = entry.path if entry.is_dir else entry.path.parent
        if classify_job_type(target_dir) is None:
            return None
        return build_job_node(
            target_dir,
            self._fs.ls(target_dir),
            job_id="/".join(target_dir.parts[-2:]),
            source="scan",
            note_text=self._safe_read_note(target_dir / "note.txt"),
        )

    def _preview_for_directory(self, entry: RemoteEntry) -> PreviewResult:
        if self._project_index is not None:
            if self._active_project_root is not None and entry.path == self._active_project_root:
                return preview_for_project(self._project_index)
            job = self._project_index.find_job_for_path(entry.path)
            if job is not None and entry.path == job.job_dir:
                return self._job_preview(job)
        return preview_for_directory(entry, self._fs.ls(entry.path))

    def _show_directory(self, path: PurePosixPath, *, root_entry: RemoteEntry | None = None):
        if self._fs is None or self._config is None:
            return
        if root_entry is not None:
            self._config.root = root_entry.path
            self._sync_browse_result(self._controller.browse(root_entry.path), root_entry=root_entry)
            return
        self._sync_browse_result(self._controller.browse(path))

    def _job_preview(self, job: RelionJobNode) -> PreviewResult:
        snippet = self._job_preview_snippet(job)
        return preview_for_job(job, preview_snippet=snippet)

    def _job_preview_snippet(self, job: RelionJobNode) -> str | None:
        if self._fs is None:
            return None
        preview_path = job.artifacts.preview_path
        if preview_path is None:
            return None
        try:
            entry = self._fs.info(preview_path)
        except Exception:
            return None
        if is_text_previewable(preview_path):
            return self._fs.read_text_head(preview_path, 4096)
        if is_mrc_previewable(preview_path):
            return preview_for_mrc(entry, self._fs.read_bytes_head(preview_path, 4096)).body
        return None

    def _open_job_artifacts(self, job: RelionJobNode):
        if self._fs is None or self._config is None:
            return
        map_path = job.artifacts.postprocess_map or job.artifacts.latest_map
        if map_path is None:
            self._widget.show_status("No map was found for the selected job.", warning=True)
            return

        try:
            map_entry = self._fs.info(map_path)
            local_map = self._cache_path_for_entry(map_entry)
            model_paths: list[Path] = []
            if job.artifacts.model_path is not None:
                model_entry = self._fs.info(job.artifacts.model_path)
                model_paths.append(self._cache_path_for_entry(model_entry))
            open_artifacts(self.session, map_paths=[local_map], model_paths=model_paths)
            self._widget.select_job(job.job_id, emit=False)
            self._widget.show_status(f"Opened {map_path.name}.")
            self._sync_toolbar_state()
        except Exception as exc:
            self.session.logger.error(f"CryoRemote could not open job artifacts: {exc}")
            self._widget.show_status(f"Open failed: {exc}", error=True)

    def _cache_path_for_entry(self, entry: RemoteEntry) -> Path:
        return self._controller._cache_path_for_entry(entry)

    def _cache_command_file_target(self, remote_path: PurePosixPath) -> Path:
        return self._controller._cache_command_file_target(remote_path)

    def _activate_project_for_path(self, path: PurePosixPath, *, announce: bool):
        if self._fs is None or self._config is None:
            return
        project_root = find_project_root(self._fs, path, floor=self._config.root)
        if project_root is None:
            self._active_project_root = None
            self._project_index = None
            self._watch_timer.stop()
            self._widget.clear_project()
            if announce:
                self._widget.show_status("Connected, but no RELION project was detected under the current root.", warning=True)
            self._sync_toolbar_state()
            return

        self._active_project_root = project_root
        self._remember_project_root(project_root)
        self._reload_project_index(preferred_job_id=self._widget.current_job_id(), announce=announce)

    def _reload_project_index(self, *, preferred_job_id: str | None, announce: bool):
        if self._fs is None or self._active_project_root is None:
            return
        try:
            index = load_project_index(self._fs, self._active_project_root)
        except Exception as exc:
            self.session.logger.warning(f"CryoRemote could not refresh RELION project index: {exc}")
            if announce:
                self._widget.show_status(f"Pipeline refresh failed: {exc}", warning=True)
            return

        self._project_index = index
        self._widget.set_project(index, preferred_job_id=preferred_job_id)
        self._watch_timer.start()
        selected = index.job_by_id(self._widget.current_job_id())
        if selected is not None:
            self._widget.update_preview(self._job_preview(selected))
        if announce:
            self._widget.show_status(f"RELION pipeline refreshed from {index.root}.")
        self._sync_toolbar_state()

    def _safe_read_note(self, path: PurePosixPath) -> str | None:
        if self._fs is None:
            return None
        try:
            return self._fs.read_text_head(path, 8192)
        except Exception:
            return None

    def _resolve_connection_root(
        self,
        fs: ParamikoSFTPFileSystem,
        config: ResolvedHostConfig,
        *,
        root_text: str,
        root_source: str,
        remembered: RememberedTargetState | None,
    ) -> tuple[RemoteEntry, str | None, bool, str]:
        candidates = root_candidates(
            root_text=root_text,
            root_source=root_source,
            remembered=remembered,
            preferred_root=self.settings.preferred_root,
        )
        first_label = candidates[0][0]
        first_path = candidates[0][1]
        last_error: Exception | None = None

        for label, candidate in candidates:
            try:
                entry = fs.info(candidate)
            except Exception as exc:
                last_error = exc
                continue
            if not entry.is_dir:
                last_error = SFTPConnectionError(f"Configured root is not a directory: {candidate}")
                continue
            config.root = candidate
            note, warning = self._root_resolution_message(
                resolved_label=label,
                resolved_root=candidate,
                first_label=first_label,
                first_path=first_path,
            )
            return entry, note, warning, label

        if root_source == "manual":
            raise SFTPConnectionError(f"Configured root does not exist or is not a directory: {first_path}")
        if last_error is not None:
            raise SFTPConnectionError(f"No usable starting directory was found: {last_error}")
        raise SFTPConnectionError("No usable starting directory was found.")

    def _root_resolution_message(
        self,
        *,
        resolved_label: str,
        resolved_root: PurePosixPath,
        first_label: str,
        first_path: PurePosixPath,
    ) -> tuple[str | None, bool]:
        if resolved_label == "remembered" and resolved_root == first_path:
            return f"Restored last location {resolved_root}", False
        if resolved_label == "remembered":
            return f"Fell back to remembered project location {resolved_root}", True
        if resolved_label == "preferred" and first_label == "remembered":
            return f"Fell back to configured root {resolved_root} because the last location was unavailable", True
        if resolved_label == "default":
            return f"Fell back to / because the saved location was unavailable", True
        return None, False

    def _remembered_roots_by_alias(self) -> dict[str, str]:
        roots: dict[str, str] = {}
        for key, state in self._remembered_targets.items():
            if state.last_root is None:
                continue
            roots[key] = str(state.last_root)
        return roots

    def _remember_entry_location(self, entry: RemoteEntry):
        self._remember_location(directory_to_remember(entry.path, is_dir=entry.is_dir))

    def _remember_project_root(self, project_root: PurePosixPath):
        self._remember_location(project_root, project_root=project_root)

    def _remember_location(self, directory: PurePosixPath, *, project_root: PurePosixPath | None = None):
        if self._target_key is None:
            return
        state = self._remembered_targets.get(self._target_key, RememberedTargetState())
        state.last_root = directory
        if project_root is not None:
            state.last_project_root = project_root
        state.updated_at = time()
        self._remembered_targets[self._target_key] = state
        self.settings.remembered_targets = remembered_targets_to_settings(self._remembered_targets)
        if self._config is not None and self._config.alias:
            self._widget.set_remembered_root(self._config.alias, str(state.last_root))

    def _save_preferences(self, payload: dict, config: ResolvedHostConfig, *, root_source: str):
        self.settings.preferred_alias = payload.get("alias") or config.alias
        self.settings.preferred_host = payload.get("host") or config.hostname
        self.settings.preferred_user = payload.get("user") or (config.user or "")
        self.settings.preferred_port = int(payload.get("port") or config.port)
        if root_source == "manual" or not self.settings.preferred_root:
            self.settings.preferred_root = payload.get("root") or str(config.root)

    def _default_cache_dir(self) -> Path:
        from chimerax import app_dirs

        override = self.settings.cache_dir
        if override:
            return Path(override)
        return Path(app_dirs.user_cache_dir) / "CryoRemote"

    def _sync_widget_from_controller(self, *, root_entry: RemoteEntry | None = None):
        if self._fs is None or self._config is None:
            self._widget.set_connected(False)
            self._widget.set_session_target("Not connected")
            self._widget.set_current_path("/")
            self._widget.clear_project()
            self._widget.clear_model()
            self._watch_timer.stop()
            return
        if self._target_key is None:
            self._target_key = target_key(self._config.alias, self._config)
        self._widget.set_connected(True)
        self._widget.set_session_target(session_target_text(self._config))
        self._widget.set_root_value(str(self._config.root), source="manual")
        self._sync_browse_result(self._controller.refresh_current_root(), root_entry=root_entry)

    def _sync_browse_result(self, result, *, root_entry: RemoteEntry | None = None):
        entry = root_entry or result.root_entry
        self._widget.install_model(RemoteTreeModel(self._fs, entry))
        self._widget.set_current_path(str(entry.path))
        self._remember_entry_location(entry)
        self._sync_project_widget(preferred_job_id=self._widget.current_job_id())
        self._widget.update_preview(result.preview)
        self._sync_toolbar_state()

    def _sync_project_widget(self, *, preferred_job_id: str | None = None):
        if self._project_index is None:
            self._watch_timer.stop()
            self._widget.clear_project()
            return
        self._widget.set_project(self._project_index, preferred_job_id=preferred_job_id)
        self._watch_timer.start()

    def _sync_preview_payload(self, payload):
        self._sync_project_widget(preferred_job_id=self._widget.current_job_id())
        if payload.related_job is not None and self._widget.current_job_id() != payload.related_job.job_id:
            self._widget.select_job(payload.related_job.job_id, emit=False)
        self._widget.update_preview(payload.preview)

    def _sync_preview_for_current_root(self):
        if self._config is None:
            return
        self._sync_preview_payload(self._controller.preview_path(self._config.root))

    def _action_availability(self) -> dict[str, bool]:
        entry = self._widget.current_entry()
        job = self._selected_job()
        return compute_action_availability(
            connected=self._fs is not None and self._config is not None,
            has_project=self._project_index is not None and bool(self._project_index.jobs),
            has_job=job is not None,
            has_tree_entry=entry is not None,
            is_openable_file=bool(entry is not None and entry.is_file and entry.path.suffix.lower() in OPENABLE_SUFFIXES),
        )

    def _sync_toolbar_state(self):
        toolbar = getattr(self.session, "toolbar", None)
        if toolbar is None:
            return
        availability = self._action_availability()
        for action_id, placement in TOOLBAR_BUTTONS.items():
            toolbar.set_enabled(availability.get(action_id, False), TOOLBAR_TAB, placement.section, placement.button)
        toolbar.set_enabled(True, TOOLBAR_TAB, TOOLBAR_BUTTONS[ACTION_SHOW].section, TOOLBAR_BUTTONS[ACTION_SHOW].button)
