from __future__ import annotations

import re
import shlex
from pathlib import PurePosixPath
from typing import Iterable, Protocol, Sequence

from .models import FlowchartNodeLayout, RelionArtifactSet, RelionJobNode, RelionProjectIndex, RelionJobState, RemoteEntry

JOB_TYPE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("PostProcess", re.compile(r"postprocess", re.IGNORECASE)),
    ("Refine3D", re.compile(r"refine3d", re.IGNORECASE)),
    ("Class3D", re.compile(r"class3d", re.IGNORECASE)),
    ("Class2D", re.compile(r"class2d", re.IGNORECASE)),
    ("MaskCreate", re.compile(r"maskcreate", re.IGNORECASE)),
    ("LocalRes", re.compile(r"localres", re.IGNORECASE)),
)

JOB_NUMBER_RE = re.compile(r"job(\d+)", re.IGNORECASE)
TAG_RE = re.compile(r"#([A-Za-z0-9_.-]+)")

PROCESS_TYPE_NAMES = {
    "relion.import.movies": "Import Movies",
    "relion.import.other": "Import Others",
    "relion.importtomo": "Import Tomo",
    "relion.motioncorr.motioncor2": "Motion Corr.",
    "relion.motioncorr.own": "Motion Corr.",
    "relion.ctffind.ctffind4": "CTF Estimation",
    "relion.class2d": "2D Class",
    "relion.class3d": "3D Class",
    "relion.initialmodel": "Initial Model",
    "relion.refine3d": "Auto Refine",
    "relion.refine3d.tomo": "Auto Refine",
    "relion.postprocess": "Post Process",
    "relion.maskcreate": "Create Mask",
    "relion.localres.own": "Local Resolution",
    "relion.localres.resmap": "Local Resolution",
}

PIPELINE_STATE_MAP: dict[str, RelionJobState] = {
    "running": "running",
    "scheduled": "scheduled",
    "succeeded": "succeeded",
    "failed": "failed",
    "aborted": "aborted",
}

MARKER_STATE_PRIORITY: tuple[tuple[str, RelionJobState], ...] = (
    ("RELION_JOB_EXIT_FAILURE", "failed"),
    ("RELION_JOB_EXIT_ABORTED", "aborted"),
    ("RELION_JOB_ABORT_NOW", "aborted"),
    ("RELION_JOB_EXIT_SUCCESS", "succeeded"),
)


class RemoteFileSystem(Protocol):
    def info(self, path: str | PurePosixPath) -> RemoteEntry: ...

    def ls(self, path: str | PurePosixPath) -> list[RemoteEntry]: ...

    def read_text_head(self, path: str | PurePosixPath, limit: int = 65536, encoding: str = "utf-8") -> str: ...


def classify_job_type(path: PurePosixPath, process_type_label: str | None = None) -> str | None:
    if process_type_label:
        lowered = process_type_label.lower()
        for label, pattern in JOB_TYPE_PATTERNS:
            if pattern.search(lowered):
                return label
    haystack = "/".join(path.parts)
    for label, pattern in JOB_TYPE_PATTERNS:
        if pattern.search(haystack):
            return label
    return None


def normalize_job_id(job_id: str) -> str:
    normalized = job_id.strip().replace("\\", "/")
    if normalized and not normalized.endswith("/"):
        normalized += "/"
    return normalized


def job_number(path: PurePosixPath | str) -> int | None:
    name = path if isinstance(path, str) else path.name
    match = JOB_NUMBER_RE.search(name)
    if match:
        return int(match.group(1))
    return None


def choose_latest_job(job_dirs: Sequence[RemoteEntry]) -> RemoteEntry | None:
    if not job_dirs:
        return None
    numbered = []
    unnumbered = []
    for entry in job_dirs:
        number = job_number(entry.path)
        if number is None:
            unnumbered.append(entry)
        else:
            numbered.append((number, entry.mtime or -1, entry))
    if numbered:
        return sorted(numbered, key=lambda item: (item[0], item[1]))[-1][2]
    return sorted(unnumbered, key=lambda item: item.mtime or -1)[-1]


