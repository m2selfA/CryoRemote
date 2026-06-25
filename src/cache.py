from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path
from typing import Callable

from .models import CacheProbe, CacheRecord, RemoteEntry


class CacheManager:
    def __init__(self, base_dir: Path):
        self.base_dir = Path(base_dir)

    def probe(self, host: str, entry: RemoteEntry) -> CacheProbe:
        data_path, meta_path = self.paths_for(host, str(entry.path))
        if not data_path.exists() or not meta_path.exists():
            return CacheProbe(False, False, data_path, meta_path, None)

        try:
            payload = json.loads(meta_path.read_text(encoding="utf-8"))
            record = CacheRecord(**payload)
        except (OSError, ValueError, TypeError):
            return CacheProbe(True, False, data_path, meta_path, None)

        fresh = (
            record.remote_path == str(entry.path)
            and record.size == entry.size
            and _mtime_equal(record.mtime, entry.mtime)
            and data_path.exists()
        )
        return CacheProbe(True, fresh, data_path, meta_path, record)

    def ensure_cached(
        self,
        host: str,
        entry: RemoteEntry,
        downloader: Callable[[Path], None],
    ) -> Path:
        probe = self.probe(host, entry)
        if probe.is_fresh:
            return probe.data_path

        probe.data_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            prefix=f"{probe.data_path.stem}.",
            suffix=".tmp",
            dir=probe.data_path.parent,
            delete=False,
        ) as handle:
            tmp_path = Path(handle.name)

        try:
            downloader(tmp_path)
            os.replace(tmp_path, probe.data_path)
            record = CacheRecord(
                remote_path=str(entry.path),
                size=entry.size,
                mtime=entry.mtime,
                host=host,
            )
            probe.meta_path.write_text(json.dumps(asdict(record), indent=2), encoding="utf-8")
            return probe.data_path
        finally:
            if tmp_path.exists():
                tmp_path.unlink(missing_ok=True)

    def clear(self) -> None:
        if not self.base_dir.exists():
            return
        for child in sorted(self.base_dir.glob("**/*"), reverse=True):
            if child.is_file():
                child.unlink(missing_ok=True)
            elif child.is_dir():
                child.rmdir()
        self.base_dir.rmdir()

    def paths_for(self, host: str, remote_path: str) -> tuple[Path, Path]:
        digest = hashlib.sha256(remote_path.encode("utf-8")).hexdigest()
        basename = Path(remote_path).name or "root"
        target_dir = self.base_dir / host / digest
        return target_dir / basename, target_dir / "cache.json"


def _mtime_equal(left: float | None, right: float | None) -> bool:
    if left is None or right is None:
        return left == right
    return abs(left - right) < 1e-6

