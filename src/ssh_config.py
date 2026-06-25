from __future__ import annotations

from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

import paramiko

from .models import ResolvedHostConfig

SUPPORTED_DIRECTIVES = {
    "host",
    "hostname",
    "user",
    "port",
    "identityfile",
    "identitiesonly",
    "stricthostkeychecking",
}

UNSUPPORTED_DIRECTIVES = {
    "include",
    "match",
    "proxycommand",
    "proxyjump",
    "requesttty",
    "forwardx11",
    "forwardx11trusted",
    "forwardagent",
    "xauthlocation",
    "preferredauthentications",
    "gssapiauthentication",
    "remoteforward",
    "localforward",
    "dynamicforward",
}


def load_aliases(config_path: Path | None = None) -> list[str]:
    path = _default_config_path(config_path)
    if not path.exists():
        return []

    aliases: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, _, value = stripped.partition(" ")
        if key.lower() != "host":
            continue
        for candidate in value.split():
            if any(token in candidate for token in ("*", "?", "!")):
                continue
            if candidate not in aliases:
                aliases.append(candidate)
    return aliases


def resolve_host(
    alias: str,
    *,
    config_path: Path | None = None,
    root: str | PurePosixPath = "/",
    host_override: str | None = None,
    user_override: str | None = None,
    port_override: int | None = None,
) -> ResolvedHostConfig:
    path = _default_config_path(config_path)
    lookup = {}
    if path.exists():
        ssh_config = paramiko.SSHConfig()
        with path.open(encoding="utf-8") as handle:
            ssh_config.parse(handle)
        lookup = ssh_config.lookup(alias)

    hostname = host_override or lookup.get("hostname") or alias
    user = user_override or lookup.get("user")
    port = port_override or _coerce_int(lookup.get("port"), 22)
    identity_files = tuple(
        Path(candidate).expanduser()
        for candidate in _as_list(lookup.get("identityfile"))
    )
    identities_only = _coerce_bool(lookup.get("identitiesonly"), False)
    strict_host_key_checking = _coerce_strict_host_key_checking(lookup.get("stricthostkeychecking"))
    unsupported = tuple(scan_unsupported_options(alias, config_path=path))

    warnings = []
    if unsupported:
        warnings.append(
            "Phase 1 ignores unsupported SSH options for this alias: " + ", ".join(sorted(set(unsupported)))
        )
    if not strict_host_key_checking:
        warnings.append("StrictHostKeyChecking is disabled for this alias; unknown host keys will be added locally.")

    return ResolvedHostConfig(
        alias=alias,
        hostname=hostname,
        user=user,
        port=port,
        root=PurePosixPath(str(root or "/")),
        identity_files=identity_files,
        identities_only=identities_only,
        strict_host_key_checking=strict_host_key_checking,
        warnings=tuple(warnings),
        unsupported_options=unsupported,
        config_path=path if path.exists() else None,
    )


def normalize_connection_overrides(
    alias: str,
    *,
    host_input: str | None = None,
    user_input: str | None = None,
    port_input: str | int | None = None,
    config_path: Path | None = None,
) -> tuple[str | None, str | None, int | None]:
    host_override = (host_input or "").strip() or None
    user_override = (user_input or "").strip() or None

    if port_input in (None, ""):
        port_override = None
    else:
        port_override = int(port_input)

    if not alias:
        return host_override, user_override, port_override

    base = resolve_host(alias, config_path=config_path)
    if host_override in {alias, base.hostname}:
        host_override = None
    if user_override == (base.user or None):
        user_override = None
    if port_override == base.port:
        port_override = None
    return host_override, user_override, port_override


def scan_unsupported_options(alias: str, *, config_path: Path | None = None) -> list[str]:
    path = _default_config_path(config_path)
    if not path.exists():
        return []

    matches = []
    current_patterns: list[str] = ["*"]
    global_applicable = True

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue

        key, _, remainder = stripped.partition(" ")
        directive = key.lower()
        value = remainder.strip()

        if directive == "host":
            current_patterns = value.split()
            global_applicable = _matches_host(alias, current_patterns)
            continue

        if directive in {"include", "match"}:
            matches.append(directive)
            continue

        if not global_applicable:
            continue

        if directive in UNSUPPORTED_DIRECTIVES:
            matches.append(directive)

    return matches


def _default_config_path(config_path: Path | None) -> Path:
    if config_path is not None:
        return config_path
    return Path.home() / ".ssh" / "config"


def _matches_host(alias: str, patterns: list[str]) -> bool:
    matched = False
    for pattern in patterns:
        inverted = pattern.startswith("!")
        token = pattern[1:] if inverted else pattern
        if fnmatchcase(alias, token):
            if inverted:
                return False
            matched = True
    return matched


def _as_list(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    return [str(value)]


def _coerce_int(value: object, default: int) -> int:
    if value in (None, ""):
        return default
    return int(value)


def _coerce_bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    return str(value).strip().lower() in {"yes", "true", "1", "on"}


def _coerce_strict_host_key_checking(value: object) -> bool:
    if value is None:
        return True
    lowered = str(value).strip().lower()
    return lowered not in {"no", "false", "off", "0"}
