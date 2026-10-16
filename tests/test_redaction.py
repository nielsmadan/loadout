"""Redaction as every printed diff applies it (review 4, batches 1.2 and 1.3): the whole
text, each value to its end, whole values only."""

from __future__ import annotations

import subprocess
import sys

import pytest

from loadout.credentials import credential_material, names_a_secret, redact, redacted_pair
from loadout.diffs import diff_lines

TOKEN = "ghp_" + "a1B2" * 9


def _shown(text: str, format_name: str = "copy") -> str:
    return redacted_pair(text, "", format_name)[0]


def _in_a_child(script: str, seconds: float) -> str:
    """What `script` prints, from a child given `seconds`: a scan gone quadratic over a
    large text hangs rather than fails (review 4, batch 4.3)."""
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            check=True,
            text=True,
            timeout=seconds,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"took longer than {seconds} s")
    return result.stdout


PAST_THE_CAP = f"""
from loadout.credentials import redacted_pair
text = "x" * (1024 * 1024 + 100_000) + "\\npush with {TOKEN}\\n"
print(redacted_pair(text, "")[0][-30:], end="")
"""


@pytest.mark.integration
def test_a_token_past_the_scan_s_size_cap_is_redacted() -> None:
    assert _in_a_child(PAST_THE_CAP, 10).endswith("x\npush with <redacted>\n")


def test_a_nul_byte_does_not_turn_redaction_off() -> None:
    assert _shown(f"a\0b\npush with {TOKEN}\n") == "a\0b\npush with <redacted>\n"


@pytest.mark.parametrize(
    ("line", "shown"),
    [
        ("password: horse battery staple", "password: <redacted>"),
        ("db_password = 'pass w0rd FAKE'", "db_password = '<redacted>'"),
        ('"password": "two words",', '"password": "<redacted>",'),
        ("Authorization: Token abc123def456", "Authorization: Token <redacted>"),
        ("Authorization: Digest abc123def456", "Authorization: Digest <redacted>"),
        ("Authorization: ApiKey abc123def456", "Authorization: ApiKey <redacted>"),
        ("Authorization: Bearer abc123def456", "Authorization: Bearer <redacted>"),
        ('SECRET_KEY = "django-insecure-0123456789abcdefghij"', 'SECRET_KEY = "<redacted>"'),
        ("STRIPE_SECRET_KEY=sk_live_" + "4eC39HqLyjWDarjtT1zdp7dc", "STRIPE_SECRET_KEY=<redacted>"),
        ("OPENAI_KEY=abc123def456", "OPENAI_KEY=<redacted>"),
        ("client_secret: abc123def456", "client_secret: <redacted>"),
        ("signingKey: abc123def456", "signingKey: <redacted>"),
    ],
)
def test_a_labelled_value_is_redacted_to_its_end(line: str, shown: str) -> None:
    assert _shown(f"{line}\nnext\n") == f"{shown}\nnext\n"


@pytest.mark.parametrize(
    ("text", "shown"),
    [
        ("use sk_" + "live_4eC39HqLyjWDarjtT1zdp7dc here", "use <redacted> here"),
        ("use rk_" + "test_4eC39HqLyjWDarjtT1zdp7dc here", "use <redacted> here"),
        ("hf_token hf_" + "AbCdEfGhIjKlMnOpQrStUvWxYz0123", "hf_token <redacted>"),
        (
            "post to https://hooks.slack.com/services/T0000/B0000/XXXXabcdEFGH1234",
            "post to <redacted>",
        ),
        ("key django-insecure-0123456789abcdefghij", "key <redacted>"),
    ],
)
def test_a_provider_s_token_shape_is_redacted(text: str, shown: str) -> None:
    assert _shown(text) == shown
    assert credential_material(text.encode()) is not None


def test_a_value_is_never_replaced_inside_another_word() -> None:
    """A found value was replaced everywhere: `password` became `<redacted>word`."""
    text = "secret: pass\nreset the password\n"

    assert _shown(text) == "secret: <redacted>\nreset the password\n"


def test_a_long_value_shown_unlabelled_on_the_other_side_is_redacted_there_too() -> None:
    actual, expected = redacted_pair("password: abc123def456\n", "abc123def456\nxabc123def456\n")

    assert (actual, expected) == ("password: <redacted>\n", "<redacted>\nxabc123def456\n")


def test_an_escaped_json_value_is_matched_as_the_document_reads_it() -> None:
    """No label names it a secret: only the parsed document's `env` map does."""
    text = '{"env": {"DATABASE": "abc\\u0064efgh1234"}}\n'

    assert _shown(text, "json") == '{"env": {"DATABASE": "<redacted>"}}\n'


def test_an_escaped_quote_does_not_end_a_value() -> None:
    assert _shown('"password": "a\\"b c",\n') == '"password": "<redacted>",\n'


@pytest.mark.parametrize(
    "name", ["SECRET_KEY", "STRIPE_SECRET_KEY", "client_secret", "signingKey", "OPENAI_KEY"]
)
def test_the_vocabulary_names_these_secrets(name: str) -> None:
    assert names_a_secret(name)


