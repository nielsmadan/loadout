"""Telling a secret from an ordinary value in configuration init adopts (ADR 0030).

A secret found is adopted as it is and named in a warning, so this decides only what
the warning says. One vocabulary serves every place a secret can sit: a key, a flag,
a `name = value` label, a value under `env` or `headers`, or a value whose own shape
gives it away.
"""

from __future__ import annotations

import bisect
import itertools
import json
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


def _flagged_in(text: str, format_name: str, *, structured: bool) -> list[tuple[str, str]]:
    """Each secret `text` holds, as (why, the value): by its shape, by its label, and in a
    JSON or TOML document by where it sits. The scan caps what it reads; redaction does not."""
    found = [
        (_REASONS[match.lastgroup or "token"], match.group(0))
        for match in _SHAPES.finditer(text)
        if not _placeholder(match.group(0))
    ]
    found += [(LABELLED_REASON, value) for value, _, _ in _labelled_values(text)]
    found += [(why, value) for why, value, _, _ in _flag_values(text)]
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


def _labelled_values(text: str) -> Iterator[tuple[str, int, int]]:
    """Each labelled secret: the word that decides it, and where its whole value lies,
    to its closing quote or the end of its line (review 4, batch 1.3). After an
    authorization label a one-word scheme, such as `Token` or `Digest`, is skipped."""
    for match in _LABELLED.finditer(text):
        line = False
        if match.group("bearer") is not None:
            decided, start, quote = match.group("bearer"), match.start("bearer"), ""
        elif names_a_secret(match.group("label")):
            decided, start, quote = match.group("value"), match.start("value"), match.group("quote")
            # `key: value` runs to the line's end, YAML style; `KEY=value`, shell style,
            # to the first whitespace (review 6, batch 3).
            line = bool(match.group("gap"))
            if not quote:
                decided = text[start : _value_end(text, start, quote, line=line)]
            scheme = _SCHEME_VALUE.match(text, match.end("value"))
            if _words(match.group("label"))[-1:] in _AUTHORIZATION and scheme is not None:
                decided, start, line = scheme.group("value"), scheme.start("value"), False
        else:
            continue
        if not quote and _BLOCK_SCALAR.fullmatch(decided):
            # A YAML block scalar: the value is the indented lines below its label.
            end = _block_end(text, match.start())
            body = text[text.find("\n", start) + 1 : end].strip()
            if body and _secret_value(body):
                yield body, start, end
        elif _secret_value(decided):
            yield decided, start, _value_end(text, start, quote, line=line)


# A flag's value in text: after `=`, after whitespace on a command line, or as the next
# item of an argument array (review 6, batch 2.6).
_FLAG_VALUE = re.compile(
    r"(?<![\w-])(?P<flag>--?[A-Za-z][\w-]*)"
    r"(?:(?P<inline>[ \t]*=[ \t]*)|[ \t]+|[\"'][ \t]*,[ \t]*)"
    r"(?P<quote>[\"']?)(?P<value>[^\s\"',]+)"
)


def _flag_values(text: str) -> Iterator[tuple[str, str, int, int]]:
    """Each value a secret flag gives in text, as `_flagged` names it in a document,
    and where it lies: to its closing quote, or, unquoted, to the end of the word."""
    for match in _FLAG_VALUE.finditer(text):
        flag, start, quote = match.group("flag"), match.start("value"), match.group("quote")
        if not _secret_flag(flag):
            continue
        end = (
            _value_end(text, start, quote)
            if quote
            else min(match.end("value"), _value_end(text, start, quote))
        )
        value = text[start:end]
        if _literal(value) and _secret_value(value):
            why = "of" if match.group("inline") is not None else "after"
            yield f"the value {why} {flag}", value, start, end


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


