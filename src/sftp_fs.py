from __future__ import annotations

import socket
from pathlib import Path, PurePosixPath
from stat import S_ISDIR
from typing import Callable, Iterable

import paramiko
from paramiko.hostkeys import HostKeyEntry, InvalidHostKey

from .models import RemoteEntry, ResolvedHostConfig

PromptHandler = Callable[[str, str, list[tuple[str, bool]]], list[str]]


class SFTPConnectionError(RuntimeError):
    pass


class ParamikoSFTPFileSystem:
    def __init__(
        self,
        config: ResolvedHostConfig,
        *,
        password: str | None = None,
        prompt_handler: PromptHandler | None = None,
        timeout: float = 15.0,
    ):
        self.config = config
        self.password = password
        self.prompt_handler = prompt_handler
        self.timeout = timeout
        self._transport: paramiko.Transport | None = None
        self._sftp: paramiko.SFTPClient | None = None

    def connect(self) -> None:
        if self._transport is not None:
            return

        sock = socket.create_connection((self.config.hostname, self.config.port), timeout=self.timeout)
        transport = paramiko.Transport(sock)
        try:
            transport.start_client(timeout=self.timeout)
            self._verify_host_key(transport)
            self._authenticate(transport)
            self._sftp = paramiko.SFTPClient.from_transport(transport)
            self._transport = transport
        except Exception:
            transport.close()
            raise

    def close(self) -> None:
        if self._sftp is not None:
            self._sftp.close()
            self._sftp = None
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    def info(self, path: str | PurePosixPath) -> RemoteEntry:
        client = self._require_sftp()
        target = PurePosixPath(str(path))
        attr = client.stat(str(target))
        entry_type = "directory" if S_ISDIR(attr.st_mode) else "file"
        return RemoteEntry(
            path=target,
            entry_type=entry_type,
            size=getattr(attr, "st_size", None),
            mtime=float(getattr(attr, "st_mtime", 0)) if getattr(attr, "st_mtime", None) is not None else None,
        )

    def ls(self, path: str | PurePosixPath) -> list[RemoteEntry]:
        client = self._require_sftp()
        target = PurePosixPath(str(path))
        results = []
        for attr in client.listdir_attr(str(target)):
            child = target / attr.filename
            entry_type = "directory" if S_ISDIR(attr.st_mode) else "file"
            results.append(
                RemoteEntry(
                    path=child,
                    entry_type=entry_type,
                    size=getattr(attr, "st_size", None),
                    mtime=float(getattr(attr, "st_mtime", 0))
                    if getattr(attr, "st_mtime", None) is not None
                    else None,
                )
            )
        return sorted(results, key=lambda item: (item.entry_type != "directory", item.name.lower()))

    def read_bytes_head(self, path: str | PurePosixPath, limit: int = 65536) -> bytes:
        client = self._require_sftp()
        with client.open(str(PurePosixPath(str(path))), "rb") as handle:
            return handle.read(limit)

    def read_text_head(self, path: str | PurePosixPath, limit: int = 65536, encoding: str = "utf-8") -> str:
        return self.read_bytes_head(path, limit=limit).decode(encoding, errors="replace")

    def read_head(self, path: str | PurePosixPath, limit: int = 65536) -> bytes:
        return self.read_bytes_head(path, limit=limit)

    def get_file(self, remote_path: str | PurePosixPath, local_path: Path) -> None:
        client = self._require_sftp()
        client.get(str(PurePosixPath(str(remote_path))), str(local_path))

    def _authenticate(self, transport: paramiko.Transport) -> None:
        username = self.config.user
        if not username:
            raise SFTPConnectionError("A username is required to connect.")

        explicit_keys = list(_load_private_keys(self.config.identity_files, self.password))
        for key in explicit_keys:
            try:
                transport.auth_publickey(username, key)
                if transport.is_authenticated():
                    return
            except paramiko.SSHException:
                continue

        agent = paramiko.Agent()
        for key in agent.get_keys():
            try:
                transport.auth_publickey(username, key)
                if transport.is_authenticated():
                    return
            except paramiko.SSHException:
                continue

        if not self.config.identities_only:
            for key in _load_private_keys(_default_key_files(), self.password):
                try:
                    transport.auth_publickey(username, key)
                    if transport.is_authenticated():
                        return
                except paramiko.SSHException:
                    continue

        if self.password:
            try:
                transport.auth_password(username, self.password)
                if transport.is_authenticated():
                    return
            except paramiko.AuthenticationException:
                pass

        if self.prompt_handler is not None:
            try:
                transport.auth_interactive(username, self._interactive_handler)
                if transport.is_authenticated():
                    return
            except paramiko.AuthenticationException:
                pass

        raise SFTPConnectionError("Authentication failed.")

    def _interactive_handler(self, title: str, instructions: str, prompts: list[tuple[str, bool]]) -> list[str]:
        if self.prompt_handler is None:
            if self.password is not None:
                return [self.password for _prompt, _echo in prompts]
            raise SFTPConnectionError("Keyboard-interactive authentication requires a prompt handler.")
        return self.prompt_handler(title, instructions, prompts)

    def _verify_host_key(self, transport: paramiko.Transport) -> None:
        server_key = transport.get_remote_server_key()
        known_hosts_path = Path.home() / ".ssh" / "known_hosts"
        host_keys = _load_known_hosts(known_hosts_path)

        candidates = _host_key_candidates(self.config)
        if any(host_keys.check(candidate, server_key) for candidate in candidates):
            return

        if self.config.strict_host_key_checking:
            raise SFTPConnectionError(
                "Host key is unknown or does not match known_hosts for "
                f"{self.config.hostname}:{self.config.port}."
            )

        primary = candidates[0]
        host_keys.add(primary, server_key.get_name(), server_key)
        known_hosts_path.parent.mkdir(parents=True, exist_ok=True)
        host_keys.save(str(known_hosts_path))

    def _require_sftp(self) -> paramiko.SFTPClient:
        if self._sftp is None:
            raise SFTPConnectionError("Not connected.")
        return self._sftp