def load_project_index(fs: RemoteFileSystem, project_root: PurePosixPath) -> RelionProjectIndex:
    pipeline_path = project_root / "default_pipeline.star"
    warnings: list[str] = []

    try:
        pipeline_entry = fs.info(pipeline_path)
    except Exception:
        pipeline_entry = None

    if pipeline_entry and pipeline_entry.is_file:
        limit = max((pipeline_entry.size or 0) + 1024, 131072)
        try:
            pipeline_text = fs.read_text_head(pipeline_path, limit=limit)
            return _build_project_from_pipeline(fs, project_root, pipeline_path, pipeline_text, pipeline_entry.mtime)
        except Exception as exc:
            warnings.append(f"Could not parse default_pipeline.star: {exc}")

    fallback = scan_project_index(fs, project_root)
    if warnings:
        fallback.warnings = tuple(dict.fromkeys(warnings + list(fallback.warnings)))
    return fallback


def scan_project_index(fs: RemoteFileSystem, project_root: PurePosixPath) -> RelionProjectIndex:
    job_dirs: list[RemoteEntry] = []
    for child in _safe_ls(fs, project_root):
        if not child.is_dir:
            continue
        if job_number(child.path) is not None:
            job_dirs.append(child)
            continue
        job_type = classify_job_type(child.path)
        if job_type is None:
            continue
        for candidate in _safe_ls(fs, child.path):
            if candidate.is_dir and job_number(candidate.path) is not None:
                job_dirs.append(candidate)

    jobs = []
    for job_dir in sorted(job_dirs, key=lambda entry: (_job_sort_key(entry.path), entry.path.as_posix())):
        try:
            relative_id = normalize_job_id(str(job_dir.path.relative_to(project_root)).replace("\\", "/"))
        except ValueError:
            relative_id = normalize_job_id("/".join(job_dir.path.parts[-2:]))
        jobs.append(build_job_node(job_dir.path, _safe_ls(fs, job_dir.path), job_id=relative_id, source="scan"))
    return RelionProjectIndex(
        root=project_root,
        pipeline_path=None,
        jobs=tuple(jobs),
        warnings=("default_pipeline.star was not found; showing directory scan fallback.",),
        source="scan",
        updated_at=_latest_mtime_from_jobs(jobs),
    )


def build_job_node(
    job_dir: PurePosixPath,
    entries: Iterable[RemoteEntry],
    *,
    job_id: str | None = None,
    process_type_label: str | None = None,
    pipeline_status: str | None = None,
    alias: str | None = None,
    parents: Iterable[str] = (),
    children: Iterable[str] = (),
    source: str = "scan",
    note_text: str | None = None,
) -> RelionJobNode:
    items = list(entries)
    artifacts = discover_artifacts(job_dir, items)
    job_type = classify_job_type(job_dir, process_type_label)
    tags = extract_tags(note_text)
    notes = summarize_job_notes(artifacts, tags)
    state = resolve_job_state(items, pipeline_status)
    title = humanize_job_title(job_dir, job_type, process_type_label, alias)
    node = RelionJobNode(
        job_id=normalize_job_id(job_id or "/".join(job_dir.parts[-2:])),
        job_dir=job_dir,
        job_type=job_type,
        title=title,
        state=state,
        updated_at=_latest_mtime_from_entries(items),
        parents=tuple(dict.fromkeys(normalize_job_id(parent) for parent in parents if parent)),
        children=tuple(dict.fromkeys(normalize_job_id(child) for child in children if child)),
        artifacts=artifacts,
        notes=notes,
        next_actions=(),
        tags=tags,
        alias=alias,
        process_type_label=process_type_label,
        pipeline_status=pipeline_status,
        source="pipeline" if source == "pipeline" else "scan",
    )
    node.next_actions = suggest_next_actions(node)
    return node


