"""Telling a secret from an ordinary value in configuration init adopts (ADR 0030).

A secret found is adopted as it is and named in a warning, so this decides only what
the warning says. One vocabulary serves every place a secret can sit: a key, a flag,
a `name = value` label, a value under `env` or `headers`, or a value whose own shape
gives it away.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from itertools import pairwise

from .errors import LoadoutError
from .native_documents import parse_document

# Names that mark a value as a secret, matched against the last words of a key, a flag
# or a label: `apiKey`, `X-API-KEY`, `--api-key` and `api_key =` all end in "api key",
# while `--api-key-file`, `max_tokens` and `auth_env_var` end in something else.
SECRET_NAMES = (
    "token",
    "api key",
    "secret",
    "password",
    "passwd",
    "passphrase",
    "pass",
    "credential",
    "credentials",
    "authorization",
    "auth",
    "access key",
    "private key",
    "pat",
)
_SECRET_WORDS = tuple(tuple(name.split()) for name in SECRET_NAMES)

# A reference a harness resolves when it runs, so the value is not the secret itself.
ENV_REFERENCE = re.compile(
    r"(?:Bearer )?(?:\$\{[^}]+\}|\{env:[^}]+\}|\$env:[A-Za-z_]\w*|\$[A-Za-z_]\w*)"
)


@dataclass(frozen=True)
class Shape:
    name: str
    pattern: str
    reason: str
    # What a documentation placeholder keeps of the real thing.
    prefixes: tuple[str, ...] = ()


SHAPES = (
    Shape("pem", r"-----BEGIN [A-Z ]*PRIVATE KEY-----", "a private key"),
    Shape(
        "token",
        r"\b(?:(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}|glpat-[A-Za-z0-9_-]{20,}"
        r"|npm_[A-Za-z0-9]{30,}|sk-(?:ant-)?[A-Za-z0-9_-]{16,}|(?:AKIA|ASIA)[0-9A-Z]{16}"
        r"|AIza[0-9A-Za-z_-]{30,}|xox[abpe]-[A-Za-z0-9-]{10,}|xapp-[A-Za-z0-9-]{10,}"
        r"|eyJ[\w-]{8,}\.eyJ[\w-]{8,}\.[\w-]{8,})\b",
        "a token in a provider's format",
        (
            "sk-ant-",
            "sk-",
            "ghp_",
            "gho_",
            "ghu_",
            "ghs_",
            "ghr_",
            "github_pat_",
            "glpat-",
            "npm_",
            "AKIA",
            "ASIA",
            "AIza",
            "xoxa-",
            "xoxb-",
            "xoxp-",
            "xoxe-",
            "xapp-",
        ),
    ),
    Shape(
        "userinfo",
        r"\b[a-z][a-z0-9+.-]*://(?:[^/\s:@]+:[^/\s@]+|[^/\s:@]{20,})@",
        "credentials in a URL",
    ),
    Shape(
        "basic",
        r"(?i:\bauthorization)\s*[:=]\s*[\"']?Basic\s+[A-Za-z0-9+/]{8,}=*",
        "an HTTP Basic credential",
    ),
    Shape(
        "userpass",
        r"\b(?:curl|wget|http)\b[^\n]*?\s(?:-u|--user)[ =][^\s:]+:\S+",
        "a user:password on an HTTP client's command line",
    ),
    Shape("aws", r"(?i:\baws_secret_access_key)\s*[:=]\s*\S+", "an aws_secret_access_key"),
    Shape(
        "query",
        r"[?&](?:key|api_key|apikey|access_token|token)=[^&\s\"']{8,}",
        "a key in a URL query",
    ),
)
_SHAPES = re.compile("|".join(f"(?P<{s.name}>{s.pattern})" for s in SHAPES))
_REASONS = {s.name: s.reason for s in SHAPES}
_PREFIXES = tuple(sorted({p for s in SHAPES for p in s.prefixes}, key=len, reverse=True))

# Starts only where a run of name characters starts: a start inside one rescans the
# rest of the run, which made one long word quadratic.
_NAME = r"(?<![A-Za-z0-9_-])[A-Za-z][A-Za-z0-9_-]*"
_LABELLED = re.compile(
    rf"(?i)\bbearer\s+(?P<bearer>[\w.+/=-]+)|(?P<label>{_NAME})\s*[:=]\s*[\"']?(?P<value>[\w.+/=$:{{}}-]+)"
)

VALUE_MAPS = frozenset({"env", "headers", "http_headers", "environment"})
PLAIN_HEADERS = frozenset(
    {"accept", "accept-encoding", "accept-language", "cache-control", "content-type", "user-agent"}
)
PLAIN_VARIABLE = re.compile(
    r"(?i)^(?:PATH|PATH_\w*|\w*_PATH|HOME|LANG|LC_\w+|TERM|TZ|SHELL|\w*_ENV|DEBUG|\w*LOG_LEVEL"
    r"|DISABLE_\w*|ENABLE_\w*|NO_COLOR|FORCE_COLOR|CI)$"
)
PLAIN_VALUE = re.compile(r"(?i)^(?:-?\d+(?:\.\d+)?|true|false|yes|no|on|off|[~.]?/.*)$")
LABELLED_REASON = "a value labelled as a secret"
# A NUL this early is binary content, the check git itself uses; past the cap a file is
# an asset or a dump, and reading all of it cost more than the rest of init together.
BINARY_PROBE = 8192
MAX_SCANNED = 1 << 20


def names_a_secret(name: str) -> bool:
    """Whether a key, flag or label's last words are one of `SECRET_NAMES`."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    words = tuple(word.lower() for word in re.split(r"[^A-Za-z0-9]+", spaced) if word)
    return any(words[-len(secret) :] == secret for secret in _SECRET_WORDS)


