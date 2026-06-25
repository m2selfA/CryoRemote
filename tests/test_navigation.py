from __future__ import annotations

from pathlib import PurePosixPath

from cryoremote_bundle.models import RemoteEntry, ResolvedHostConfig
from cryoremote_bundle.navigation import directory_target_for_entry, normalize_browse_path, session_target_text


def test_normalize_browse_path_keeps_absolute_path():
    assert normalize_browse_path(PurePosixPath("/data/jobs"), "/share/scratch/project") == PurePosixPath(
        "/share/scratch/project"
    )


def test_normalize_browse_path_resolves_relative_segments():
    assert normalize_browse_path(PurePosixPath("/data/jobs/job010"), "../job011/./maps") == PurePosixPath(
        "/data/jobs/job011/maps"
    )


def test_normalize_browse_path_empty_text_keeps_current_root():
    assert normalize_browse_path(PurePosixPath("/data/jobs/job010"), "   ") == PurePosixPath("/data/jobs/job010")


def test_session_target_text_prefers_alias():
    config = ResolvedHostConfig(alias="wmn02", hostname="10.100.10.10", user="shark", port=23523)

    assert session_target_text(config) == "wmn02 | shark@10.100.10.10:23523"


def test_session_target_text_falls_back_to_endpoint():
    config = ResolvedHostConfig(alias="", hostname="cluster.example.org", user="shark", port=22)

    assert session_target_text(config) == "shark@cluster.example.org:22"


def test_directory_target_for_entry_uses_directory_as_is():
    entry = RemoteEntry(PurePosixPath("/share/project/Class3D/job010"), "directory")

    assert directory_target_for_entry(entry) == PurePosixPath("/share/project/Class3D/job010")


def test_directory_target_for_entry_uses_parent_for_file():
    entry = RemoteEntry(PurePosixPath("/share/project/Class3D/job010/run_it001_data.star"), "file")

    assert directory_target_for_entry(entry) == PurePosixPath("/share/project/Class3D/job010")


def test_directory_target_for_entry_allows_empty_selection():
    assert directory_target_for_entry(None) is None