def discover_artifacts(job_dir: PurePosixPath, entries: Iterable[RemoteEntry]) -> RelionArtifactSet:
    items = sorted((entry for entry in entries if entry.is_file), key=lambda entry: entry.name.lower())
    related = [entry.path for entry in items]
    artifacts = RelionArtifactSet(related_files=tuple(related))

    star_candidates: list[PurePosixPath] = []
    preview_candidates: list[PurePosixPath] = []

    for entry in items:
        lowered = entry.name.lower()
        if lowered == "postprocess.mrc":
            artifacts.postprocess_map = entry.path
            artifacts.latest_map = entry.path
        elif re.fullmatch(r"run_half1_class\d+_unfil\.mrc", lowered):
            artifacts.half_map_1 = entry.path
        elif re.fullmatch(r"run_half2_class\d+_unfil\.mrc", lowered):
            artifacts.half_map_2 = entry.path
        elif re.fullmatch(r"run_(it\d+_)?class\d+\.mrc", lowered) and artifacts.latest_map is None:
            artifacts.latest_map = entry.path
        elif re.fullmatch(r"run_class\d+\.mrc", lowered) and artifacts.latest_map is None:
            artifacts.latest_map = entry.path
        elif lowered.endswith((".pdb", ".cif")) and artifacts.model_path is None:
            artifacts.model_path = entry.path
        elif "mask" in lowered and lowered.endswith(".mrc") and artifacts.mask_path is None:
            artifacts.mask_path = entry.path
        elif lowered == "run.out":
            artifacts.log_path = entry.path
            preview_candidates.append(entry.path)
        elif lowered == "run.err":
            artifacts.err_path = entry.path
            preview_candidates.insert(0, entry.path)
        elif lowered == "note.txt":
            artifacts.note_path = entry.path
            preview_candidates.insert(0, entry.path)
        elif lowered.endswith(".star"):
            star_candidates.append(entry.path)
            preview_candidates.append(entry.path)

    if star_candidates:
        preferred = next((path for path in star_candidates if path.name.lower() not in {"job.star", "job_pipeline.star"}), None)
        artifacts.primary_star = preferred or star_candidates[0]
    if artifacts.preview_path is None:
        artifacts.preview_path = next(
            (path for path in preview_candidates if path is not None),
            artifacts.primary_star or artifacts.postprocess_map or artifacts.latest_map,
        )
    return artifacts


def resolve_job_state(entries: Iterable[RemoteEntry], pipeline_status: str | None = None) -> RelionJobState:
    names = {entry.name for entry in entries if entry.is_file}
    for marker, state in MARKER_STATE_PRIORITY:
        if marker in names:
            return state

    if pipeline_status:
        lowered = pipeline_status.strip().lower()
        return PIPELINE_STATE_MAP.get(lowered, "unknown")

    if any(name in names for name in ("run.out", "run.err")):
        return "running"
    return "unknown"


def humanize_job_title(
    job_dir: PurePosixPath,
    job_type: str | None,
    process_type_label: str | None,
    alias: str | None,
) -> str:
    if alias and alias.lower() != "none":
        return alias
    if process_type_label and process_type_label in PROCESS_TYPE_NAMES:
        return PROCESS_TYPE_NAMES[process_type_label]
    if job_type is not None:
        return job_type
    return job_dir.parent.name or job_dir.name


def summarize_job_notes(artifacts: RelionArtifactSet, tags: tuple[str, ...]) -> tuple[str, ...]:
    notes: list[str] = []
    if artifacts.postprocess_map and artifacts.model_path:
        notes.append("PostProcess map and model are both available.")
    if artifacts.half_map_1 and artifacts.half_map_2:
        notes.append("Half maps are available.")
    if tags:
        notes.append("Tags: " + ", ".join(f"#{tag}" for tag in tags))
    return tuple(notes)


def suggest_next_actions(job: RelionJobNode) -> tuple[str, ...]:
    actions: list[str] = []
    if job.artifacts.postprocess_map or job.artifacts.latest_map:
        actions.append("Open PostProcess + Model" if job.artifacts.postprocess_map else "Open Latest Refine Map")
    if job.artifacts.half_map_1 and job.artifacts.half_map_2:
        actions.append("Open Half Maps")
    if job.children:
        actions.append("Follow downstream jobs in flowchart")
    if job.state in {"running", "scheduled"} and (job.artifacts.log_path or job.artifacts.err_path):
        actions.append("Preview run.out or run.err")
    elif job.artifacts.primary_star:
        actions.append("Preview STAR outputs")
    return tuple(dict.fromkeys(actions))


def find_project_root(
    fs: RemoteFileSystem,
    start_path: PurePosixPath,
    *,
    floor: PurePosixPath | None = None,
) -> PurePosixPath | None:
    current = PurePosixPath(str(start_path))
    while True:
        probe = current / "default_pipeline.star"
        try:
            entry = fs.info(probe)
        except Exception:
            entry = None
        if entry and entry.is_file:
            return current
        if floor is not None and current == floor:
            return None
        if current.parent == current:
            return None
        current = current.parent


def latest_completed_job(project: RelionProjectIndex) -> RelionJobNode | None:
    completed = [job for job in project.jobs if job.state == "succeeded"]
    if not completed:
        return None
    return sorted(completed, key=lambda job: (_job_sort_key(job.job_id), job.updated_at or -1))[-1]


