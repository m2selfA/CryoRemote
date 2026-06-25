from __future__ import annotations

from pathlib import Path
from typing import Iterable

from chimerax.core.commands import run

from .cxc import quote_command_path


def open_artifacts(
    session,
    *,
    map_paths: Iterable[Path] = (),
    model_paths: Iterable[Path] = (),
    hidden_map_paths: Iterable[Path] = (),
) -> list[object]:
    opened: list[object] = []

    for path in map_paths:
        opened.extend(_open_one(session, path))
    for path in model_paths:
        opened.extend(_open_one(session, path))
    for path in hidden_map_paths:
        opened.extend(_open_one(session, path, hide_model=True))

    _run_safe(session, "view all")
    return opened


def open_half_maps(session, first: Path, second: Path) -> list[object]:
    opened = []
    opened.extend(_open_one(session, first, name_suffix="half1", color="#4fa3ff"))
    opened.extend(_open_one(session, second, name_suffix="half2", color="#ff9f1c"))
    _run_safe(session, "view all")
    return opened


def run_command_file(session, path: Path) -> None:
    run(session, f"open {quote_command_path(path)}")


def _open_one(
    session,
    path: Path,
    *,
    name_suffix: str | None = None,
    color: str | None = None,
    hide_model: bool = False,
) -> list[object]:
    models, status = session.open_command.open_data(str(path), in_file_history=True)
    session.models.add(models)
    if status:
        session.logger.info(status)

    for model in models:
        if name_suffix:
            model.name = f"{path.stem} ({name_suffix})"
        else:
            model.name = path.stem
        if color:
            _run_safe(session, f"color #{model.id_string} {color}")
        if hide_model:
            _run_safe(session, f"hide #{model.id_string} models")

    return list(models)


def _run_safe(session, command: str) -> None:
    try:
        run(session, command)
    except Exception as exc:  # pragma: no cover - exercised in ChimeraX
        session.logger.warning(f"CryoRemote could not run '{command}': {exc}")
