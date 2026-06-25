from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable

from .navigation import normalize_browse_path
from .opening import COMMAND_FILE_SUFFIX


_FETCH_SPEC_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_+-]*:[^/\\].*$")
_WINDOWS_DRIVE_RE = re.compile(r"^[A-Za-z]:[/\\]")
_REMOTE_GLOB_CHARS = set("*?[]{}")


class CommandFileRewriteError(RuntimeError):
    pass


class NestedCommandFileError(CommandFileRewriteError):
    pass


@dataclass(frozen=True, slots=True)
class CommandToken:
    value: str
    start: int
    end: int


def rewrite_command_file_text(
    text: str,
    remote_path: PurePosixPath,
    rewrite_remote_target: Callable[[PurePosixPath], Path],
) -> str:
    script_dir = remote_path.parent
    rewritten_lines: list[str] = []

    for line_number, line in enumerate(text.splitlines(keepends=True), start=1):
        body, newline = _split_line_ending(line)
        rewritten_parts = [
            _rewrite_command_part(part, line_number=line_number, script_dir=script_dir, rewrite_remote_target=rewrite_remote_target)
            for part in _split_semicolon_commands(body)
        ]
        rewritten_lines.append(";".join(rewritten_parts) + newline)

    return "".join(rewritten_lines)


def quote_command_path(path: Path | str) -> str:
    text = path.as_posix() if isinstance(path, Path) else str(path)
    escaped = text.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _rewrite_command_part(
    part: str,
    *,
    line_number: int,
    script_dir: PurePosixPath,
    rewrite_remote_target: Callable[[PurePosixPath], Path],
) -> str:
    if not part.strip():
        return part

    stripped = part.lstrip()
    if stripped.startswith("#"):
        return part

    leading_len = len(part) - len(stripped)
    trailing = part[len(part.rstrip(" \t")) :]
    core = part[leading_len : len(part) - len(trailing)] if trailing else part[leading_len:]
    tokens = _tokenize_command(core, line_number=line_number)
    if not tokens or tokens[0].value.lower() != "open":
        return part

    replacements: list[tuple[int, int, str]] = []
    for token in tokens[1:]:
        if not _looks_like_remote_open_target(token.value):
            break
        remote_target = _resolve_remote_target(token.value, script_dir, line_number=line_number)
        if remote_target.suffix.lower() == COMMAND_FILE_SUFFIX:
            raise NestedCommandFileError(
                f"Nested .cxc open is not supported in remote command files: {remote_target} (line {line_number})"
            )
        try:
            local_path = rewrite_remote_target(remote_target)
        except Exception as exc:
            raise CommandFileRewriteError(
                f"Could not cache remote open target {remote_target} referenced from {script_dir} (line {line_number}): {exc}"
            ) from exc
        replacements.append((token.start, token.end, quote_command_path(local_path)))

    if not replacements:
        return part

    rebuilt = []
    cursor = 0
    for start, end, replacement in replacements:
        rebuilt.append(core[cursor:start])
        rebuilt.append(replacement)
        cursor = end
    rebuilt.append(core[cursor:])
    return f"{part[:leading_len]}{''.join(rebuilt)}{trailing}"


def _tokenize_command(text: str, *, line_number: int) -> list[CommandToken]:
    tokens: list[CommandToken] = []
    length = len(text)
    index = 0

    while index < length:
        while index < length and text[index].isspace():
            index += 1
        if index >= length:
            break

        start = index
        chars: list[str] = []
        if text[index] in {"'", '"'}:
            quote = text[index]
            index += 1
            while index < length:
                char = text[index]
                if char == "\\" and index + 1 < length:
                    chars.append(text[index + 1])
                    index += 2
                    continue
                if char == quote:
                    index += 1
                    break
                chars.append(char)
                index += 1
            else:
                raise CommandFileRewriteError(f"Unterminated quote in command file on line {line_number}.")
        else:
            while index < length and not text[index].isspace():
                char = text[index]
                if char == "\\" and index + 1 < length:
                    chars.append(text[index + 1])
                    index += 2
                    continue
                chars.append(char)
                index += 1

        tokens.append(CommandToken(value="".join(chars), start=start, end=index))

    return tokens


def _split_line_ending(line: str) -> tuple[str, str]:
    if line.endswith("\r\n"):
        return line[:-2], "\r\n"
    if line.endswith("\n"):
        return line[:-1], "\n"
    return line, ""


def _split_semicolon_commands(line: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escape = False

    for char in line:
        if escape:
            current.append(char)
            escape = False
            continue
        if quote is not None:
            current.append(char)
            if char == "\\":
                escape = True
            elif char == quote:
                quote = None
            continue
        if char in {"'", '"'}:
            quote = char
            current.append(char)
            continue
        if char == ";":
            parts.append("".join(current))
            current = []
            continue
        current.append(char)

    parts.append("".join(current))
    return parts


def _looks_like_remote_open_target(token: str) -> bool:
    if not token or token.lower() == "browse":
        return False
    if token.startswith(("http://", "https://", "ftp://")):
        return False
    if _WINDOWS_DRIVE_RE.match(token):
        return False
    if _FETCH_SPEC_RE.match(token):
        return False
    if token.startswith("#"):
        return False
    if token.startswith("~"):
        return True
    normalized = token.replace("\\", "/")
    return normalized.startswith("/") or "/" in normalized or PurePosixPath(normalized).suffix != ""


def _resolve_remote_target(token: str, script_dir: PurePosixPath, *, line_number: int) -> PurePosixPath:
    if token.startswith("~"):
        raise CommandFileRewriteError(
            f"Tilde-prefixed paths are not supported in remote command files: {token} (line {line_number})"
        )
    if any(char in token for char in _REMOTE_GLOB_CHARS):
        raise CommandFileRewriteError(
            f"Wildcard open targets are not supported in remote command files: {token} (line {line_number})"
        )
    return normalize_browse_path(script_dir, token.replace("\\", "/"))
