from __future__ import annotations

from pathlib import PurePosixPath

from cryoremote_bundle.cache import CacheManager
from cryoremote_bundle.models import RemoteEntry


def test_cache_round_trip(tmp_path):
    manager = CacheManager(tmp_path / "cache")
    entry = RemoteEntry(PurePosixPath("/remote/project/map.mrc"), "file", size=128, mtime=42.0)

    target = manager.ensure_cached(
        "delta",
        entry,
        lambda output: output.write_bytes(b"abc"),
    )

    assert target.exists()
    probe = manager.probe("delta", entry)
    assert probe.exists is True
    assert probe.is_fresh is True


def test_cache_invalidates_on_mtime_change(tmp_path):
    manager = CacheManager(tmp_path / "cache")
    entry = RemoteEntry(PurePosixPath("/remote/project/map.mrc"), "file", size=128, mtime=42.0)
    manager.ensure_cached("delta", entry, lambda output: output.write_bytes(b"abc"))

    changed = RemoteEntry(PurePosixPath("/remote/project/map.mrc"), "file", size=128, mtime=43.0)
    probe = manager.probe("delta", changed)

    assert probe.exists is True
    assert probe.is_fresh is False