# A string literal of a JSON or TOML document, multi-line ones included, decoded and
# compared against what the parsed document flagged (review 6, batch 1.2).
_STRING_LITERAL = re.compile(
    r'"""(?P<multi>[\s\S]*?)"""(?!")'
    r"|'''(?P<multi_literal>[\s\S]*?)'''(?!')"
    r'|"(?P<basic>(?:[^"\\\n]|\\.)*)"'
    r"|'(?P<literal>[^'\n]*)'"
)
_ESCAPES = {
    "b": "\b",
    "t": "\t",
    "n": "\n",
    "f": "\f",
    "r": "\r",
    '"': '"',
    "\\": "\\",
    "/": "/",
    "e": "\x1b",
}


def _decoded(raw: str, *, escapes: bool, multi: bool) -> tuple[str, list[int]]:
    """A literal's value, and where in `raw` each of its characters starts, with one entry
    past the end, so a value found decoded is redacted where it is written."""
    chars: list[str] = []
    starts: list[int] = []
    position = 2 if multi and raw.startswith("\r\n") else 1 if multi and raw.startswith("\n") else 0
    while position < len(raw):
        char = raw[position]
        if not escapes or char != "\\" or position + 1 == len(raw):
            chars.append(char)
            starts.append(position)
            position += 1
            continue
        following = raw[position + 1]
        if following in "uU":
            width = 4 if following == "u" else 8
            digits = raw[position + 2 : position + 2 + width]
            try:
                chars.append(chr(int(digits, 16)))
            except ValueError:
                chars.append(char)
                starts.append(position)
                position += 1
                continue
            starts.append(position)
            position += 2 + width
        elif following in _ESCAPES:
            chars.append(_ESCAPES[following])
            starts.append(position)
            position += 2
        elif multi and following in " \t\r\n":
            # A line-ending backslash: the whitespace after it is no part of the value.
            position += 1
            while position < len(raw) and raw[position] in " \t\r\n":
                position += 1
        else:
            chars.append(char)
            starts.append(position)
            position += 1
    return "".join(chars), [*starts, len(raw)]


@dataclass(frozen=True)
class _Secrets:
    """One side of a diff, scanned once: where each secret lies, and each value flagged."""

    spans: list[tuple[int, int]]
    values: list[str]


def _secrets_in(text: str, format_name: str) -> _Secrets:
    """Every secret `text` holds, by shape, by label and, in a JSON or TOML document, by
    where it sits: one scan and one parse a side (review 5, batch 3.2)."""
    shapes = [match for match in _SHAPES.finditer(text) if not _placeholder(match.group(0))]
    labelled = [(value, start, end) for value, start, end in _labelled_values(text)]
    labelled += [(value, start, end) for _, value, start, end in _flag_values(text)]
    spans = [(match.start(), match.end()) for match in shapes]
    spans += [(start, end) for _, start, end in labelled]
    values = [match.group(0) for match in shapes] + [value for value, _, _ in labelled]
    if format_name in {"json", "toml"}:
        secrets = [value for _, value in _placed(text, format_name)]
        spans += list(_structured_spans(text, set(secrets)))
        values += secrets
    return _Secrets(spans, values)


def _structured_spans(text: str, secrets: set[str]) -> Iterator[tuple[int, int]]:
    """Where each string literal holds a value the parsed document flagged: the whole
    literal when it is the value, else each whole occurrence in it, mapped back from the
    decoded value to the raw spelling, escapes and all."""
    if not secrets:
        return
    for match in _STRING_LITERAL.finditer(text):
        group = match.lastgroup or "basic"
        raw = match.group(group)
        decoded, starts = _decoded(
            raw, escapes=group in {"basic", "multi"}, multi=group.startswith("multi")
        )
        offset = match.start(group)
        if decoded in secrets:
            yield offset, offset + len(raw)
            continue
        for start, end in _carried_spans(decoded, secrets):
            yield offset + starts[start], offset + starts[end]


