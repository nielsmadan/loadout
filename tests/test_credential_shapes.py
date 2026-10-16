"""Secret shapes the credential check recognises, so init names each source file holding
one (review 1.8, ADR 0030), and the ordinary values it must leave alone (re-review of 798a4f2)."""

from __future__ import annotations

import json
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import loadout
import loadout.init_workflow as workflow
from loadout.credentials import (
    BINARY_PROBE,
    MAX_SCANNED,
    credential_material,
    findings,
    names_a_secret,
)
from loadout.diffs import diff_lines
from loadout.sync import RenderedChange

SECRETS = {
    "flag before a key in args": ("json", {"args": ["--api-key", "sk-live-0123456789abcdef"]}),
    "flag before a token in args": ("json", {"args": ["--token", "ghp_abcdefghijklmnopqrst1234"]}),
    "access key given inline": ("json", {"args": ["--access-key=FAKEVALUE"]}),
    "private key given inline": ("json", {"args": ["--private-key=FAKEVALUE"]}),
    "flag in a command string": ("json", {"command": "curl --api-key FAKEVALUE https://x.test"}),
    "URL userinfo": ("json", {"url": "https://admin:hunter2@mcp.example.com/sse"}),
    "URL token as the user": ("json", {"url": "https://abcdefghijklmnopqrstuvwx@host.test/"}),
    "URL query key": ("json", {"url": "https://api.test/v1?key=abcdef123456"}),
    "bare GitHub token": ("copy", "Use ghp_abcdefghijklmnopqrstuvwx1234 to push.\n"),
    "fine-grained GitHub token": ("copy", "github_pat_11ABCDEFG0123456789abcdefghij\n"),
    "GitLab token": ("copy", "glpat-abcdefghijklmnopqrst\n"),
    "npm token": ("copy", "npm_abcdefghijklmnopqrstuvwxyz0123456789\n"),
    "Google API key": ("copy", "AIzaSyA1234567890abcdefghijklmnopqrstu\n"),
    "AWS session key id": ("copy", "ASIAIOSFODNN7ABCDEFG\n"),
    "AWS access key id": ("copy", "key AKIAIOSFODNN7ABCDEFG\n"),
    "Slack app token": ("copy", "xapp-1-A0123456789-abcdefghij\n"),
    "Slack refresh token": ("copy", "xoxe-" + "1-abcdefghijklmnop\n"),
    "JWT": ("copy", "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0In0.abcdefghijklmnop\n"),
    "OpenSSH private key": (
        "copy",
        "-----BEGIN OPENSSH PRIVATE KEY-----\nb3BlbnNzaC1rZXk\n-----END OPENSSH PRIVATE KEY-----\n",
    ),
    "Basic authorization": ("copy", "curl -H 'Authorization: Basic YWRtaW46aHVudGVyMg=='\n"),
    "curl user option": ("json", {"hooks": {"Stop": "curl -u admin:hunter2 https://x.test"}}),
    "AWS secret setting": ("copy", "aws_secret_access_key = wJalrXUtnFEMI/K7MDENG/bPxRfiCY\n"),
    "Anthropic key": ("copy", "export KEY=sk-ant-api03-abcdefghijklmnop\n"),
    "Slack bot token": ("copy", "xoxb-123456789012-abcdefghij\n"),
    "OpenCode environment value": ("json", {"mcp": {"x": {"environment": {"API": "s3cr3t"}}}}),
    # One rule decides each case below (review 2, batch 5 item 5).
    "Slack user token": ("copy", "xoxp-123456789012-abcdefghij\n"),
    "Slack legacy app token": ("copy", "xoxa-2-abcdefghijklmnop\n"),
    "a pat flag": ("json", {"args": ["--pat", "FAKEVALUE1"]}),
    "an auth flag": ("json", {"args": ["--auth", "FAKEVALUE1"]}),
    "a pass flag": ("json", {"args": ["--pass", "FAKEVALUE1"]}),
    "a passwd key": ("json", {"passwd": "FAKEVALUE1"}),
    "a passphrase key": ("json", {"passphrase": "FAKEVALUE1"}),
    "a credential key": ("json", {"credential": "FAKEVALUE1"}),
    "a credentials key": ("json", {"credentials": "FAKEVALUE1"}),
    "an authorization key": ("json", {"authorization": "FAKEVALUE1"}),
    "an access key key": ("json", {"accessKey": "FAKEVALUE1"}),
    "a private key key": ("json", {"private_key": "FAKEVALUE1"}),
    "a header value": ("json", {"headers": {"X-Custom": "FAKEVALUE1"}}),
    "a Codex header value": ("toml", '[mcp_servers.x.http_headers]\nX-Custom = "FAKEVALUE1"\n'),
    "a bearer value in prose": ("copy", "Send it as Bearer abcdef1234567890 on each call.\n"),
}

