"""Telling a secret from an ordinary value in configuration init adopts (ADR 0030).

A secret found is adopted as it is and named in a warning, so this decides only what
the warning says. One vocabulary serves every place a secret can sit: a key, a flag,
a `name = value` label, a value under `env` or `headers`, or a value whose own shape
gives it away.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator
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
    "secret key",
    "client secret",
    "signing key",
    "pat",
)
_SECRET_WORDS = tuple(tuple(name.split()) for name in SECRET_NAMES)
# A provider's name followed by `key` names that provider's key: `OPENAI_KEY`, `stripeKey`.
PROVIDERS = frozenset(
    {
        "anthropic",
        "cloudflare",
        "cohere",
        "datadog",
        "deepseek",
        "gemini",
        "github",
        "gitlab",
        "google",
        "groq",
        "hf",
        "huggingface",
        "jina",
        "mailgun",
        "mistral",
        "openai",
        "openrouter",
        "replicate",
        "sendgrid",
        "sentry",
        "slack",
        "stripe",
        "supabase",
        "together",
        "twilio",
        "vercel",
    }
)
# Labels whose value may begin with a one-word scheme, as an HTTP Authorization does.
_AUTHORIZATION = frozenset({("authorization",), ("auth",)})

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
    # To its END line, or to the end of the text: the body is the secret, not the header.
    Shape(
        "pem",
        r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|\Z)",
        "a private key",
    ),
    Shape(
        "token",
        r"\b(?:(?:gh[pousr]_|github_pat_)[A-Za-z0-9_]{20,}|glpat-[A-Za-z0-9_-]{20,}"
        r"|npm_[A-Za-z0-9]{30,}|sk-(?:ant-)?[A-Za-z0-9_-]{16,}|(?:AKIA|ASIA)[0-9A-Z]{16}"
        r"|AIza[0-9A-Za-z_-]{30,}|xox[abpe]-[A-Za-z0-9-]{10,}|xapp-[A-Za-z0-9-]{10,}"
        r"|(?:sk|rk)_(?:live|test)_[A-Za-z0-9]{16,}|hf_[A-Za-z0-9]{30,}"
        r"|django-insecure-[^\s\"']{20,}"
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
            "sk_live_",
            "sk_test_",
            "rk_live_",
            "rk_test_",
            "hf_",
            "django-insecure-",
        ),
    ),
    Shape(
        "webhook",
        r"\bhttps://hooks\.slack\.com/services/[A-Za-z0-9_/]{20,}",
        "a Slack webhook URL",
    ),
    Shape(
        "userinfo",
        # Starts only where a run of scheme characters starts, as `_NAME` does below.
        r"(?<![A-Za-z0-9+.-])[a-z][a-z0-9+.-]*://(?:[^/\s:@]+:[^/\s@]+|[^/\s:@]{20,})@",
        "credentials in a URL",
    ),
    Shape(
        "basic",
        r"(?i:\bauthorization)\s*[:=]\s*[\"']?Basic\s+[A-Za-z0-9+/]{8,}=*",
        "an HTTP Basic credential",
    ),
    Shape(
        "userpass",
        # A bounded gap: unbounded, every `http` on one long line scanned to its end.
        r"\b(?:curl|wget|http)\b[^\n]{0,256}?\s(?:-u|--user)[ =][^\s:]+:\S+",
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
# A label may be quoted, as a JSON or YAML key is. Its first word decides whether it is a
# secret; the value it labels runs to its closing quote, or to the end of the line.
# A label and its value share a line: a parent key such as `env:` labels nothing on the
# next line (review 6, batch 1.1).
_LABELLED = re.compile(
    rf"(?i)\bbearer[ \t]+(?P<bearer>[\w.+/=-]+)"
    rf"|(?P<label>{_NAME})[\"']?[ \t]*[:=](?P<gap>[ \t]*)(?P<quote>[\"']?)(?P<value>[^\s\"']+)"
)
_SCHEME_VALUE = re.compile(r"[ \t]+(?P<value>[^\s\"']+)")

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


def _words(name: str) -> tuple[str, ...]:
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", name)
    return tuple(word.lower() for word in re.split(r"[^A-Za-z0-9]+", spaced) if word)


def names_a_secret(name: str) -> bool:
    """Whether a key, flag or label's last words are one of `SECRET_NAMES`, or a provider
    followed by `key`."""
    words = _words(name)
    provider_key = words[-1:] == ("key",) and any(word in PROVIDERS for word in words[-2:-1])
    return provider_key or any(words[-len(secret) :] == secret for secret in _SECRET_WORDS)


def reference(value: str) -> bool:
    return bool(ENV_REFERENCE.fullmatch(value))


def _placeholder(found: str) -> bool:
    """A documentation stand-in: AWS's `…EXAMPLE` keys, or a run of x's after a prefix."""
    core = next((found[len(p) :] for p in _PREFIXES if found.startswith(p)), found)
    return found.endswith("EXAMPLE") or set(core.replace("-", "").replace("_", "")) <= {"x", "X"}


def _secret_value(value: str) -> bool:
    """A literal worth warning about: not a reference, a switch, a number, a path or a
    placeholder. The empty string counts as a placeholder."""
    return not (reference(value) or PLAIN_VALUE.match(value) or _placeholder(value))


def findings(content: bytes, format_name: str = "copy") -> list[tuple[str, str]]:
    """Each secret `content` seems to hold, as (why, the value itself). Binary content
    holds none, and only the first `MAX_SCANNED` bytes are read."""
    if b"\0" in content[:BINARY_PROBE]:
        return []
    text = content[:MAX_SCANNED].decode("utf-8", errors="replace")
    return _flagged_in(text, format_name, structured=len(content) <= MAX_SCANNED)


def secret_reason(text: str, format_name: str = "copy") -> str | None:
    """Why `text` holds a secret, or None: whether a diff of it may be shown. The whole
    text is read, past `MAX_SCANNED` and past a NUL, as a diff prints all of it."""
    return next((why for why, _ in _flagged_in(text, format_name, structured=True)), None)


def _flagged_in(text: str, format_name: str, *, structured: bool) -> list[tuple[str, str]]:
    """Each secret `text` holds, as (why, the value): by its shape, by its label, and in a
    JSON or TOML document by where it sits."""
    found = [
        (_REASONS[match.lastgroup or "token"], match.group(0))
        for match in _SHAPES.finditer(text)
        if not _placeholder(match.group(0))
    ]
    found += [(LABELLED_REASON, value) for value in _labelled_values(text)]
    found += list(_flag_values(text))
    if structured and format_name in {"json", "toml"}:
        found += _placed(text, format_name)
    return found


def _placed(text: str, format_name: str) -> list[tuple[str, str]]:
    """What a JSON or TOML document flags by where a value sits. A JSON document that does
    not parse is read for its value maps alone (review 6, batch 2.6)."""
    try:
        document = parse_document(text, format_name)
    except LoadoutError:
        return list(_unparsed_value_maps(text)) if format_name == "json" else []
    return list(_structured(document))


_JSON_STRING = r'"(?:[^"\\]|\\.)*"'
# A value map's object, read up to the first brace inside it or the end of the text.
_VALUE_MAP = re.compile(
    rf'(?i)"(?P<map>{"|".join(sorted(VALUE_MAPS))})"\s*:\s*\{{(?P<body>(?:[^{{}}"]|{_JSON_STRING})*)'
)
_JSON_PAIR = re.compile(rf"(?P<name>{_JSON_STRING})\s*:\s*(?P<value>{_JSON_STRING})")


def _unparsed_value_maps(text: str) -> Iterator[tuple[str, str]]:
    for found in _VALUE_MAP.finditer(text):
        for pair in _JSON_PAIR.finditer(found.group("body")):
            name, value = _json_string(pair.group("name")), _json_string(pair.group("value"))
            if not _plain_entry(name, value):
                yield f"{found.group('map')}.{name}", value


def _json_string(literal: str) -> str:
    try:
        decoded = json.loads(literal)
    except ValueError:
        return literal[1:-1]
    return decoded if isinstance(decoded, str) else literal[1:-1]


def credential_material(content: bytes, format_name: str = "copy") -> str | None:
    """Why `content` looks like it holds a secret, or None when it does not."""
    return next((why for why, _ in findings(content, format_name)), None)


def _labelled_values(text: str) -> Iterator[str]:
    """Each labelled secret's value: the word that decides it, or unquoted, its whole
    value (review 4, batch 1.3). After an authorization label a one-word scheme, such as
    `Token` or `Digest`, is skipped."""
    for match in _LABELLED.finditer(text):
        if match.group("bearer") is not None:
            decided, start, quote = match.group("bearer"), match.start("bearer"), ""
        elif names_a_secret(match.group("label")):
            decided, start, quote = match.group("value"), match.start("value"), match.group("quote")
            # `key: value` runs to the line's end, YAML style; `KEY=value`, shell style,
            # to the first whitespace (review 6, batch 3).
            if not quote:
                line = bool(match.group("gap"))
                decided = text[start : _value_end(text, start, quote, line=line)]
            scheme = _SCHEME_VALUE.match(text, match.end("value"))
            if _words(match.group("label"))[-1:] in _AUTHORIZATION and scheme is not None:
                decided, start = scheme.group("value"), scheme.start("value")
        else:
            continue
        if not quote and _BLOCK_SCALAR.fullmatch(decided):
            # A YAML block scalar: the value is the indented lines below its label.
            end = _block_end(text, match.start())
            body = text[text.find("\n", start) + 1 : end].strip()
            if body and _secret_value(body):
                yield body
        elif _secret_value(decided):
            yield decided


# A flag's value in text: after `=`, after whitespace on a command line, or as the next
# item of an argument array (review 6, batch 2.6).
_FLAG_VALUE = re.compile(
    r"(?<![\w-])(?P<flag>--?[A-Za-z][\w-]*)"
    r"(?:(?P<inline>[ \t]*=[ \t]*)|[ \t]+|[\"'][ \t]*,[ \t]*)"
    r"(?P<quote>[\"']?)(?P<value>[^\s\"',]+)"
)


def _flag_values(text: str) -> Iterator[tuple[str, str]]:
    """Each value a secret flag gives in text, as (why, the value), as `_flagged` names
    it in a document: to its closing quote, or, unquoted, to the end of the word."""
    for match in _FLAG_VALUE.finditer(text):
        flag, start, quote = match.group("flag"), match.start("value"), match.group("quote")
        if not _secret_flag(flag):
            continue
        value = text[start : _value_end(text, start, quote)] if quote else match.group("value")
        if _literal(value) and _secret_value(value):
            why = "of" if match.group("inline") is not None else "after"
            yield f"the value {why} {flag}", value


_BLOCK_SCALAR = re.compile(r"[|>](?:[-+]?[1-9]?|[1-9][-+])")


def _block_end(text: str, label: int) -> int:
    """Where a block scalar labelled at `label` ends: before the first line, blank lines
    aside, indented no deeper than the label's own."""
    line_start = text.rfind("\n", 0, label) + 1
    indent = len(text[line_start:label]) - len(text[line_start:label].lstrip(" "))
    position = text.find("\n", label)
    end = len(text) if position == -1 else position
    while position != -1:
        next_end = text.find("\n", position + 1)
        line = text[position + 1 : len(text) if next_end == -1 else next_end]
        if line.strip() and len(line) - len(line.lstrip(" ")) <= indent:
            break
        if line.strip():
            end = len(text) if next_end == -1 else next_end
        position = next_end
    return end


# A comment after a value that runs to the end of its line: a marker after whitespace.
_LINE_COMMENT = re.compile(r"[ \t](?:#|//)")
_WHITESPACE = re.compile(r"\s")


def _value_end(text: str, start: int, quote: str, *, line: bool = False) -> int:
    """The end of a value: its closing quote, unescaped; unquoted, the first whitespace,
    or with `line`, the line's end or a comment, trailing whitespace trimmed."""
    line_end = text.find("\n", start)
    line_end = len(text) if line_end == -1 else line_end
    position = start
    while quote and (closing := text.find(quote, position, line_end)) != -1:
        if text[closing - 1] != "\\":
            return closing
        position = closing + 1
    if quote:
        return start + len(text[start:line_end].rstrip())
    if line:
        comment = _LINE_COMMENT.search(text, max(start - 1, 0), line_end)
        end = line_end if comment is None else max(comment.start(), start)
        return start + len(text[start:end].rstrip())
    found = _WHITESPACE.search(text, start, line_end)
    return line_end if found is None else found.start()


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
