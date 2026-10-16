"""A file holding a secret is never diffed: loadout names it and counts the lines that
differ, so no value is printed and no change is hidden (ADR 0032)."""

from __future__ import annotations

import subprocess
import sys

import pytest

from loadout.diffs import diff_lines

TOKEN = "ghp_" + "a1B2" * 9
KEY = "FAKE" + "abcdef1234567890"
SHAPE = "a token in a provider's format"
LABELLED = "a value labelled as a secret"


def _withheld(rel: str, count: int, reason: str) -> list[str]:
    line = (
        f"{rel}: {count} line(s) differ; diff not shown because the file holds a secret "
        f"({reason}) — read the file directly\n"
    )
    return [line]


@pytest.mark.parametrize("side", ["on disk", "expected"])
def test_a_secret_on_either_side_withholds_the_diff(side: str) -> None:
    secret, plain = f"Rules.\npush with {TOKEN}\n", "Rules.\n"
    actual, expected = (secret, plain) if side == "on disk" else (plain, secret)

    assert diff_lines("out.md", actual, expected, context=1) == _withheld("out.md", 1, SHAPE)


def test_a_change_beside_a_secret_is_counted() -> None:
    lines = diff_lines(
        "notes.sh",
        f"export API_TOKEN={KEY}\nmodel = a\nkeep\n",
        f"export API_TOKEN={KEY}\nmodel = b\nkeep\nadded\n",
        context=1,
    )

    assert lines == _withheld("notes.sh", 2, LABELLED)


def test_a_change_only_inside_a_secret_is_counted() -> None:
    """Review 6, batch 1.2: redacted, it showed as unchanged context, and `--force` then
    wrote the placeholder over the real token."""
    lines = diff_lines(
        "claude.json",
        '{"Authorization": "Bearer AAAAAAAAAAAAAAAA1"}\n',
        '{"Authorization": "Bearer BBBBBBBBBBBBBBBB2"}\n',
        context=1,
    )

    assert lines == _withheld("claude.json", 1, LABELLED)


def test_a_file_holding_a_secret_that_matches_has_no_diff() -> None:
    text = f"push with {TOKEN}\n"

    assert diff_lines("out.md", text, text, context=1) == []


def test_a_file_without_a_secret_is_diffed_in_full() -> None:
    assert diff_lines("out.md", "Rules.\nold\n", "Rules.\nnew\n", context=1) == [
        "--- out.md (on disk)\n",
        "+++ out.md (expected)\n",
        "@@ -1,2 +1,2 @@\n",
        " Rules.\n",
        "-old\n",
        "+new\n",
    ]


# Each of these printed part or all of its secret while diffs were redacted.
WITHHELD = {
    "json command": (
        "claude.json",
        f'{{"mcpServers": {{"s": {{"command": "srv --api-key={KEY}"}}}}}}\n',
        "the value of --api-key",
    ),
    "toml command": ("config.toml", f'command = "y --api-key={KEY}"\n', "the value of --api-key"),
    "toml multi-line": (
        "config.toml",
        f'[mcp_servers.s.env]\nTOKEN = """\n{KEY}\nsecond-line-of-it\n"""\n',
        "env.TOKEN",
    ),
    "json escaped": (
        "claude.json",
        '{"mcpServers": {"s": {"command": "srv --api-key=FAKE\\\\abcdef1234567890"}}}\n',
        "the value of --api-key",
    ),
    "env map escaped": (
        "claude.json",
        '{"env": {"DATABASE": "abc\\u0064efgh1234"}}\n',
        "env.DATABASE",
    ),
    "env map short": ("claude.json", '{"env": {"DATABASE": "hunt2"}}\n', "env.DATABASE"),
    "env map unparsable": (
        "claude.json",
        f'{{"env": {{"MY_SERVICE": "{KEY}", "LOG_LEVEL": "debug",}}, "x": 1}}\n',
        "env.MY_SERVICE",
    ),
    "pem in json": (
        "claude.json",
        '{"key": "-----BEGIN PRIVATE KEY-----\\nMIIBODY\\n-----END PRIVATE KEY-----\\n"}\n',
        "a private key",
    ),
    "pem in markdown": (
        "notes.md",
        "-----BEGIN RSA PRIVATE KEY-----\nMIIBODY\n-----END RSA PRIVATE KEY-----\nafter\n",
        "a private key",
    ),
    "yaml block": ("notes.yaml", f"password: |2-\n  {KEY}\n  second line\nnext: x\n", LABELLED),
    "nested under env": ("notes.yaml", f"env:\n  GITHUB_TOKEN: {KEY}\n", LABELLED),
    "hyphenated token": ("notes.md", f"x-{TOKEN}-y\n", SHAPE),
    "codex rules array": (
        "rules.star",
        f'prefix_rule(pattern=["mycli", "--api-key", "{KEY}"], decision="allow")\n',
        "the value after --api-key",
    ),
    "a nul byte": ("out.md", f"a\0b\npush with {TOKEN}\n", SHAPE),
}


@pytest.mark.parametrize("case", sorted(WITHHELD))
def test_a_secret_in_any_spelling_withholds_the_diff(case: str) -> None:
    rel, text, reason = WITHHELD[case]

    assert diff_lines(rel, text, "", context=1) == _withheld(rel, text.count("\n"), reason)


# Values detection leaves alone, so a file holding only these is diffed as any other.
NO_SECRET = [
    "aws_key_id AKIAXXXXXXXXXXXXXXXX\n",
    "password: |\n  ${DB_PASSWORD}\nnext: 1\n",
    "password: |\n  xxxxxxxx\nnext: 1\n",
    "password: # set in CI\n",
    "Authorization: Token\t${API_TOKEN}\n",
    f"mycli --verbose {KEY}x --api-key-file key.pem --token $GH_TOKEN --api-key --dry\n",
]


@pytest.mark.parametrize("text", NO_SECRET)
def test_a_file_whose_values_are_no_secret_is_diffed(text: str) -> None:
    assert diff_lines("notes.yaml", text, "", context=1)[3:] == [
        f"-{line}" for line in text.splitlines(keepends=True)
    ]


PAST_THE_CAP = f"""
from loadout.diffs import diff_lines
text = "x" * (1024 * 1024 + 100_000) + "\\npush with {TOKEN}\\n"
print(diff_lines("out.md", text, "", context=1), end="")
"""


@pytest.mark.integration
def test_a_secret_past_the_scan_s_cap_withholds_the_diff() -> None:
    """Review 4, batch 1.3: init's scan stops at 1 MiB; the check before a diff reads
    the whole text. In a child, as a scan gone quadratic hangs rather than fails."""
    try:
        result = subprocess.run(
            [sys.executable, "-c", PAST_THE_CAP],
            capture_output=True,
            check=True,
            text=True,
            timeout=10,
        )
    except subprocess.TimeoutExpired:
        pytest.fail("took longer than 10 s")

    assert result.stdout == repr(_withheld("out.md", 2, SHAPE))