NOT_SECRETS = {
    "a token budget": ("json", {"max_tokens": 1000}),
    "a port flag": ("json", {"args": ["--port", "8080"]}),
    "a key flag naming an environment variable": ("json", {"args": ["--api-key", "$API_KEY"]}),
    "a word ending in sk": ("copy", "Run the task-runner-for-every-module command.\n"),
    "basic as a word": ("copy", "This skill covers basic deployment steps for the team.\n"),
    "a keymap flag": ("json", {"args": ["--keymap", "vim"]}),
    "a hotkey flag": ("json", {"args": ["--hotkey", "ctrl-k"]}),
    "a sort key flag": ("json", {"args": ["--sort-key", "name"]}),
    "a primary key flag": ("json", {"args": ["--primary-key", "id"]}),
    "a max tokens flag": ("json", {"args": ["--max-tokens", "4096"]}),
    "a token limit flag": ("json", {"args": ["--token-limit", "4096"]}),
    "a tokenizer flag": ("json", {"args": ["--tokenizer", "cl100k"]}),
    "a key file flag": ("json", {"args": ["--api-key-file", "/run/key"]}),
    "a password file flag": ("json", {"args": ["--password-file", "/run/pass"]}),
    "a secrets dir flag": ("json", {"args": ["--secrets-dir", "/run/secrets"]}),
    "a TLS key flag": ("json", {"args": ["--tls-key", "server.key"]}),
    "an SSH key path flag": ("json", {"args": ["--ssh-key-path", "~/.ssh/id"]}),
    "a secret name flag": ("json", {"args": ["--secret-name", "prod"]}),
    "a keychain flag": ("json", {"args": ["--use-keychain", "yes"]}),
    "a flag followed by a flag": ("json", {"args": ["--no-token", "--verbose"]}),
    "a docker uid:gid": ("json", {"command": "docker run -u 1000:1000 image"}),
    "a sudo user:group": ("copy", "sudo -u www-data:www-data make\n"),
    "a documented uid:gid": ("copy", "Run the container with --user 1000:1000.\n"),
    "an AWS example key": ("copy", "key AKIAIOSFODNN7EXAMPLE\n"),
    "an all-x placeholder": ("copy", "export KEY=sk-xxxxxxxxxxxxxxxxxxxx\n"),
    "a numeric switch in env": ("json", {"env": {"DISABLE_TELEMETRY": "1"}}),
    "a path in env": ("json", {"env": {"PATH_EXTRA": "/usr/local/bin"}}),
    "a content type header": ("json", {"headers": {"Content-Type": "application/json"}}),
    # One rule decides each case below (review 2, batch 5 item 5).
    "a variable that never holds a secret": ("json", {"env": {"LOG_LEVEL": "verbose"}}),
    "a switch value": ("json", {"env": {"CUSTOM_SETTING": "true"}}),
    "a number value": ("json", {"env": {"CUSTOM_SETTING": "8080"}}),
    "a key naming where a secret lives": ("json", {"auth_env_var": "MY_TOKEN"}),
    "an OpenCode env reference": ("json", {"env": {"API_TOKEN": "{env:API_TOKEN}"}}),
    "a PowerShell env reference": ("json", {"env": {"API_TOKEN": "$env:API_TOKEN"}}),
    "a braced env reference": ("json", {"env": {"API_TOKEN": "${API_TOKEN}"}}),
    "a bearer reference": ("json", {"headers": {"Authorization": "Bearer ${API_TOKEN}"}}),
}


