from __future__ import annotations

from pathlib import Path, PurePosixPath


COMMAND_FILE_SUFFIX = ".cxc"
MODEL_SUFFIXES = frozenset({".pdb", ".cif"})
MAP_SUFFIXES = frozenset({".mrc", ".map"})
OPENABLE_SUFFIXES = frozenset((*MAP_SUFFIXES, *MODEL_SUFFIXES, COMMAND_FILE_SUFFIX))


def suffix_for_path(path: PurePosixPath | Path) -> str:
    return path.suffix.lower()


def is_command_file_path(path: PurePosixPath | Path) -> bool:
    return suffix_for_path(path) == COMMAND_FILE_SUFFIX


def is_model_path(path: PurePosixPath | Path) -> bool:
    return suffix_for_path(path) in MODEL_SUFFIXES


def is_map_path(path: PurePosixPath | Path) -> bool:
    return suffix_for_path(path) in MAP_SUFFIXES


def is_openable_path(path: PurePosixPath | Path) -> bool:
    return suffix_for_path(path) in OPENABLE_SUFFIXES