def test_every_printed_diff_is_redacted() -> None:
    lines = diff_lines("out.md", "Rules.\n", f"Rules.\npush with {TOKEN}\n", context=1)

    assert "+push with <redacted>\n" in lines
    assert TOKEN not in "".join(lines)


REDACT_MANY = """
import random, string
from loadout.credentials import redact
random.seed(1)
values = ["".join(random.choices(string.ascii_letters, k=random.randint(8, 30))) for _ in range(30_000)]
text = "\\n".join(f"password: {value}" for value in values)
print(redact(text, values).count("<redacted>"))
"""


@pytest.mark.integration
def test_many_distinct_values_are_redacted_in_one_pass() -> None:
    """Review 4, batch 3.1: a pass per value took 7.4 s for 16k of them."""
    assert _in_a_child(REDACT_MANY, 10) == "30000\n"


def test_the_longest_value_that_fits_is_the_one_replaced() -> None:
    assert redact("a abcdefgh abcdefghij\n", ["abcdefgh", "abcdefghij"]) == (
        "a <redacted> <redacted>\n"
    )
    assert redact("abcdefghij-x abcdefgh_\n", ["abcdefgh", "abcdefghij"]) == (
        "abcdefghij-x abcdefgh_\n"
    )


def test_a_value_inside_one_already_replaced_is_not_replaced_again() -> None:
    assert redact("x horse battery staple\n", ["horse battery staple", "battery staple"]) == (
        "x <redacted>\n"
    )


KEY = "FAKE" + "abcdef1234567890"


def _diff(rel: str, actual: str, expected: str = "") -> str:
    return "".join(diff_lines(rel, actual, expected, context=1))


@pytest.mark.parametrize(
    ("rel", "text", "shown"),
    [
        (
            "claude.json",
            f'{{"mcpServers": {{"s": {{"command": "srv --api-key={KEY}"}}}}}}\n',
            "--api-key=<redacted>",
        ),
        ("config.toml", f'command = "y --api-key={KEY}"\n', "--api-key=<redacted>"),
        (
            "config.toml",
            f'[mcp_servers.s.env]\nTOKEN = """\n{KEY}\nsecond-line-of-it\n"""\n',
            '-<redacted>"""\n',
        ),
        (
            "notes.yaml",
            f"password: |\n  {KEY}\n  second line\nnext: x\n",
            "-password: <redacted>\n-next: x\n",
        ),
        (
            "claude.json",
            '{"mcpServers": {"s": {"command": "srv --api-key=FAKE\\\\abcdef1234567890"}}}\n',
            "--api-key=<redacted>",
        ),
    ],
    ids=["json command", "toml command", "toml multi-line", "yaml block", "json escaped"],
)
def test_a_flagged_value_is_redacted_wherever_it_sits(rel: str, text: str, shown: str) -> None:
    """Review 5, batch 1.2: inside a longer string, a command line or a multi-line string,
    the value printed in clear."""
    printed = _diff(rel, text)

    assert shown in printed
    assert "abcdef1234567890" not in printed
    assert "second" not in printed


def test_sides_that_differ_only_inside_a_redacted_value_say_so() -> None:
    """Review 5, batch 1.3: both sides redacted alike, so the diff came out empty and the
    abort showed nothing to port."""
    lines = diff_lines(
        "claude.json",
        '{"Authorization": "Bearer AAAAAAAAAAAAAAAA1"}\n',
        '{"Authorization": "Bearer BBBBBBBBBBBBBBBB2"}\n',
        context=1,
    )

    assert lines == ["claude.json: differs only inside a redacted value\n"]


def test_a_short_labelled_value_is_redacted_where_it_sits_and_nowhere_else() -> None:
    """Ruling on review 5, batch 1: a value under six characters is redacted at the place
    it was flagged, and an unrelated `word` elsewhere stays visible."""
    assert _shown("secret: word\nthe word stays\n") == "secret: <redacted>\nthe word stays\n"


def test_a_short_flagged_value_in_a_command_is_redacted_where_it_sits() -> None:
    text = '{"command": "srv --api-key=ab12c", "note": "ab12c is a build"}\n'

    assert _shown(text, "json") == (
        '{"command": "srv --api-key=<redacted>", "note": "ab12c is a build"}\n'
    )


def test_a_value_of_six_characters_is_carried_to_the_other_side() -> None:
    assert redacted_pair("password: abc123\n", "abc123 is used here\n") == (
        "password: <redacted>\n",
        "<redacted> is used here\n",
    )


def test_a_token_inside_a_hyphenated_word_is_redacted_by_its_shape() -> None:
    """Review 5, batch 5.4: no whole-word carry reaches it, so only its shape does."""
    assert _shown(f"x-{TOKEN}-y\n") == "x-<redacted>-y\n"


def test_a_short_env_value_is_redacted_by_where_it_sits() -> None:
    """Too short to carry, and labelled by nothing secret: only the parsed `env` map
    flags it."""
    assert _shown('{"env": {"DATABASE": "hunt2"}}\n', "json") == (
        '{"env": {"DATABASE": "<redacted>"}}\n'
    )


def test_an_unquoted_value_s_trailing_spaces_stay_outside_the_redaction() -> None:
    assert _shown("password: horse battery   \nnext\n") == "password: <redacted>   \nnext\n"