def _bytes(value: object) -> bytes:
    return (value if isinstance(value, str) else json.dumps(value)).encode()


@pytest.mark.parametrize("case", sorted(SECRETS))
def test_each_secret_shape_is_credential_material(case: str) -> None:
    format_name, value = SECRETS[case]

    assert credential_material(_bytes(value), format_name)


@pytest.mark.parametrize("case", sorted(NOT_SECRETS))
def test_ordinary_values_stay_publishable(case: str) -> None:
    format_name, value = NOT_SECRETS[case]

    assert credential_material(_bytes(value), format_name) is None


def test_the_reason_names_what_made_a_file_private() -> None:
    document = _bytes({"mcpServers": {"x": {"env": {"DEPLOY_KEY": "abc123"}}}})

    assert credential_material(document, "json") == "env.DEPLOY_KEY"


@pytest.mark.migration_integration
def test_a_tracked_skill_mentioning_basic_steps_stays_public(tmp_path, capsys) -> None:
    """The reported regression: this skill was made private and untracked."""
    root = tmp_path / "p"
    skill = root / ".claude/skills/deploy/SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\nname: deploy\ndescription: d\n---\nThis skill covers basic deployment steps.\n"
    )
    for args in (("init", "-q"), ("add", "-A"), ("commit", "-qm", "base")):
        subprocess.run(
            ["git", "-C", str(root), "-c", "user.email=t@e", "-c", "user.name=t", *args],
            check=True,
            capture_output=True,
        )

    argv = ["init", "project", "--root", str(root), "--harness", "claude", "--yes"]
    assert loadout.main(argv) == 0

    ignored = (root / ".gitignore").read_text().splitlines()
    assert "/loadout/skills/deploy/SKILL.md" not in ignored
    assert (root / "loadout/skills/deploy/SKILL.md").is_file()


@pytest.mark.migration_integration
def test_the_preview_names_the_secret_in_each_source_file(tmp_path, fake_home, capsys) -> None:
    settings = fake_home / ".claude/settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text('{"env": {"DEPLOY_KEY": "abc123"}}\n')
    source = tmp_path / "source"
    source.mkdir()

    argv = ["init", "global", str(source), "--harness", "claude", "--dry-run"]
    assert loadout.main(argv) == 0

    adopted = source / "loadout/settings/claude.json"
    assert f"Contains a secret: {adopted}: env.DEPLOY_KEY" in capsys.readouterr().out


@pytest.mark.migration_integration
def test_codex_http_headers_are_adopted_and_named_as_a_secret(tmp_path, fake_home, capsys) -> None:
    """A Codex server's literal auth header goes into the server source as it is, renders
    back into config.toml, and is listed like any other secret (ADR 0030)."""
    config = fake_home / ".codex/config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(
        '[mcp_servers.docs]\nurl = "https://docs.test/mcp"\n'
        '[mcp_servers.docs.http_headers]\nAuthorization = "Bearer abc123"\n'
    )
    source = tmp_path / "source"
    source.mkdir()

    argv = ["init", "global", str(source), "--harness", "codex", "--yes"]
    assert loadout.main(argv) == 0

    mcp = source / "loadout/mcp.toml"
    assert tomllib.loads(mcp.read_text())["docs"] == {
        "transport": "http",
        "url": "https://docs.test/mcp",
        "headers": {"Authorization": "Bearer abc123"},
    }
    assert tomllib.loads(config.read_text())["mcp_servers"]["docs"]["http_headers"] == {
        "Authorization": "Bearer abc123"
    }
    assert f"Contains a secret: {mcp}: a value labelled as a secret" in capsys.readouterr().out


