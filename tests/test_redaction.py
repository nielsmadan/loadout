"""Redaction as every printed diff applies it (review 4, batches 1.2 and 1.3): the whole
text, each value to its end, whole values only."""

from __future__ import annotations

import subprocess
import sys

import pytest

from loadout.credentials import (
    credential_material,
    findings,
    names_a_secret,
    redact,
    redacted_pair,
)
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
        # After `: ` a value runs to the line's end or a comment, YAML style; after a
        # bare `=` to the first whitespace, shell style (review 6, batch 3).
        ("password: horse battery staple", "password: <redacted>"),
        ("password: FAKEabc123 # used by staging", "password: <redacted> # used by staging"),
        ("password: FAKE abc123 // staging", "password: <redacted> // staging"),
        ("password: FAKEabc123#staging", "password: <redacted>"),
        ("api_token: FAKEabc123//staging", "api_token: <redacted>"),
        ("db_password = pass w0rd FAKE", "db_password = <redacted>"),
        (
            "export API_TOKEN=FAKEabc123 then run make dev",
            "export API_TOKEN=<redacted> then run make dev",
        ),
        ("password:FAKEabc123 then more", "password:<redacted> then more"),
        ("password: # set in CI", "password: # set in CI"),
        ("Authorization: Token abc123def456 extra", "Authorization: Token <redacted> extra"),
        ("api_token: https://user:pw@host.test/x", "api_token: <redacted>"),
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
            "-<redacted>\n-<redacted>\n",
        ),
        (
            "notes.yaml",
            f"password: |\n  {KEY}\n  second line\nnext: x\n",
            "-password: <redacted>\n-<redacted>\n-<redacted>\n-next: x\n",
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


@pytest.mark.parametrize("header", ["|-", "|+", ">2", "|-2", "|2-", ">+1", ">1+"])
def test_a_block_scalar_is_redacted_whichever_order_its_indicators_take(header: str) -> None:
    """Review 6, batch 1.2: `|2-`, indentation before chomping, printed its body."""
    printed = _diff("notes.yaml", f"password: {header}\n  {KEY}\n  second line\nnext: x\n")

    assert "-password: <redacted>\n-<redacted>\n-<redacted>\n-next: x\n" in printed


def test_a_line_that_differs_only_inside_a_redacted_value_is_shown_changed() -> None:
    """Review 6, batch 1.2: it showed as unchanged context beside any other change, and
    `--force` then wrote the placeholder over the real token."""
    lines = diff_lines(
        "claude.json",
        '{"Authorization": "Bearer AAAAAAAAAAAAAAAA1"}\nmodel = a\n',
        '{"Authorization": "Bearer BBBBBBBBBBBBBBBB2"}\nmodel = b\n',
        context=1,
    )

    assert lines[2:] == [
        "@@ -1,2 +1,2 @@\n",
        '-{"Authorization": "Bearer <redacted>"}\n',
        "-model = a\n",
        '+{"Authorization": "Bearer <redacted>"}  (differs inside a redacted value)\n',
        "+model = b\n",
    ]


def test_a_change_after_an_unquoted_secret_on_its_line_is_shown() -> None:
    lines = diff_lines(
        "notes.sh",
        f"export API_TOKEN={KEY} then run make dev\n",
        f"export API_TOKEN={KEY} then run make test\n",
        context=1,
    )

    assert lines[3:] == [
        "-export API_TOKEN=<redacted> then run make dev\n",
        "+export API_TOKEN=<redacted> then run make test\n",
    ]


def test_a_short_flagged_value_is_redacted_as_a_whole_word_everywhere() -> None:
    """Review 6 reverses round 5's carry floor: a short value redacted on one side only
    printed in clear beside its own redaction. Never inside another word, though."""
    assert _shown("secret: word\nthe word stays\nwordy\n") == (
        "secret: <redacted>\nthe <redacted> stays\nwordy\n"
    )


def test_a_short_flagged_value_in_a_command_is_redacted_wherever_it_sits() -> None:
    text = '{"command": "srv --api-key=ab12c", "note": "ab12c is a build"}\n'

    assert _shown(text, "json") == (
        '{"command": "srv --api-key=<redacted>", "note": "<redacted> is a build"}\n'
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
    assert _shown("password: horse   \nnext\n") == "password: <redacted>   \nnext\n"


NESTED = [
    f"env:\n  GITHUB_TOKEN: {KEY}\n",
    f"mcpServers:\n  gh:\n    env:\n      API_KEY: {KEY}\n",
    f"env:\n  password: |\n    {KEY}\n",
    f"notes:\npassword: {KEY}\n",
]


@pytest.mark.parametrize("text", NESTED, ids=["env", "mcpServers", "block", "after a parent"])
def test_a_secret_nested_under_a_parent_key_is_found_and_redacted(text: str) -> None:
    """Review 6, batch 1.1: a label's separator spanned the newline, so `env:` took the
    next line as its value and the real label was never read."""
    assert findings(text.encode()) != []
    printed = "".join(diff_lines("notes.yaml", text, "", context=1))
    assert "<redacted>" in printed
    assert "abcdef1234567890" not in printed


def test_a_flagged_value_inside_a_multi_line_toml_string_is_redacted() -> None:
    """Review 6, batch 1.2: multi-line strings yielded no spans."""
    text = "command = \"\"\"\nsrv --api-key=ab12c\n\"\"\"\nkeep = '''\n--token ab34d\n'''\n"

    assert _shown(text, "toml") == (
        "command = \"\"\"\nsrv --api-key=<redacted>\n\"\"\"\nkeep = '''\n--token <redacted>\n'''\n"
    )


def test_a_flagged_value_written_with_escapes_is_redacted_where_it_is_written() -> None:
    """Review 6, batch 1.2: the value was found decoded and left raw."""
    text = '{"command": "srv --api-key=ab\\u0031\\u0032cdef"}\n'

    assert _shown(text, "json") == '{"command": "srv --api-key=<redacted>"}\n'


@pytest.mark.parametrize(
    ("text", "shown", "why"),
    [
        (
            f'prefix_rule(pattern=["mycli", "--api-key", "{KEY}"], decision="allow")\n',
            'prefix_rule(pattern=["mycli", "--api-key", "<redacted>"], decision="allow")\n',
            "the value after --api-key",
        ),
        (
            f"curl --token {KEY} https://x\n",
            "curl --token <redacted> https://x\n",
            "the value after --token",
        ),
        (
            f'curl --token "{KEY}" -s\n',
            'curl --token "<redacted>" -s\n',
            "the value after --token",
        ),
        (
            f"curl --api-key={KEY} -s\n",
            "curl --api-key=<redacted> -s\n",
            "the value of --api-key",
        ),
    ],
    ids=["codex rules array", "flag then value", "flag then quoted value", "flag=value"],
)
def test_a_secret_flag_s_value_in_text_is_labelled(text: str, shown: str, why: str) -> None:
    """Review 6, batch 2.6: outside a parsed document, only `label: value` was."""
    assert (_shown(text), findings(text.encode())) == (shown, [(why, KEY)])


def test_a_flag_that_names_no_secret_labels_nothing() -> None:
    text = f"mycli --verbose {KEY}x --api-key-file key.pem --token $GH_TOKEN --api-key --dry\n"

    assert _shown(text) == text


def test_a_value_map_entry_of_an_unparsable_json_document_is_redacted() -> None:
    """Review 6, batch 2.6: the entry is flagged only by where it sits, and a document
    that does not parse, as a hand edit leaving a trailing comma, lost it."""
    text = f'{{"env": {{"MY_SERVICE": "{KEY}", "LOG_LEVEL": "debug",}}, "x": 1}}\n'

    assert (_shown(text, "json"), findings(text.encode(), "json")) == (
        '{"env": {"MY_SERVICE": "<redacted>", "LOG_LEVEL": "debug",}, "x": 1}\n',
        [("env.MY_SERVICE", KEY)],
    )


def test_a_value_that_starts_inside_a_longer_one_is_hidden_past_its_end() -> None:
    """Review 6, batch 3 (rx-redact-break): a start inside an earlier match was skipped,
    so the part of a second value running past that match printed."""
    assert redact("x foobar.baz yy z\n", ["foobar.baz", "baz yy"]) == "x <redacted> z\n"


def test_a_quoted_label_s_whole_value_is_carried_to_the_other_side() -> None:
    """Review 6, batch 3 (cr-carry-spans): the decided word is the first one alone."""
    assert redacted_pair('password: "two words"\n', "two words here\n") == (
        'password: "<redacted>"\n',
        "<redacted> here\n",
    )


def test_a_block_scalar_s_body_is_carried_to_the_other_side() -> None:
    """Review 6, batch 3 (cs-values): the span holds the header too, so only the value
    finds the body written bare."""
    assert redacted_pair(f"password: |\n  {KEY}\n", f"{KEY}\n")[1] == "<redacted>\n"


def test_a_documentation_placeholder_stays_in_view() -> None:
    """Review 6, batch 3 (cs-placeholder)."""
    text = "aws_key_id AKIAXXXXXXXXXXXXXXXX\n"

    assert _shown(text) == text


def test_a_flagged_value_inside_a_literal_is_redacted_only_as_a_whole_word() -> None:
    """Review 6, batch 3 (lit-bounds)."""
    text = '{"env": {"A_VAR": "hunt2"}, "note": "hunt2x or xhunt2 or hunt2"}\n'

    assert _shown(text, "json") == (
        '{"env": {"A_VAR": "<redacted>"}, "note": "hunt2x or xhunt2 or <redacted>"}\n'
    )


def test_a_quoted_bar_is_a_value_not_a_block_scalar() -> None:
    """Review 6, batch 3 (yb-quote)."""
    assert _shown('password: "|"\n  indented line\n') == (
        'password: "<redacted>"\n  indented line\n'
    )


def test_a_block_scalar_ends_before_its_trailing_blank_lines() -> None:
    """Review 6, batch 3 (yb-blank-end)."""
    assert _shown(f"password: |\n  {KEY}\n   \nnext: 1\n") == (
        "password: <redacted>\n<redacted>\n   \nnext: 1\n"
    )


@pytest.mark.parametrize("body", ["${DB_PASSWORD}", "xxxxxxxx"])
def test_a_block_scalar_holding_a_reference_or_placeholder_stays_in_view(body: str) -> None:
    """Review 6, batch 3 (yb-secret-value)."""
    text = f"password: |\n  {body}\nnext: 1\n"

    assert _shown(text) == text


def test_a_provider_names_a_key_only_directly_before_it() -> None:
    """Review 6, batch 3 (rx-provider-pos)."""
    assert [names_a_secret(name) for name in ("my_openai_key", "openai_model_key")] == [
        True,
        False,
    ]


def test_a_scheme_after_a_tab_stays_in_view() -> None:
    """Review 6, batch 3 (rx-ws-scheme)."""
    assert _shown("Authorization: Token\tabc123def456\n") == ("Authorization: Token\t<redacted>\n")
