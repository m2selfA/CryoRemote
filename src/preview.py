from __future__ import annotations

import struct
from datetime import datetime, timezone
from pathlib import PurePosixPath

from .models import PreviewResult, RelionJobNode, RelionProjectIndex, RemoteEntry

TEXT_SUFFIXES = {".star", ".txt", ".log", ".out", ".err", ".json", ".cxc"}
MRC_SUFFIXES = {".mrc", ".map"}


def preview_for_directory(entry: RemoteEntry, children: list[RemoteEntry]) -> PreviewResult:
    body_lines = [
        f"Directory: {entry.path}",
        f"Entries: {len(children)}",
    ]
    dirs = sum(1 for child in children if child.is_dir)
    files = sum(1 for child in children if child.is_file)
    body_lines.append(f"Directories: {dirs}")
    body_lines.append(f"Files: {files}")
    return PreviewResult(
        title=entry.name,
        body="\n".join(body_lines),
    )


def preview_for_text(entry: RemoteEntry, payload: bytes) -> PreviewResult:
    text = payload.decode("utf-8", errors="replace")
    header = _entry_header(entry)
    if entry.path.suffix.lower() == ".cxc":
        body = f"{header}\n\nChimeraX command file: opening this file will execute its commands.\n\n{text}"
    else:
        body = f"{header}\n\n{text}"
    return PreviewResult(
        title=entry.name,
        body=body,
    )


def preview_for_mrc(entry: RemoteEntry, payload: bytes) -> PreviewResult:
    title = entry.name
    header = _entry_header(entry)
    details = parse_mrc_header(payload)
    lines = [header, "", "MRC header preview:"]
    for key, value in details.items():
        lines.append(f"{key}: {value}")
    return PreviewResult(title=title, body="\n".join(lines), is_text=False)


def parse_mrc_header(payload: bytes) -> dict[str, str]:
    if len(payload) < 1024:
        return {"error": "Header is shorter than 1024 bytes."}

    nx, ny, nz, mode = struct.unpack_from("<4i", payload, 0)
    mx, my, mz = struct.unpack_from("<3i", payload, 28)
    xlen, ylen, zlen = struct.unpack_from("<3f", payload, 40)
    return {
        "dimensions": f"{nx} x {ny} x {nz}",
        "mode": str(mode),
        "sampling_grid": f"{mx} x {my} x {mz}",
        "cell_lengths": f"{xlen:.3f}, {ylen:.3f}, {zlen:.3f}",
    }


def _entry_header(entry: RemoteEntry) -> str:
    size = f"{entry.size} bytes" if entry.size is not None else "unknown size"
    if entry.mtime is None:
        modified = "unknown mtime"
    else:
        modified = datetime.fromtimestamp(entry.mtime, tz=timezone.utc).isoformat()
    return f"Path: {entry.path}\nType: {entry.entry_type}\nSize: {size}\nModified: {modified}"


def is_text_previewable(path: PurePosixPath) -> bool:
    return path.suffix.lower() in TEXT_SUFFIXES


def is_mrc_previewable(path: PurePosixPath) -> bool:
    return path.suffix.lower() in MRC_SUFFIXES


def preview_for_project(project: RelionProjectIndex) -> PreviewResult:
    counts = project.state_counts()
    body_lines = [
        f"RELION project: {project.root}",
        f"Source: {project.source}",
        f"Pipeline: {project.pipeline_path or 'directory scan fallback'}",
        f"Jobs: {len(project.jobs)}",
        f"Succeeded: {counts['succeeded']}",
        f"Running: {counts['running']}",
        f"Scheduled: {counts['scheduled']}",
        f"Failed: {counts['failed']}",
        f"Aborted: {counts['aborted']}",
        f"Unknown: {counts['unknown']}",
    ]
    return PreviewResult(
        title=f"{project.root.name or project.root.as_posix()}",
        body="\n".join(body_lines),
        notes=project.warnings,
    )


def preview_for_job(job: RelionJobNode, preview_snippet: str | None = None) -> PreviewResult:
    body_lines = [
        f"Job: {job.job_id}",
        f"Title: {job.title}",
        f"Type: {job.job_type or 'unknown'}",
        f"State: {job.state}",
        f"Updated: {format_timestamp(job.updated_at)}",
        f"Parents: {', '.join(job.parents) if job.parents else '(none)'}",
        f"Children: {', '.join(job.children) if job.children else '(none)'}",
    ]
    if job.artifacts.postprocess_map:
        body_lines.append(f"PostProcess map: {job.artifacts.postprocess_map.name}")
    if job.artifacts.latest_map and job.artifacts.latest_map != job.artifacts.postprocess_map:
        body_lines.append(f"Latest map: {job.artifacts.latest_map.name}")
    if job.artifacts.model_path:
        body_lines.append(f"Model: {job.artifacts.model_path.name}")
    if job.artifacts.mask_path:
        body_lines.append(f"Mask: {job.artifacts.mask_path.name}")
    if job.artifacts.primary_star:
        body_lines.append(f"STAR: {job.artifacts.primary_star.name}")
    if job.artifacts.half_map_1 and job.artifacts.half_map_2:
        body_lines.append(f"Half maps: {job.artifacts.half_map_1.name}, {job.artifacts.half_map_2.name}")
    if preview_snippet:
        body_lines.extend(["", preview_snippet])
    notes = list(job.notes)
    notes.extend(f"[hint] {hint}" for hint in job.next_actions)
    return PreviewResult(
        title=f"{job.title} [{job.state}]",
        body="\n".join(body_lines),
        related_files=job.artifacts.related_files,
        notes=tuple(notes),
    )


def format_timestamp(value: float | None) -> str:
    if value is None:
        return "unknown"
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