@pytest.mark.migration_integration
def test_a_codex_server_with_two_auth_sources_is_adopted_with_a_note(
    tmp_path, fake_home, capsys
) -> None:
    """Review 2, 2.6: init failed with an opaque reconstruction error and no note."""
    config = fake_home / ".codex/config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(
        '[mcp_servers.docs]\nurl = "https://docs.test/mcp"\nbearer_token_env_var = "DOCS"\n'
        '[mcp_servers.docs.http_headers]\nAuthorization = "Bearer abc123"\n'
    )
    source = tmp_path / "source"
    source.mkdir()

    argv = ["init", "global", str(source), "--harness", "codex", "--dry-run"]
    assert loadout.main(argv) == 0

    assert "docs: Authorization beside bearer_token_env_var" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("content", "format_name", "reason"),
    [
        (b'{"auth": "s3cr3tvalue"}', "json", "a value labelled as a secret"),
        (b"credential = s3cr3tvalue\n", "copy", "a value labelled as a secret"),
        (b'{"args": ["--auth", "s3cr3tvalue"]}', "json", "the value after --auth"),
        (b'{"x-api-key": "s3cr3tvalue"}', "json", "a value labelled as a secret"),
    ],
)
def test_every_place_a_secret_sits_uses_one_vocabulary(
    content: bytes, format_name: str, reason: str
) -> None:
    """Review 2, 2.14: `--auth x` was a secret and `{"auth": x}` was not, and the reverse
    for `credential`."""
    assert credential_material(content, format_name) == reason


@pytest.mark.parametrize(
    "placeholder", ["AKIAXXXXXXXXXXXXXXXX", "ASIAXXXXXXXXXXXXXXXX", "AIza" + "x" * 35]
)
def test_a_documentation_placeholder_is_not_a_secret(placeholder: str) -> None:
    assert credential_material(f"key: {placeholder}\n".encode()) is None


def test_the_preview_names_a_file_holding_a_secret_instead_of_diffing_it(capsys) -> None:
    """The warning named the secret while the render diff above it printed its value."""
    change = RenderedChange(
        path=Path("/p/settings.json"),
        kind="update",
        actual='{\n  "env": {"DEPLOY_KEY": "abc123secret"},\n  "model": "a"\n}\n',
        expected='{\n  "env": {"DEPLOY_KEY": "abc123secret"},\n  "model": "b"\n}\n',
    )

    workflow._show_render((change,))

    assert capsys.readouterr().out.splitlines()[-1] == (
        "/p/settings.json: 1 line(s) differ; diff not shown because the file holds a secret "
        "(env.DEPLOY_KEY) — read the file directly"
    )


TOKEN = b"ghp_" + b"a1B2" * 9


def test_a_binary_file_is_not_scanned() -> None:
    """Performance, should-fix 2: one 40 MB asset took init from 2.3 s to 10 s."""
    assert findings(b"\x89PNG\r\n\x00\x00" + TOKEN) == []


def _scan_in_a_subprocess(content: bytes, seconds: float) -> str:
    """The findings for `content`, from a child process given `seconds` to finish. A scan
    gone quadratic hangs rather than fails, for 55 minutes once, so the child is killed
    and the test fails instead (review 3, batch 5.11)."""
    script = (
        "import sys; from loadout.credentials import findings; "
        "print(findings(sys.stdin.buffer.read()))"
    )
    try:
        result = subprocess.run(
            [sys.executable, "-c", script],
            input=content,
            capture_output=True,
            check=True,
            timeout=seconds,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"the scan took longer than {seconds} s")
    return result.stdout.decode().strip()


@pytest.mark.integration
def test_a_token_in_text_is_found_however_large_the_file() -> None:
    """In a child: 2 MiB in-process ran the unit tier for over 20 minutes once the scan
    went quadratic (review 4, batch 4.3)."""
    found = _scan_in_a_subprocess(TOKEN + b"\n" + b"x" * (MAX_SCANNED * 2), 10)

    assert found == repr([("a token in a provider's format", TOKEN.decode())])


