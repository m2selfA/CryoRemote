from __future__ import annotations

from pathlib import PurePosixPath

from cryoremote_bundle.location_memory import (
    RememberedTargetState,
    directory_to_remember,
    remembered_targets_from_settings,
    remembered_targets_to_settings,
    root_candidates,
    target_key,
)
from cryoremote_bundle.models import ResolvedHostConfig


def test_target_key_prefers_alias():
    config = ResolvedHostConfig(alias="ignored", hostname="host", user="shark", port=2222)

    assert target_key("wmn02", config) == "wmn02"


def test_target_key_falls_back_to_endpoint():
    config = ResolvedHostConfig(alias="host-input", hostname="10.100.10.10", user="shark", port=23523)

    assert target_key("", config) == "shark@10.100.10.10:23523"


def test_remembered_targets_round_trip():
    states = {
        "wmn02": RememberedTargetState(
            last_root=PurePosixPath("/proj/work"),
            last_project_root=PurePosixPath("/proj"),
            updated_at=42.5,
        )
    }

    encoded = remembered_targets_to_settings(states)
    decoded = remembered_targets_from_settings(encoded)

    assert decoded["wmn02"].last_root == PurePosixPath("/proj/work")
    assert decoded["wmn02"].last_project_root == PurePosixPath("/proj")
    assert decoded["wmn02"].updated_at == 42.5


def test_root_candidates_manual_root_does_not_fallback():
    remembered = RememberedTargetState(
        last_root=PurePosixPath("/proj/work"),
        last_project_root=PurePosixPath("/proj"),
    )

    assert root_candidates(
        root_text="/manual/path",
        root_source="manual",
        remembered=remembered,
        preferred_root="/preferred",
    ) == [("manual", PurePosixPath("/manual/path"))]


def test_root_candidates_auto_prefers_remembered_then_project_then_preferred():
    remembered = RememberedTargetState(
        last_root=PurePosixPath("/proj/work"),
        last_project_root=PurePosixPath("/proj"),
    )

    assert root_candidates(
        root_text="/preferred",
        root_source="preferred",
        remembered=remembered,
        preferred_root="/preferred",
    ) == [
        ("remembered", PurePosixPath("/proj/work")),
        ("remembered", PurePosixPath("/proj")),
        ("preferred", PurePosixPath("/preferred")),
        ("default", PurePosixPath("/")),
    ]


def test_directory_to_remember_uses_parent_for_file():
    assert directory_to_remember(PurePosixPath("/proj/map.mrc"), is_dir=False) == PurePosixPath("/proj")
    assert directory_to_remember(PurePosixPath("/proj/job010"), is_dir=True) == PurePosixPath("/proj/job010")
