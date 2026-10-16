"""Secret shapes the credential check recognises, so init names each source file holding
one (review 1.8, D-07), and the ordinary values it must leave alone (re-review of 798a4f2)."""

from __future__ import annotations

import json
import subprocess

import pytest

import loadout
from loadout.discovery import credential_material

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
def test_codex_http_headers_are_reported_as_not_adopted(tmp_path, fake_home, capsys) -> None:
    """A Codex server's literal auth header has nowhere to go in the server source; the
    preview says so rather than dropping it silently."""
    config = fake_home / ".codex/config.toml"
    config.parent.mkdir(parents=True)
    config.write_text(
        '[mcp_servers.docs]\nurl = "https://docs.test/mcp"\n'
        '[mcp_servers.docs.http_headers]\nAuthorization = "Bearer abc123"\n'
    )
    source = tmp_path / "source"
    source.mkdir()

    argv = ["init", "global", str(source), "--harness", "codex", "--dry-run"]
    assert loadout.main(argv) == 0

    assert (
        f"{config}: codex-servers: docs carries http_headers, which loadout does not adopt"
        in capsys.readouterr().out
    )