def _load_private_keys(paths: Iterable[Path], password: str | None) -> list[paramiko.PKey]:
    keys: list[paramiko.PKey] = []
    seen = set()
    for path in paths:
        resolved = path.expanduser()
        if not resolved.exists() or resolved in seen:
            continue
        seen.add(resolved)
        key = _load_private_key(resolved, password=password)
        if key is not None:
            keys.append(key)
    return keys


def _load_private_key(path: Path, password: str | None) -> paramiko.PKey | None:
    key_types = (
        getattr(paramiko, "RSAKey", None),
        getattr(paramiko, "ECDSAKey", None),
        getattr(paramiko, "Ed25519Key", None),
        getattr(paramiko, "DSSKey", None),
    )
    for key_type in key_types:
        if key_type is None:
            continue
        try:
            return key_type.from_private_key_file(str(path), password=password)
        except (FileNotFoundError, PermissionError, paramiko.SSHException, ValueError):
            continue
    return None


def _default_key_files() -> tuple[Path, ...]:
    home = Path.home() / ".ssh"
    return tuple(home / name for name in ("id_rsa", "id_ed25519", "id_ecdsa", "id_dsa"))


def _host_key_candidates(config: ResolvedHostConfig) -> tuple[str, ...]:
    hostnames = [config.hostname, config.alias]
    values: list[str] = []
    for name in hostnames:
        if config.port != 22:
            values.append(f"[{name}]:{config.port}")
        values.append(name)
    deduped = []
    for value in values:
        if value not in deduped:
            deduped.append(value)
    return tuple(deduped)


def _load_known_hosts(path: Path) -> paramiko.HostKeys:
    host_keys = paramiko.HostKeys()
    if not path.exists():
        return host_keys

    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        try:
            entry = HostKeyEntry.from_line(stripped, lineno)
        except InvalidHostKey:
            continue
        if entry is None:
            continue
        for hostname in entry.hostnames:
            host_keys.add(hostname, entry.key.get_name(), entry.key)
    return host_keys