@pytest.mark.integration
def test_only_the_scanned_prefix_of_a_large_file_is_read() -> None:
    """The scan's cap, which init's detection keeps; the check before a diff reads the
    whole text."""
    assert _scan_in_a_subprocess(b"x" * MAX_SCANNED + b"\n" + TOKEN, 10) == "[]"


@pytest.mark.integration
def test_one_long_word_is_scanned_in_linear_time() -> None:
    """A label could start anywhere inside a run of name characters, and each start
    rescanned the rest of the run: 20k characters took 4.4 s, a 1 MB one hours."""
    assert _scan_in_a_subprocess(b"x" * 50_000, 10) == "[]"


@pytest.mark.parametrize(
    "text",
    [
        # Review 3, batch 3.3: from each `http` the user:password shape scanned to the end of
        # the line, so a 469 KB one-line JSON took 28.7 s.
        "http x " * 60_000,
        # The URL scheme restarted at every word boundary of a dotted run: 100 KB took 3.8 s.
        # At 500 KB a regression takes minutes, well past the limit (review 4, batch 4.3).
        "a." * 250_000,
    ],
    ids=["many http on one line", "a long dotted run"],
)
@pytest.mark.integration
def test_a_long_line_is_scanned_in_linear_time(text: str) -> None:
    assert _scan_in_a_subprocess(text.encode(), 10) == "[]"


def test_a_user_password_far_along_the_command_line_is_still_found() -> None:
    """The bound on the gap is wide enough for a real command line."""
    line = "curl " + "-H 'X-A: b' " * 15 + "--user admin:hunter2 https://x.test\n"
    assert credential_material(line.encode()) == "a user:password on an HTTP client's command line"


# Each of these printed part or all of its secret while diffs were redacted (review 3, 1.7).
SPELLINGS = {
    "a JSON-escaped value": ("json", '{"env": {"PASSWORD": "pa\\"ss-w0rd"}}\n'),
    "a PEM with no END line": ("md", "-----BEGIN PRIVATE KEY-----\nMIIBODY\n"),
    "a label value past punctuation": ("md", "password: hunter2!REST\n"),
    "a quoted label in plain text": ("txt", '"API_TOKEN": "abcdef123456"\n'),
    "a quoted label in unparsable JSON": ("json", '{"API_TOKEN": "abcdef123456",\n'),
    "a value holding an at sign": ("md", "password = p@ssword123\npath = /p/x\n"),
}


@pytest.mark.parametrize("case", sorted(SPELLINGS))
def test_a_file_holding_any_spelling_of_a_secret_is_not_diffed(case: str) -> None:
    format_name, text = SPELLINGS[case]
    rel = f"file.{format_name}"
    reason = credential_material(text.encode(), format_name)

    withheld = (
        f"{rel}: {text.count(chr(10))} line(s) differ; diff not shown because the file holds "
        f"a secret ({reason}) — read the file directly\n"
    )

    assert diff_lines(rel, text, "", context=1) == [withheld]


LABELLED = "a value labelled as a secret"
QUERY = "a key in a URL query"

