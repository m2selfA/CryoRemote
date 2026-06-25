from __future__ import annotations

from pathlib import PurePosixPath

from .location_memory import directory_to_remember
from .models import RemoteEntry, ResolvedHostConfig


def normalize_browse_path(current_root: PurePosixPath, path_text: str) -> PurePosixPath:
    text = (path_text or "").strip()
    if not text:
        return current_root

    candidate = PurePosixPath(text)
    if not candidate.is_absolute():
        candidate = current_root / candidate

    normalized_parts: list[str] = []
    for part in candidate.parts:
        if part in {"", "/", "."}:
            continue
        if part == "..":
            if normalized_parts:
                normalized_parts.pop()
            continue
        normalized_parts.append(part)

    if not normalized_parts:
        return PurePosixPath("/")
    return PurePosixPath("/").joinpath(*normalized_parts)


def session_target_text(config: ResolvedHostConfig) -> str:
    endpoint = f"{config.user or '?'}@{config.hostname}:{config.port}"
    if config.alias:
        return f"{config.alias} | {endpoint}"
    return endpoint


def directory_target_for_entry(entry: RemoteEntry | None) -> PurePosixPath | None:
    if entry is None:
        return None
    return directory_to_remember(entry.path, is_dir=entry.is_dir)
