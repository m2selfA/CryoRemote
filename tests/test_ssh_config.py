from __future__ import annotations

from pathlib import Path

import paramiko

from cryoremote_bundle.sftp_fs import _load_known_hosts
from cryoremote_bundle.ssh_config import (
    load_aliases,
    normalize_connection_overrides,
    resolve_host,
    scan_unsupported_options,
)


def test_load_aliases_skips_wildcards(tmp_path: Path):
    config = tmp_path / "config"
    config.write_text(
        "Host *\n    User base\n\nHost alpha beta*\n    HostName alpha.example\n",
        encoding="utf-8",
    )

    assert load_aliases(config) == ["alpha"]


def test_resolve_host_collects_unsupported_options(tmp_path: Path):
    config = tmp_path / "config"
    config.write_text(
        "\n".join(
            [
                "Host *",
                "    User base",
                "    ForwardX11 yes",
                "Host delta",
                "    HostName delta.example",
                "    Port 2200",
                "    IdentityFile ~/.ssh/id_rsa",
                "    StrictHostKeyChecking no",
                "    ProxyCommand corkscrew ...",
            ]
        ),
        encoding="utf-8",
    )

    resolved = resolve_host("delta", config_path=config)

    assert resolved.hostname == "delta.example"
    assert resolved.port == 2200
    assert resolved.identity_files[0].name == "id_rsa"
    assert resolved.strict_host_key_checking is False
    assert "proxycommand" in resolved.unsupported_options
    assert "forwardx11" in resolved.unsupported_options


def test_scan_unsupported_options_honors_host_patterns(tmp_path: Path):
    config = tmp_path / "config"
    config.write_text(
        "\n".join(
            [
                "Host alpha",
                "    ProxyJump jumpbox",
                "Host beta",
                "    RequestTTY yes",
            ]
        ),
        encoding="utf-8",
    )

    assert scan_unsupported_options("alpha", config_path=config) == ["proxyjump"]
    assert scan_unsupported_options("beta", config_path=config) == ["requesttty"]


def test_load_known_hosts_skips_invalid_cert_authority_lines(tmp_path: Path):
    known_hosts = tmp_path / "known_hosts"
    rsa_key = paramiko.RSAKey.generate(1024)
    known_hosts.write_text(
        "\n".join(
            [
                "@cert-authority uptermd.upterm.dev ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAICiecex8Dq718eSe1CCLgLvDmI7AagvCtax7brPFWkh4",
                f"example.com {rsa_key.get_name()} {rsa_key.get_base64()}",
            ]
        ),
        encoding="utf-8",
    )

    host_keys = _load_known_hosts(known_hosts)

    assert "example.com" in host_keys


def test_normalize_connection_overrides_ignores_alias_echo_and_matching_defaults(tmp_path: Path):
    config = tmp_path / "config"
    config.write_text(
        "\n".join(
            [
                "Host wmn02",
                "    HostName 10.100.10.10",
                "    User shark",
                "    Port 23523",
            ]
        ),
        encoding="utf-8",
    )

    host_override, user_override, port_override = normalize_connection_overrides(
        "wmn02",
        host_input="wmn02",
        user_input="shark",
        port_input="23523",
        config_path=config,
    )

    assert host_override is None
    assert user_override is None
    assert port_override is None


def test_normalize_connection_overrides_keeps_real_manual_overrides(tmp_path: Path):
    config = tmp_path / "config"
    config.write_text(
        "\n".join(
            [
                "Host wmn02",
                "    HostName 10.100.10.10",
                "    User shark",
                "    Port 23523",
            ]
        ),
        encoding="utf-8",
    )

    host_override, user_override, port_override = normalize_connection_overrides(
        "wmn02",
        host_input="10.100.10.20",
        user_input="other",
        port_input="2222",
        config_path=config,
    )

    assert host_override == "10.100.10.20"
    assert user_override == "other"
    assert port_override == 2222
