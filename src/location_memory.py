from __future__ import annotations

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Literal

from .models import ResolvedHostConfig


RootSource = Literal["manual", "remembered", "preferred", "default"]


@dataclass(slots=True)
class RememberedTargetState:
    last_root: PurePosixPath | None = None
    last_project_root: PurePosixPath | None = None
    updated_at: float | None = None

    @classmethod
    def from_dict(cls, data: object) -> "RememberedTargetState | None":
        if not isinstance(data, dict):
            return None
        last_root = _optional_posix_path(data.get("last_root"))
        last_project_root = _optional_posix_path(data.get("last_project_root"))
        updated_at = data.get("updated_at")
        if updated_at is not None:
            try:
                updated_at = float(updated_at)
            except (TypeError, ValueError):
                updated_at = None
        return cls(last_root=last_root, last_project_root=last_project_root, updated_at=updated_at)

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {}
        if self.last_root is not None:
            payload["last_root"] = str(self.last_root)
        if self.last_project_root is not None:
            payload["last_project_root"] = str(self.last_project_root)
        if self.updated_at is not None:
            payload["updated_at"] = float(self.updated_at)
        return payload


def target_key(alias: str | None, config: ResolvedHostConfig) -> str:
    normalized_alias = (alias or "").strip()
    if normalized_alias:
        return normalized_alias
    if config.user:
        return f"{config.user}@{config.hostname}:{config.port}"
    return f"{config.hostname}:{config.port}"


def remembered_targets_from_settings(raw: object) -> dict[str, RememberedTargetState]:
    if not isinstance(raw, dict):
        return {}
    states: dict[str, RememberedTargetState] = {}
    for key, value in raw.items():
        if not isinstance(key, str):
            continue
        state = RememberedTargetState.from_dict(value)
        if state is None:
            continue
        states[key] = state
    return states


def remembered_targets_to_settings(states: dict[str, RememberedTargetState]) -> dict[str, dict[str, object]]:
    return {key: state.to_dict() for key, state in states.items()}


def root_candidates(
    *,
    root_text: str,
    root_source: str,
    remembered: RememberedTargetState | None,
    preferred_root: str,
) -> list[tuple[RootSource, PurePosixPath]]:
    if root_source == "manual":
        return [("manual", _normalize_posix_path(root_text or "/"))]

    candidates: list[tuple[RootSource, PurePosixPath]] = []
    seen: set[PurePosixPath] = set()

    def add(source: RootSource, value: PurePosixPath | str | None):
        if value is None:
            return
        path = value if isinstance(value, PurePosixPath) else _normalize_posix_path(value)
        if path in seen:
            return
        seen.add(path)
        candidates.append((source, path))

    if remembered is not None:
        add("remembered", remembered.last_root)
        add("remembered", remembered.last_project_root)
    add("preferred", root_text or preferred_root or "/")
    add("preferred", preferred_root)
    add("default", "/")
    return candidates


def directory_to_remember(path: PurePosixPath, *, is_dir: bool) -> PurePosixPath:
    return path if is_dir else path.parent


def _optional_posix_path(value: object) -> PurePosixPath | None:
    if value in (None, ""):
        return None
    return _normalize_posix_path(str(value))


def _normalize_posix_path(value: str) -> PurePosixPath:
    normalized = str(value or "/").strip() or "/"
    return PurePosixPath(normalized)