def reference(value: str) -> bool:
    return bool(ENV_REFERENCE.fullmatch(value))


def _placeholder(found: str) -> bool:
    """A documentation stand-in: AWS's `…EXAMPLE` keys, or a run of x's after a prefix."""
    core = next((found[len(p) :] for p in _PREFIXES if found.startswith(p)), found)
    return found.endswith("EXAMPLE") or set(core.replace("-", "").replace("_", "")) <= {"x", "X"}


def _secret_value(value: str) -> bool:
    """A literal worth warning about: not a reference, a switch, a number, a path or a
    placeholder."""
    return bool(value) and not (reference(value) or PLAIN_VALUE.match(value) or _placeholder(value))


def findings(content: bytes, format_name: str = "copy") -> list[tuple[str, str]]:
    """Each secret `content` seems to hold, as (why, the value itself). Binary content
    holds none, and only the first `MAX_SCANNED` bytes are read."""
    if b"\0" in content[:BINARY_PROBE]:
        return []
    text = content[:MAX_SCANNED].decode("utf-8", errors="replace")
    found = [
        (_REASONS[match.lastgroup or "token"], match.group(0))
        for match in _SHAPES.finditer(text)
        if not _placeholder(match.group(0))
    ]
    for match in _LABELLED.finditer(text):
        value = match.group("bearer") or match.group("value")
        named = match.group("bearer") is not None or names_a_secret(match.group("label"))
        if named and _secret_value(value):
            found.append((LABELLED_REASON, value))
    if format_name in {"json", "toml"} and len(content) <= MAX_SCANNED:
        try:
            document = parse_document(text, format_name)
        except LoadoutError:
            return found
        found.extend(_structured(document))
    return found


def credential_material(content: bytes, format_name: str = "copy") -> str | None:
    """Why `content` looks like it holds a secret, or None when it does not."""
    return next((why for why, _ in findings(content, format_name)), None)


def redact(text: str, values: Iterable[str]) -> str:
    """`text` with each secret value replaced, so a diff never repeats one."""
    for value in sorted(set(values), key=len, reverse=True):
        text = text.replace(value, "<redacted>")
    return text


def _structured(value: object, key: str = "") -> Iterator[tuple[str, str]]:
    if isinstance(value, dict):
        for name, item in value.items():
            yield from _entry(key, str(name), item)
    elif isinstance(value, list):
        for item in value:
            yield from _structured(item, key)
        yield from _flagged([item for item in value if isinstance(item, str)])
    elif isinstance(value, str) and value:
        if names_a_secret(key) and _secret_value(value):
            yield f"{key} holds a literal value", value
        else:
            yield from _flagged(value.split())


def _entry(key: str, name: str, item: object) -> Iterator[tuple[str, str]]:
    if key.lower() in VALUE_MAPS and isinstance(item, str):
        if not _plain_entry(name, item):
            yield f"{key}.{name}", item
        return
    yield from _structured(item, name)


def _plain_entry(name: str, value: str) -> bool:
    """An environment or header entry that cannot be a secret: a number, a switch, a
    path, a reference, or a name that never holds one."""
    return (
        not _secret_value(value)
        or name.lower() in PLAIN_HEADERS
        or bool(PLAIN_VARIABLE.match(name))
    )


def _flagged(words: list[str]) -> Iterator[tuple[str, str]]:
    """The value after a `--token`, `--api-key` and the like, or given as `--token=…`,
    in an argument list or a command line."""
    for word in words:
        flag, _, inline = word.partition("=")
        if inline and _secret_flag(flag) and _literal(inline):
            yield f"the value of {flag}", inline
    for flag, value in pairwise(words):
        if _secret_flag(flag) and _literal(value):
            yield f"the value after {flag}", value


def _secret_flag(word: str) -> bool:
    return word.startswith("-") and names_a_secret(word.lstrip("-"))


def _literal(value: str) -> bool:
    return bool(value) and not value.startswith("-") and not reference(value)
