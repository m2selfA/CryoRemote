from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def load_bundle_package():
    root = Path(__file__).resolve().parents[1]
    src_dir = root / "src"
    module_name = "cryoremote_bundle"
    if module_name in sys.modules:
        return sys.modules[module_name]

    spec = importlib.util.spec_from_file_location(
        module_name,
        src_dir / "__init__.py",
        submodule_search_locations=[str(src_dir)],
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


load_bundle_package()