def latest_refine_job(project: RelionProjectIndex) -> RelionJobNode | None:
    candidates = [
        job
        for job in project.jobs
        if job.job_type in {"PostProcess", "Refine3D"} and (job.artifacts.postprocess_map or job.artifacts.latest_map)
    ]
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda job: (
            0 if job.artifacts.postprocess_map else 1,
            -_job_sort_key(job.job_id),
            -(job.updated_at or -1),
        ),
    )[0]


def build_flowchart_layout(project: RelionProjectIndex) -> tuple[FlowchartNodeLayout, ...]:
    parents = {job.job_id: [parent for parent in job.parents if project.job_by_id(parent)] for job in project.jobs}
    children = {job.job_id: [child for child in job.children if project.job_by_id(child)] for job in project.jobs}
    indegree = {job.job_id: len(parents[job.job_id]) for job in project.jobs}
    depth = {job.job_id: 0 for job in project.jobs}
    queue = [job.job_id for job in project.jobs if indegree[job.job_id] == 0]

    while queue:
        job_id = queue.pop(0)
        for child in children[job_id]:
            depth[child] = max(depth[child], depth[job_id] + 1)
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)

    grouped: dict[int, list[str]] = {}
    for job in project.jobs:
        grouped.setdefault(depth[job.job_id], []).append(job.job_id)

    layouts: list[FlowchartNodeLayout] = []
    for column in sorted(grouped):
        ordered = sorted(grouped[column], key=lambda job_id: (_job_sort_key(job_id), job_id))
        for row, job_id in enumerate(ordered):
            layouts.append(FlowchartNodeLayout(job_id=job_id, column=column, row=row))
    return tuple(layouts)


def extract_tags(note_text: str | None) -> tuple[str, ...]:
    if not note_text:
        return ()
    tags: list[str] = []
    for tag in TAG_RE.findall(note_text):
        if tag not in tags:
            tags.append(tag)
    return tuple(tags)


def _build_project_from_pipeline(
    fs: RemoteFileSystem,
    project_root: PurePosixPath,
    pipeline_path: PurePosixPath,
    pipeline_text: str,
    updated_at: float | None,
) -> RelionProjectIndex:
    tables = _parse_pipeline_tables(pipeline_text)
    process_rows = _rows_from_table(_table_block(tables, "pipeline_processes"))
    input_rows = _rows_from_table(_table_block(tables, "pipeline_input_edges"))
    output_rows = _rows_from_table(_table_block(tables, "pipeline_output_edges"))

    producers: dict[str, str] = {}
    for row in output_rows:
        process_name = _row_value(row, "rlnPipeLineEdgeProcess")
        process = normalize_job_id(process_name) if process_name else None
        to_node = _row_value(row, "rlnPipeLineEdgeToNode")
        if process and to_node:
            producers[str(to_node)] = process

    parents: dict[str, list[str]] = {}
    children: dict[str, list[str]] = {}
    for row in input_rows:
        process_name = _row_value(row, "rlnPipeLineEdgeProcess")
        process = normalize_job_id(process_name) if process_name else None
        from_node = _row_value(row, "rlnPipeLineEdgeFromNode")
        if not process or not from_node:
            continue
        parent = producers.get(str(from_node)) or _infer_job_id_from_node(str(from_node))
        if not parent or parent == process:
            continue
        parents.setdefault(process, []).append(parent)
        children.setdefault(parent, []).append(process)

    jobs: list[RelionJobNode] = []
    for row in process_rows:
        job_name = _row_value(row, "rlnPipeLineProcessName")
        job_id = normalize_job_id(job_name) if job_name else None
        if not job_id:
            continue
        job_dir = project_root / PurePosixPath(job_id.rstrip("/"))
        entries = _safe_ls(fs, job_dir)
        note_text = None
        note_path = job_dir / "note.txt"
        if any(entry.path == note_path for entry in entries):
            note_text = _safe_read_text(fs, note_path, limit=8192)
        node = build_job_node(
            job_dir,
            entries,
            job_id=job_id,
            process_type_label=_row_value(row, "rlnPipeLineProcessTypeLabel"),
            pipeline_status=_row_value(row, "rlnPipeLineProcessStatusLabel"),
            alias=_row_value(row, "rlnPipeLineProcessAlias"),
            parents=parents.get(job_id, ()),
            children=children.get(job_id, ()),
            source="pipeline",
            note_text=note_text,
        )
        jobs.append(node)

    jobs = sorted(jobs, key=lambda job: (_job_sort_key(job.job_id), job.job_id))
    return RelionProjectIndex(
        root=project_root,
        pipeline_path=pipeline_path,
        jobs=tuple(jobs),
        warnings=(),
        source="pipeline",
        updated_at=updated_at,
    )