# Each case is decided by the one rule it is named for: without that rule it reads
# otherwise (review 3, batch 5.10). A None is a value the rule keeps from being flagged.
DECIDED = {
    "password in SECRET_NAMES": (b"password: hunter2xyz\n", "copy", LABELLED),
    "secret in SECRET_NAMES": (b"client_secret = abc123xyz\n", "copy", LABELLED),
    "a placeholder label value": (b"api_key = xxxxxxxxxxxx\n", "copy", None),
    "CI is plain": (b'{"env": {"CI": "a1b2c3"}}', "json", None),
    "*_ENV is plain": (b'{"env": {"NODE_ENV": "a1b2c3"}}', "json", None),
    "PATH_* is plain": (b'{"env": {"PATH_EXTRA": "a1b2c3"}}', "json", None),
    "*_PATH is plain": (b'{"env": {"GO_PATH": "a1b2c3"}}', "json", None),
    "an env value that is not plain": (
        b'{"env": {"DEPLOY_KEY": "a1b2c3"}}',
        "json",
        "env.DEPLOY_KEY",
    ),
    "a path value is plain": (b'{"env": {"DEPLOY_KEY": "/usr/local/bin"}}', "json", None),
    "value maps case-folded": (b'{"ENV": {"DEPLOY_KEY": "a1b2c3"}}', "json", "ENV.DEPLOY_KEY"),
    "a flag starts with a dash": (b'{"args": ["token", "a1b2c3d4"]}', "json", None),
    "query key": (b"https://x.test/v1?key=abcdefgh12\n", "copy", QUERY),
    "query api_key": (b"https://x.test/v1?a=1&api_key=abcdefgh12\n", "copy", QUERY),
    "query apikey": (b"https://x.test/v1?apikey=abcdefgh12\n", "copy", QUERY),
    "query access_token": (b"https://x.test/v1?access_token=abcdefgh12\n", "copy", QUERY),
    "query token": (b"https://x.test/v1?a=1&token=abcdefgh12\n", "copy", QUERY),
    "the basic shape": (
        b"Authorization: Basic dXNlcjpwYXNzd29yZA==\n",
        "copy",
        "an HTTP Basic credential",
    ),
    "the aws shape": (
        b"aws_secret_access_key = wJalrXUtnFEMIK7MDENG\n",
        "copy",
        "an aws_secret_access_key",
    ),
}


@pytest.mark.parametrize("case", sorted(DECIDED))
def test_each_rule_decides_a_case_alone(case: str) -> None:
    content, format_name, reason = DECIDED[case]

    assert credential_material(content, format_name) == reason


@pytest.mark.parametrize(
    ("offset", "reason"),
    [(BINARY_PROBE - 1, None), (BINARY_PROBE, "a token in a provider's format")],
)
def test_a_nul_counts_as_binary_only_inside_the_probe(offset: int, reason: str | None) -> None:
    content = b"x" * offset + b"\0 " + TOKEN + b"\n"

    assert credential_material(content) == reason


@pytest.mark.parametrize(("extra", "reason"), [(0, "env.MY_SERVICE"), (1, None)])
def test_a_document_is_parsed_up_to_the_cap_and_no_further(extra: int, reason: str | None) -> None:
    """A secret only the document's structure shows is found in a file of exactly
    MAX_SCANNED bytes and not in one a byte longer."""
    head = b'{"env": {"MY_SERVICE": "a1b2c3d4"}, "pad": "'
    tail = b'"}'
    content = head + b"y" * (MAX_SCANNED + extra - len(head) - len(tail)) + tail

    assert credential_material(content, "json") == reason


KEY = "FAKE" + "abcdef1234567890"


@pytest.mark.parametrize(
    ("line", "value"),
    [
        # After `: ` a value runs to the line's end or a comment, YAML style; after a
        # bare `=` to the first whitespace, shell style (review 6, batch 3).
        ("password: horse battery staple", "horse battery staple"),
        ("password: FAKEabc123 # used by staging", "FAKEabc123"),
        ("password: FAKE abc123 // staging", "FAKE abc123"),
        ("password: FAKEabc123#staging", "FAKEabc123#staging"),
        ("db_password = pass w0rd FAKE", "pass w0rd FAKE"),
        ("export API_TOKEN=FAKEabc123 then run make dev", "FAKEabc123"),
        ("password:FAKEabc123 then more", "FAKEabc123"),
        ("Authorization: Token abc123def456 extra", "abc123def456"),
        ("Authorization: Digest abc123def456", "abc123def456"),
        ("Authorization: Bearer abc123def456", "abc123def456"),
        ("Authorization: Token\tabc123def456", "abc123def456"),
        ("OPENAI_KEY=abc123def456", "abc123def456"),
        ("client_secret: abc123def456", "abc123def456"),
        ("signingKey: abc123def456", "abc123def456"),
    ],
)
def test_a_labelled_value_is_read_to_its_end(line: str, value: str) -> None:
    assert findings(f"{line}\nnext\n".encode()) == [(LABELLED, value)]


