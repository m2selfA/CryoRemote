from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Literal


EntryType = Literal["file", "directory"]
RelionJobState = Literal["running", "scheduled", "succeeded", "failed", "aborted", "unknown"]


@dataclass(slots=True)
class ResolvedHostConfig:
    alias: str
    hostname: str
    user: str | None = None
    port: int = 22
    root: PurePosixPath = PurePosixPath("/")
    identity_files: tuple[Path, ...] = ()
    identities_only: bool = False
    strict_host_key_checking: bool = True
    warnings: tuple[str, ...] = ()
    unsupported_options: tuple[str, ...] = ()
    config_path: Path | None = None


@dataclass(slots=True)
class RemoteEntry:
    path: PurePosixPath
    entry_type: EntryType
    size: int | None = None
    mtime: float | None = None

    @property
    def name(self) -> str:
        return self.path.name or str(self.path)

    @property
    def is_file(self) -> bool:
        return self.entry_type == "file"

    @property
    def is_dir(self) -> bool:
        return self.entry_type == "directory"


@dataclass(slots=True)
class RelionArtifactSet:
    latest_map: PurePosixPath | None = None
    postprocess_map: PurePosixPath | None = None
    half_map_1: PurePosixPath | None = None
    half_map_2: PurePosixPath | None = None
    model_path: PurePosixPath | None = None
    mask_path: PurePosixPath | None = None
    primary_star: PurePosixPath | None = None
    log_path: PurePosixPath | None = None
    err_path: PurePosixPath | None = None
    note_path: PurePosixPath | None = None
    related_files: tuple[PurePosixPath, ...] = ()
    preview_path: PurePosixPath | None = None


@dataclass(slots=True)
class RelionJobNode:
    job_id: str
    job_dir: PurePosixPath
    job_type: str | None
    title: str
    state: RelionJobState = "unknown"
    updated_at: float | None = None
    parents: tuple[str, ...] = ()
    children: tuple[str, ...] = ()
    artifacts: RelionArtifactSet = field(default_factory=RelionArtifactSet)
    notes: tuple[str, ...] = ()
    next_actions: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()
    alias: str | None = None
    process_type_label: str | None = None
    pipeline_status: str | None = None
    source: Literal["pipeline", "scan"] = "scan"


@dataclass(slots=True)
class RelionProjectIndex:
    root: PurePosixPath
    pipeline_path: PurePosixPath | None
    jobs: tuple[RelionJobNode, ...]
    warnings: tuple[str, ...] = ()
    source: Literal["pipeline", "scan"] = "scan"
    updated_at: float | None = None
    _job_map: dict[str, RelionJobNode] = field(init=False, repr=False, default_factory=dict)
    _path_map: dict[PurePosixPath, RelionJobNode] = field(init=False, repr=False, default_factory=dict)

    def __post_init__(self):
        self._job_map = {job.job_id: job for job in self.jobs}
        self._path_map = {job.job_dir: job for job in self.jobs}

    def job_by_id(self, job_id: str | None) -> RelionJobNode | None:
        if not job_id:
            return None
        normalized = _normalize_job_id_value(job_id)
        return self._job_map.get(normalized)

    def find_job_for_path(self, path: PurePosixPath) -> RelionJobNode | None:
        target = PurePosixPath(str(path))
        for candidate in (target, *target.parents):
            job = self._path_map.get(candidate)
            if job is not None:
                return job
            if candidate == self.root:
                break
        return None

    def state_counts(self) -> dict[RelionJobState, int]:
        counts: dict[RelionJobState, int] = {
            "running": 0,
            "scheduled": 0,
            "succeeded": 0,
            "failed": 0,
            "aborted": 0,
            "unknown": 0,
        }
        for job in self.jobs:
            counts[job.state] = counts.get(job.state, 0) + 1
        return counts


@dataclass(slots=True)
class FlowchartNodeLayout:
    job_id: str
    column: int
    row: int


@dataclass(slots=True)
class CacheRecord:
    remote_path: str
    size: int | None
    mtime: float | None
    host: str


@dataclass(slots=True)
class CacheProbe:
    exists: bool
    is_fresh: bool
    data_path: Path
    meta_path: Path
    record: CacheRecord | None = None


@dataclass(slots=True)
class PreviewResult:
    title: str
    body: str
    related_files: tuple[PurePosixPath, ...] = ()
    notes: tuple[str, ...] = ()
    is_text: bool = True


@dataclass(slots=True)
class ConnectResult:
    config: ResolvedHostConfig
    warnings: tuple[str, ...] = field(default_factory=tuple)


def _normalize_job_id_value(job_id: str) -> str:
    normalized = job_id.strip().replace("\\", "/")
    if normalized and not normalized.endswith("/"):
        normalized += "/"
    return normalized
