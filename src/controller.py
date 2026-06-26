from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .cache import CacheManager
from .cxc import rewrite_command_file_text
from .models import PreviewResult, RelionJobNode, RelionProjectIndex, RemoteEntry, ResolvedHostConfig
from .navigation import normalize_browse_path
from .opening import is_command_file_path, is_model_path, is_openable_path
from .preview import (
    format_timestamp,
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
from .session_ops import open_artifacts, open_half_maps, run_command_file
from .settings import CryoRemoteSettings
from .sftp_fs import ParamikoSFTPFileSystem, PromptHandler, SFTPConnectionError


@dataclass(slots=True)
class BrowseResult:
    root_entry: RemoteEntry
    entries: tuple[RemoteEntry, ...]
    preview: PreviewResult
    project_index: RelionProjectIndex | None
    active_project_root: PurePosixPath | None


@dataclass(slots=True)
class PreviewPayload:
    entry: RemoteEntry
    preview: PreviewResult
    project_index: RelionProjectIndex | None
    active_project_root: PurePosixPath | None
    related_job: RelionJobNode | None = None


@dataclass(slots=True)
class OpenResult:
    remote_paths: tuple[PurePosixPath, ...]
    opened_command_file: bool = False


@dataclass(slots=True)
class StatusSnapshot:
    connected: bool
    alias: str | None = None
    hostname: str | None = None
    user: str | None = None
    port: int | None = None
    root: PurePosixPath | None = None
    project_root: PurePosixPath | None = None
    project_source: str | None = None
    jobs: int = 0


class CryoRemoteController:
    SESSION_ATTRIBUTE = "_cryoremote_controller"

    @classmethod
    def get_for_session(cls, session, *, create: bool = True) -> "CryoRemoteController | None":
        controller = getattr(session, cls.SESSION_ATTRIBUTE, None)
        if controller is None and create:
            controller = cls(session)
            setattr(session, cls.SESSION_ATTRIBUTE, controller)
        return controller

    def __init__(self, session):
        self.session = session
        self.settings = CryoRemoteSettings(session, "CryoRemote", version="2")
        self.fs: ParamikoSFTPFileSystem | None = None
        self.config: ResolvedHostConfig | None = None
        self.project_index: RelionProjectIndex | None = None
        self.active_project_root: PurePosixPath | None = None
        self.cache_manager = CacheManager(self._default_cache_dir())

    @property
    def is_connected(self) -> bool:
        return self.fs is not None and self.config is not None

    def status_snapshot(self) -> StatusSnapshot:
        if not self.is_connected or self.config is None:
            return StatusSnapshot(connected=False)
        return StatusSnapshot(
            connected=True,
            alias=self.config.alias or None,
            hostname=self.config.hostname,
            user=self.config.user,
            port=self.config.port,
            root=self.config.root,
            project_root=self.active_project_root,
            project_source=self.project_index.source if self.project_index is not None else None,
            jobs=len(self.project_index.jobs) if self.project_index is not None else 0,
        )

    def connect(
        self,
        config: ResolvedHostConfig,
        *,
        password: str | None = None,
        prompt_handler: PromptHandler | None = None,
    ) -> BrowseResult:
        fs = ParamikoSFTPFileSystem(
            config,
            password=password,
            prompt_handler=prompt_handler,
        )
        fs.connect()
        try:
            return self.attach_connected_fs(fs, config)
        except Exception:
            fs.close()
            raise

    def attach_connected_fs(self, fs: ParamikoSFTPFileSystem, config: ResolvedHostConfig) -> BrowseResult:
        self.disconnect()
        self.fs = fs
        self.config = config
        try:
            return self.browse(config.root)
        except Exception:
            self.disconnect()
            raise

    def disconnect(self) -> None:
        if self.fs is not None:
            self.fs.close()
        self.fs = None
        self.config = None
        self.project_index = None
        self.active_project_root = None

    def clear_cache(self) -> None:
        self.cache_manager.clear()

    def refresh_current_root(self) -> BrowseResult:
        config = self._require_config()
        return self.browse(config.root)

    def refresh_pipeline(self) -> RelionProjectIndex:
        if self.active_project_root is None:
            raise RuntimeError("No RELION project is active under the current root.")
        self._reload_project_index()
        if self.project_index is None:
            raise RuntimeError("No RELION project is active under the current root.")
        return self.project_index

    def browse(self, path: str | PurePosixPath) -> BrowseResult:
        fs = self._require_fs()
        config = self._require_config()
        entry = fs.info(self.resolve_path(path))
        if not entry.is_dir:
            raise SFTPConnectionError(f"Target is not a directory: {entry.path}")
        config.root = entry.path
        self._activate_project_for_path(entry.path)
        entries = tuple(fs.ls(entry.path))
        preview = self._preview_for_entry(entry, directory_entries=entries)
        return BrowseResult(
            root_entry=entry,
            entries=entries,
            preview=preview,
            project_index=self.project_index,
            active_project_root=self.active_project_root,
        )

    def preview_path(self, path: str | PurePosixPath) -> PreviewPayload:
        fs = self._require_fs()
        entry = fs.info(self.resolve_path(path))
        target_dir = entry.path if entry.is_dir else entry.path.parent
        self._activate_project_for_path(target_dir)
        related_job = self._job_for_entry(entry)
        preview = self._preview_for_entry(entry)
        if related_job is not None and not preview.related_files:
            preview.related_files = related_job.artifacts.related_files
            preview.notes = tuple(dict.fromkeys(preview.notes + related_job.notes))
        return PreviewPayload(
            entry=entry,
            preview=preview,
            project_index=self.project_index,
            active_project_root=self.active_project_root,
            related_job=related_job,
        )

    def open_path(self, path: str | PurePosixPath) -> OpenResult:
        fs = self._require_fs()
        entry = fs.info(self.resolve_path(path))
        if not entry.is_file or not is_openable_path(entry.path):
            raise RuntimeError(f"Path is not openable: {entry.path}")
        self._activate_project_for_path(entry.path.parent)
        if is_command_file_path(entry.path):
            return self._open_command_file(entry)

        local_path = self._cache_path_for_entry(entry)
        hidden = [local_path] if "mask" in entry.name.lower() else []
        visible = [] if hidden else [local_path]
        model_paths = [local_path] if is_model_path(entry.path) else []
        open_artifacts(
            self.session,
            map_paths=visible if not model_paths else (),
            model_paths=model_paths,
            hidden_map_paths=hidden,
        )
        return OpenResult(remote_paths=(entry.path,))

    def open_latest_refine(self) -> OpenResult:
        project = self._require_project_index()
        job = latest_refine_job(project)
        if job is None:
            raise RuntimeError("No latest RELION refine map was found.")
        return self._open_job_artifacts(job)

    def open_last_completed(self) -> OpenResult:
        project = self._require_project_index()
        job = latest_completed_job(project)
        if job is None:
            raise RuntimeError("No completed job was found.")
        if not (job.artifacts.postprocess_map or job.artifacts.latest_map):
            raise RuntimeError("The latest completed RELION job has no openable map/model artifacts.")
        return self._open_job_artifacts(job)

    def open_half_maps(self, *, job_path: str | PurePosixPath | None = None) -> OpenResult:
        job = self._resolve_job(job_path)
        if job is None or not job.artifacts.half_map_1 or not job.artifacts.half_map_2:
            raise RuntimeError("Half maps were not found in the current RELION job.")

        fs = self._require_fs()
        first_entry = fs.info(job.artifacts.half_map_1)
        second_entry = fs.info(job.artifacts.half_map_2)
        local_first = self._cache_path_for_entry(first_entry)
        local_second = self._cache_path_for_entry(second_entry)
        open_half_maps(self.session, local_first, local_second)
        return OpenResult(remote_paths=(job.artifacts.half_map_1, job.artifacts.half_map_2))

    def open_postprocess_model(self, *, job_path: str | PurePosixPath | None = None) -> OpenResult:
        job = self._resolve_job(job_path)
        if job is None:
            raise RuntimeError("No RELION job is active under the current root.")
        if not (job.artifacts.postprocess_map or job.artifacts.latest_map):
            raise RuntimeError("No postprocess/refine map was found for the current RELION job.")
        return self._open_job_artifacts(job)

    def resolve_path(self, path: str | PurePosixPath) -> PurePosixPath:
        config = self._require_config()
        candidate = PurePosixPath(str(path))
        if candidate.is_absolute():
            return normalize_browse_path(PurePosixPath("/"), str(candidate))
        return normalize_browse_path(config.root, str(candidate))

    def _require_fs(self) -> ParamikoSFTPFileSystem:
        if self.fs is None:
            raise RuntimeError("CryoRemote is not connected.")
        return self.fs

    def _require_config(self) -> ResolvedHostConfig:
        if self.config is None:
            raise RuntimeError("CryoRemote is not connected.")
        return self.config

    def _require_project_index(self) -> RelionProjectIndex:
        if self.project_index is None:
            raise RuntimeError("No RELION project is active under the current root.")
        return self.project_index

    def _preview_for_entry(
        self,
        entry: RemoteEntry,
        *,
        directory_entries: tuple[RemoteEntry, ...] | None = None,
    ) -> PreviewResult:
        fs = self._require_fs()
        if entry.is_dir:
            if self.project_index is not None:
                if self.active_project_root is not None and entry.path == self.active_project_root:
                    return preview_for_project(self.project_index)
                job = self.project_index.find_job_for_path(entry.path)
                if job is not None and entry.path == job.job_dir:
                    return self._job_preview(job)
            children = list(directory_entries) if directory_entries is not None else fs.ls(entry.path)
            return preview_for_directory(entry, children)
        if is_text_previewable(entry.path):
            return preview_for_text(entry, fs.read_bytes_head(entry.path, 65536))
        if is_mrc_previewable(entry.path):
            return preview_for_mrc(entry, fs.read_bytes_head(entry.path, 4096))
        return PreviewResult(
            title=entry.name,
            body=f"Path: {entry.path}\nType: {entry.entry_type}\nSize: {entry.size or 'unknown'}",
            is_text=False,
        )

    def _job_preview(self, job: RelionJobNode) -> PreviewResult:
        return preview_for_job(job, preview_snippet=self._job_preview_snippet(job))

    def _job_preview_snippet(self, job: RelionJobNode) -> str | None:
        fs = self._require_fs()
        preview_path = job.artifacts.preview_path
        if preview_path is None:
            return None
        try:
            entry = fs.info(preview_path)
        except Exception:
            return None
        if is_text_previewable(preview_path):
            return fs.read_text_head(preview_path, 4096)
        if is_mrc_previewable(preview_path):
            return preview_for_mrc(entry, fs.read_bytes_head(preview_path, 4096)).body
        return None

    def _activate_project_for_path(self, path: PurePosixPath) -> None:
        fs = self._require_fs()
        project_root = find_project_root(fs, path)
        if project_root is None:
            self.active_project_root = None
            self.project_index = None
            return
        self.active_project_root = project_root
        self._reload_project_index()

    def _reload_project_index(self) -> None:
        fs = self._require_fs()
        if self.active_project_root is None:
            self.project_index = None
            return
        self.project_index = load_project_index(fs, self.active_project_root)

    def _job_for_entry(self, entry: RemoteEntry | None) -> RelionJobNode | None:
        fs = self._require_fs()
        if entry is None:
            return None
        if self.project_index is not None:
            job = self.project_index.find_job_for_path(entry.path)
            if job is not None:
                return job
        target_dir = entry.path if entry.is_dir else entry.path.parent
        if classify_job_type(target_dir) is None:
            return None
        return build_job_node(
            target_dir,
            fs.ls(target_dir),
            job_id="/".join(target_dir.parts[-2:]),
            source="scan",
            note_text=self._safe_read_note(target_dir / "note.txt"),
        )

    def _current_job(self) -> RelionJobNode | None:
        config = self._require_config()
        if self.project_index is None:
            return None
        return self.project_index.find_job_for_path(config.root)

    def _resolve_job(self, job_path: str | PurePosixPath | None) -> RelionJobNode | None:
        if job_path is None:
            return self._current_job()
        target = self.resolve_path(job_path)
        self._activate_project_for_path(target)
        if self.project_index is None:
            return None
        return self.project_index.find_job_for_path(target)

    def _open_job_artifacts(self, job: RelionJobNode) -> OpenResult:
        fs = self._require_fs()
        map_path = job.artifacts.postprocess_map or job.artifacts.latest_map
        if map_path is None:
            raise RuntimeError("No map was found for the current RELION job.")
        map_entry = fs.info(map_path)
        local_map = self._cache_path_for_entry(map_entry)
        model_paths: list[Path] = []
        remote_paths: list[PurePosixPath] = [map_path]
        if job.artifacts.model_path is not None:
            model_entry = fs.info(job.artifacts.model_path)
            model_paths.append(self._cache_path_for_entry(model_entry))
            remote_paths.append(job.artifacts.model_path)
        open_artifacts(self.session, map_paths=[local_map], model_paths=model_paths)
        return OpenResult(remote_paths=tuple(remote_paths))

    def _open_command_file(self, entry: RemoteEntry) -> OpenResult:
        temp_path: Path | None = None
        try:
            cached_script = self._cache_path_for_entry(entry)
            script_text = cached_script.read_text(encoding="utf-8", errors="replace")
            rewritten = rewrite_command_file_text(
                script_text,
                entry.path,
                rewrite_remote_target=self._cache_command_file_target,
            )
            with tempfile.NamedTemporaryFile(
                mode="w",
                prefix=f"{entry.path.stem}.",
                suffix=entry.path.suffix,
                delete=False,
                encoding="utf-8",
            ) as handle:
                handle.write(rewritten)
                temp_path = Path(handle.name)
            run_command_file(self.session, temp_path)
            return OpenResult(remote_paths=(entry.path,), opened_command_file=True)
        finally:
            if temp_path is not None:
                temp_path.unlink(missing_ok=True)

    def _cache_path_for_entry(self, entry: RemoteEntry) -> Path:
        fs = self._require_fs()
        config = self._require_config()
        return self.cache_manager.ensure_cached(
            config.alias,
            entry,
            lambda output: fs.get_file(entry.path, output),
        )

    def _cache_command_file_target(self, remote_path: PurePosixPath) -> Path:
        fs = self._require_fs()
        entry = fs.info(remote_path)
        if not entry.is_file:
            raise RuntimeError(f"Remote open target is not a file: {remote_path}")
        return self._cache_path_for_entry(entry)

    def _safe_read_note(self, path: PurePosixPath) -> str | None:
        fs = self._require_fs()
        try:
            return fs.read_text_head(path, 8192)
        except Exception:
            return None

    def _default_cache_dir(self) -> Path:
        from chimerax import app_dirs

        override = self.settings.cache_dir
        if override:
            return Path(override)
        return Path(app_dirs.user_cache_dir) / "CryoRemote"


def format_status_lines(snapshot: StatusSnapshot) -> list[str]:
    if not snapshot.connected:
        return ["connected: false"]
    return [
        "connected: true",
        f"alias: {snapshot.alias or '-'}",
        f"hostname: {snapshot.hostname or '-'}",
        f"user: {snapshot.user or '-'}",
        f"port: {snapshot.port if snapshot.port is not None else '-'}",
        f"root: {snapshot.root or '-'}",
        f"project_root: {snapshot.project_root or '-'}",
        f"project_source: {snapshot.project_source or '-'}",
        f"jobs: {snapshot.jobs}",
    ]


def format_browse_lines(result: BrowseResult) -> list[str]:
    lines = [
        f"path: {result.root_entry.path}",
        f"entries: {len(result.entries)}",
    ]
    for entry in result.entries:
        size_text = "-" if entry.size is None else str(entry.size)
        mtime_text = "-" if entry.mtime is None else format_timestamp(entry.mtime)
        lines.append(f"entry\t{entry.entry_type}\t{entry.path}\t{size_text}\t{mtime_text}")
    return lines


def format_preview_lines(payload: PreviewPayload) -> list[str]:
    lines = [
        f"title: {payload.preview.title}",
        f"path: {payload.entry.path}",
        f"is_text: {'true' if payload.preview.is_text else 'false'}",
        "--- body ---",
        payload.preview.body,
        "--- related ---",
    ]
    lines.extend(str(path) for path in payload.preview.related_files)
    lines.append("--- notes ---")
    lines.extend(payload.preview.notes)
    return lines


def format_open_lines(result: OpenResult) -> list[str]:
    if result.opened_command_file:
        return [f"opened-command-file: {result.remote_paths[0]}"]
    if len(result.remote_paths) == 1:
        return [f"opened: {result.remote_paths[0]}"]
    return [f"opened: {path}" for path in result.remote_paths]
