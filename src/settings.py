import json

from chimerax.core.configfile import Value
from chimerax.core.settings import Settings


class CryoRemoteSettings(Settings):
    AUTO_SAVE = {
        "preferred_alias": "",
        "preferred_host": "",
        "preferred_user": "",
        "preferred_port": 22,
        "preferred_root": "/",
        "cache_dir": "",
        "remembered_targets": Value({}, json.loads, json.dumps),
    }
