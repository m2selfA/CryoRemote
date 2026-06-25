# vim: set expandtab shiftwidth=4 softtabstop=4:

from __future__ import annotations

try:
    from chimerax.core.toolshed import BundleAPI
except ImportError:  # pragma: no cover - exercised outside ChimeraX
    class BundleAPI:  # type: ignore[override]
        api_version = 1


class _CryoRemoteAPI(BundleAPI):
    api_version = 1

    @staticmethod
    def start_tool(session, bi, ti):
        if ti.name != "CryoRemote":
            raise ValueError(f"Unsupported tool requested: {ti.name}")

        from .tool import CryoRemoteTool

        return CryoRemoteTool.get_singleton(session, create=True, display=True)

    @staticmethod
    def register_command(bi, ci, logger):
        from . import cmd

        cmd.register_command(ci.name, logger)

    @staticmethod
    def run_provider(session, name, mgr, **kw):
        from . import cmd

        return cmd.run_provider(session, name, display_name=kw.get("display_name"))


bundle_api = _CryoRemoteAPI()