@pytest.mark.parametrize("header", ["|", "|-", "|+", ">2", "|-2", "|2-", ">+1", ">1+"])
def test_a_block_scalar_s_body_is_the_value_whichever_order_its_indicators_take(
    header: str,
) -> None:
    """Review 6, batch 1.2: `|2-`, indentation before chomping, was read as the value."""
    text = f"password: {header}\n  {KEY}\n  second line\nnext: x\n"

    assert findings(text.encode()) == [(LABELLED, f"{KEY}\n  second line")]


@pytest.mark.parametrize(
    "text",
    [
        f"env:\n  GITHUB_TOKEN: {KEY}\n",
        f"mcpServers:\n  gh:\n    env:\n      API_KEY: {KEY}\n",
        f"env:\n  password: |\n    {KEY}\n",
        f"notes:\npassword: {KEY}\n",
    ],
    ids=["env", "mcpServers", "block", "after a parent"],
)
def test_a_secret_nested_under_a_parent_key_is_found(text: str) -> None:
    """Review 6, batch 1.1: a label's separator spanned the newline, so `env:` took the
    next line as its value and the real label was never read."""
    assert findings(text.encode()) == [(LABELLED, KEY)]


@pytest.mark.parametrize(
    ("text", "why"),
    [
        (
            f'prefix_rule(pattern=["mycli", "--api-key", "{KEY}"], decision="allow")\n',
            "the value after --api-key",
        ),
        (f"curl --token {KEY} https://x\n", "the value after --token"),
        (f'curl --token "{KEY}" -s\n', "the value after --token"),
        (f"curl --api-key={KEY} -s\n", "the value of --api-key"),
    ],
    ids=["codex rules array", "flag then value", "flag then quoted value", "flag=value"],
)
def test_a_secret_flag_s_value_in_text_is_found(text: str, why: str) -> None:
    """Review 6, batch 2.6: outside a parsed document, only `label: value` was."""
    assert findings(text.encode()) == [(why, KEY)]


@pytest.mark.parametrize(
    ("text", "found"),
    [
        ('{"env": {"DATABASE": "abc\\u0064efgh1234"}}\n', [("env.DATABASE", "abcdefgh1234")]),
        ('{"env": {"DATABASE": "hunt2"}}\n', [("env.DATABASE", "hunt2")]),
        # A hand edit's trailing comma leaves the document unparsable (review 6, batch 2.6).
        (
            f'{{"env": {{"MY_SERVICE": "{KEY}", "LOG_LEVEL": "debug",}}, "x": 1}}\n',
            [("env.MY_SERVICE", KEY)],
        ),
    ],
    ids=["escaped", "short", "unparsable"],
)
def test_a_value_map_entry_is_found_by_where_it_sits(
    text: str, found: list[tuple[str, str]]
) -> None:
    assert findings(text.encode(), "json") == found


@pytest.mark.parametrize(
    "text",
    [
        "use sk_" + "live_4eC39HqLyjWDarjtT1zdp7dc here",
        "use rk_" + "test_4eC39HqLyjWDarjtT1zdp7dc here",
        "hf_token hf_" + "AbCdEfGhIjKlMnOpQrStUvWxYz0123",
        "post to https://hooks.slack.com/services/T0000/B0000/XXXXabcdEFGH1234",
        "key django-insecure-0123456789abcdefghij",
        "x-ghp_" + "a1B2" * 9 + "-y",
    ],
)
def test_a_provider_s_token_shape_is_credential_material(text: str) -> None:
    assert credential_material(text.encode()) is not None


@pytest.mark.parametrize(
    "name", ["SECRET_KEY", "STRIPE_SECRET_KEY", "client_secret", "signingKey", "OPENAI_KEY"]
)
def test_the_vocabulary_names_these_secrets(name: str) -> None:
    assert names_a_secret(name)


def test_a_provider_names_a_key_only_directly_before_it() -> None:
    """Review 6, batch 3 (rx-provider-pos)."""
    assert [names_a_secret(name) for name in ("my_openai_key", "openai_model_key")] == [
        True,
        False,
    ]