def _table_block(tables: dict[str, object], suffix: str) -> object | None:
    if suffix in tables:
        return tables[suffix]
    key = f"data_{suffix}"
    if key in tables:
        return tables[key]
    return None


def _rows_from_table(table: object | None) -> list[dict[str, object]]:
    if table is None:
        return []
    if hasattr(table, "to_dict"):
        return list(table.to_dict(orient="records"))
    if isinstance(table, list):
        return [row for row in table if isinstance(row, dict)]
    return []


def _row_value(row: dict[str, object], key: str) -> str | None:
    value = row.get(key)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _infer_job_id_from_node(node_path: str) -> str | None:
    parts = node_path.replace("\\", "/").split("/")
    if len(parts) < 2:
        return None
    return normalize_job_id("/".join(parts[:2]))


def _job_sort_key(value: PurePosixPath | str) -> int:
    number = job_number(value)
    return -1 if number is None else number


def _safe_ls(fs: RemoteFileSystem, path: PurePosixPath) -> list[RemoteEntry]:
    try:
        return fs.ls(path)
    except Exception:
        return []


def _safe_read_text(fs: RemoteFileSystem, path: PurePosixPath, limit: int = 8192) -> str | None:
    try:
        return fs.read_text_head(path, limit=limit)
    except Exception:
        return None


def _latest_mtime_from_entries(entries: Iterable[RemoteEntry]) -> float | None:
    mtimes = [entry.mtime for entry in entries if entry.mtime is not None]
    return max(mtimes) if mtimes else None


def _latest_mtime_from_jobs(jobs: Iterable[RelionJobNode]) -> float | None:
    mtimes = [job.updated_at for job in jobs if job.updated_at is not None]
    return max(mtimes) if mtimes else None


def _parse_pipeline_tables(pipeline_text: str) -> dict[str, object]:
    return parse_star_blocks(pipeline_text)


def parse_star_blocks(text: str) -> dict[str, object]:
    lines = text.splitlines()
    index = 0
    blocks: dict[str, object] = {}

    while index < len(lines):
        line = lines[index].strip()
        if not line or line.startswith("#"):
            index += 1
            continue

        if not line.startswith("data_"):
            index += 1
            continue

        block_name = line[5:]
        index += 1
        index = _skip_blank_and_comment_lines(lines, index)
        if index >= len(lines):
            blocks[block_name] = {}
            break

        current = lines[index].strip()
        if current == "loop_":
            rows, index = _parse_loop_block(lines, index + 1)
            blocks[block_name] = rows
        else:
            values, index = _parse_key_value_block(lines, index)
            blocks[block_name] = values

    return blocks


def _parse_loop_block(lines: list[str], index: int) -> tuple[list[dict[str, object]], int]:
    columns: list[str] = []
    while index < len(lines):
        line = lines[index].strip()
        if not line or line.startswith("#"):
            index += 1
            continue
        if not line.startswith("_"):
            break
        column_name = line.split()[0].lstrip("_")
        columns.append(column_name)
        index += 1

    rows: list[dict[str, object]] = []
    while index < len(lines):
        line = lines[index].strip()
        if not line or line.startswith("#"):
            index += 1
            if rows:
                break
            continue
        if line.startswith(("data_", "loop_", "_")):
            break
        tokens = shlex.split(line, comments=False, posix=True)
        if not tokens:
            index += 1
            continue
        row = {column: tokens[position] if position < len(tokens) else None for position, column in enumerate(columns)}
        rows.append(row)
        index += 1

    return rows, index


def _parse_key_value_block(lines: list[str], index: int) -> tuple[dict[str, object], int]:
    values: dict[str, object] = {}
    while index < len(lines):
        line = lines[index].strip()
        if not line or line.startswith("#"):
            index += 1
            if values:
                break
            continue
        if line.startswith(("data_", "loop_")):
            break
        if not line.startswith("_"):
            index += 1
            continue

        tokens = shlex.split(line, comments=False, posix=True)
        key = tokens[0].lstrip("_")
        if len(tokens) > 1:
            values[key] = tokens[1]
            index += 1
            continue

        index += 1
        if index >= len(lines):
            values[key] = ""
            break
        values[key] = lines[index].strip()
        index += 1

    return values, index


def _skip_blank_and_comment_lines(lines: list[str], index: int) -> int:
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped and not stripped.startswith("#"):
            break
        index += 1
    return index