def _replaced(text: str, spans: Iterable[tuple[int, int]]) -> str:
    """`text` with each span replaced, overlapping ones as one."""
    pieces: list[str] = []
    position = 0
    for start, end in sorted(spans):
        # Touching or overlapping spans are one secret, under one placeholder.
        if pieces and start <= position:
            position = max(position, end)
            continue
        pieces += [text[position:start], "<redacted>"]
        position = end
    return "".join([*pieces, text[position:]])


_WORD = re.compile(r"[\w-]")
# Where a whole value can start: not inside a run of word characters.
_STARTS = re.compile(r"(?<![\w-])(?=[\s\S])")


def redact(text: str, values: Iterable[str]) -> str:
    """`text` with each whole occurrence of a secret value replaced."""
    return _replaced(text, _carried_spans(text, values))


def _carried_spans(text: str, values: Iterable[str]) -> list[tuple[int, int]]:
    """Each whole occurrence of a value in `text`, never one inside another word, the
    longest that fits at each place. One pass, each start looked up by its first
    characters: a pass per value took 7.4 s for 16k of them (review 4, batch 3.1)."""
    unique = sorted({value for value in values if value}, key=len, reverse=True)
    if not unique:
        return []
    width = len(unique[-1])
    by_prefix: dict[str, list[str]] = {}
    for value in unique:
        by_prefix.setdefault(value[:width], []).append(value)
    spans: list[tuple[int, int]] = []
    # Each start is tried, inside an earlier match too: callers merge overlapping spans.
    for start in _STARTS.finditer(text):
        at = start.start()
        for value in by_prefix.get(text[at : at + width], ()):
            end = at + len(value)
            if text.startswith(value, at) and not _WORD.match(text, end):
                spans.append((at, end))
                break
    return spans


def redacted_lines(
    actual: str, expected: str, format_name: str = "copy"
) -> tuple[list[str], list[str]]:
    """Both sides of a diff, line for line as written, each secret replaced where it lies,
    and every value either side flags replaced on both wherever it occurs as a whole word,
    whatever its length (review 6, batch 1.2). A secret spanning lines leaves a placeholder
    on each, so the lines still pair with the raw ones a diff is computed from. The whole of
    each text is read, however long and whatever bytes it holds."""
    sides = [(text, _secrets_in(text, format_name)) for text in (actual, expected)]
    carried = [
        spelling
        for text, found in sides
        for value in (*(text[start:end] for start, end in found.spans), *found.values)
        for spelling in (value, json.dumps(value)[1:-1])
    ]
    shown = [
        _by_line(text, [*found.spans, *_carried_spans(text, carried)]) for text, found in sides
    ]
    return shown[0], shown[1]


def redacted_pair(actual: str, expected: str, format_name: str = "copy") -> tuple[str, str]:
    """`redacted_lines`, each side joined back into one text."""
    before, after = redacted_lines(actual, expected, format_name)
    return "".join(before), "".join(after)


def _by_line(text: str, spans: list[tuple[int, int]]) -> list[str]:
    """`text`'s lines, each with the parts of `spans` on it replaced: a line ending is
    never replaced, so a line stays a line. Each span is placed by bisection, so the cost
    is the lines it touches, not every line."""
    lines = text.splitlines(keepends=True)
    starts = list(itertools.accumulate((len(line) for line in lines), initial=0))
    bodies = [line.rstrip("\r\n") for line in lines]
    per_line: dict[int, list[tuple[int, int]]] = {}
    for start, stop in spans:
        first = bisect.bisect_right(starts, start) - 1
        last = bisect.bisect_left(starts, stop) - 1
        for index in range(max(first, 0), min(last, len(lines) - 1) + 1):
            low = max(start, starts[index]) - starts[index]
            high = min(stop, starts[index] + len(bodies[index])) - starts[index]
            if high > low:
                per_line.setdefault(index, []).append((low, high))
    return [
        _replaced(body, per_line[index]) + line[len(body) :] if index in per_line else line
        for index, (line, body) in enumerate(zip(lines, bodies, strict=True))
    ]


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
