from __future__ import annotations

import hashlib
import json
import os
import select
import shutil
import signal
import subprocess
import sys
import time
import tomllib
from datetime import UTC, datetime
from pathlib import Path

import pytest

import loadout
from loadout.output import CATEGORIES

ROOT = Path(__file__).resolve().parents[1]
CLI = (sys.executable, "-m", "loadout")


def _git(root, *arguments):
    result = subprocess.run(
        ["git", "-C", str(root), *arguments], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def _repository(path):
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.name", "Loadout QA")
    _git(path, "config", "user.email", "qa@example.invalid")
    (path / "README.md").write_text("Disposable Loadout QA repository.\n")
    _git(path, "add", "README.md")
    _git(path, "commit", "-qm", "fixture")
    return path


def _code_identity():
    digest = hashlib.sha256()
    for path in sorted((ROOT / "src/loadout").rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            digest.update(str(path.relative_to(ROOT)).encode() + b"\0")
            digest.update(str(path.stat().st_mode & 0o7777).encode() + b"\0")
            digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()


class Evidence:
    def __init__(self, path):
        self.path = path
        self.data = {
            "time": datetime.now(UTC).isoformat(),
            "python": sys.executable,
            "version": loadout.__version__,
            "package": str(Path(loadout.__file__).resolve()),
            "revision": _git(ROOT, "rev-parse", "HEAD").strip(),
            "source_sha256": _code_identity(),
            "driver_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "events": [],
        }

    def save(self, **facts):
        self.data.update(facts)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.data, indent=2) + "\n")

    def cli(self, root, *arguments, expected=0, input_text=None):
        command = [*CLI, *arguments]
        result = subprocess.run(
            command,
            cwd=root,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.data["events"].append(
            {
                "command": command,
                "cwd": str(root),
                "exit": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        )
        self.save()
        assert result.returncode == expected, result.stdout + result.stderr
        return result


@pytest.fixture
def evidence(request, tmp_path):
    directory = Path(os.environ.get("LOADOUT_QA_REPORT_DIR", str(tmp_path / "evidence")))
    record = Evidence(directory / f"{request.node.name}.json")
    assert Path(loadout.__file__).resolve() == ROOT / "src/loadout/__init__.py"
    yield record
    record.save()


def _instruction_source(root):
    config = tomllib.loads((root / "loadout/config.toml").read_text())
    assert config["instructions"] == ["main"]
    return root / "loadout/instructions/main.md"


def _project_example(root, before, evidence):
    example = {
        "before": before,
        "source": {
            str(path.relative_to(root)): path.read_bytes().decode()
            for path in sorted((root / "loadout").rglob("*"))
            if path.is_file()
        },
        "after": {name: (root / name).read_bytes().decode() for name in before},
    }
    evidence.save(example=example)
    reconstructed = root.parent / "git-source-reconstruction"
    reconstructed.mkdir()
    _git(
        root,
        "checkout-index",
        f"--prefix={reconstructed}/",
        "--",
        *sorted(example["source"]),
    )
    evidence.cli(reconstructed, "sync", "--root", str(reconstructed))
    example["reconstructed"] = {
        name: (reconstructed / name).read_bytes().decode() for name in before
    }
    assert example["reconstructed"] == example["after"]
    evidence.cli(reconstructed, "check", "--root", str(reconstructed))
    evidence.save(example=example, reconstruction_source="Git index, authored Loadout files only")


def test_empty_project_preview_approval_and_repeat(tmp_path, evidence):
    root = _repository(tmp_path / "empty project ü")
    initial = _git(root, "rev-parse", "HEAD")
    arguments = ["init", "project", "--json"]
    for agent in ("claude", "codex", "opencode", "pi"):
        arguments.extend(("--harness", agent))
    preview = json.loads(evidence.cli(root, *arguments, "--dry-run").stdout)
    assert preview["complete"]
    assert _git(root, "status", "--porcelain") == ""
    error = json.loads(evidence.cli(root, *arguments, expected=2, input_text="").stdout)
    assert error["status"] == "error"
    assert _git(root, "status", "--porcelain") == ""
    result = json.loads(evidence.cli(root, *arguments, "--yes").stdout)
    assert result["status"] == "complete"
    assert result["already_initialized"] is False
    source = sorted(path.name for path in (root / "loadout").iterdir())
    assert source == ["config.toml", "permissions.toml"]
    assert not (root / "AGENTS.md").exists()
    assert not (root / "CLAUDE.md").exists()
    evidence.cli(root, "check")
    _git(root, "add", "loadout", ".gitignore")
    evidence.cli(root, "check", "--staged")
    before_repeat = _git(root, "diff", "--cached", "--binary")
    repeated = json.loads(evidence.cli(root, *arguments, "--yes").stdout)
    assert repeated["already_initialized"] is True
    assert _git(root, "rev-parse", "HEAD") == initial
    assert _git(root, "diff", "--cached", "--binary") == before_repeat
    evidence.save(result="Pass", scenarios=["Q01"], source=source)


def test_populated_project_sources_index_and_drift(tmp_path, evidence):
    root = _repository(tmp_path / "populated")
    (root / ".claude").mkdir()
    settings = {"permissions": {"allow": ["Bash(git status)", "Read(src/**)"]}, "model": "qa-model"}
    (root / ".claude/settings.json").write_text(json.dumps(settings, indent=2) + "\n")
    (root / "CLAUDE.md").write_bytes(b"Existing guidance.\r\n")
    (root / "notes.txt").write_text("base\n")
    _git(root, "add", "notes.txt", "CLAUDE.md", ".claude/settings.json")
    _git(root, "commit", "-qm", "baseline")
    initial = _git(root, "rev-parse", "HEAD")
    (root / "notes.txt").write_text("staged note\n")
    _git(root, "add", "notes.txt")
    (root / "notes.txt").write_text("unstaged note\n")
    arguments = ["init", "project", "--harness", "claude", "--harness", "codex", "--json"]
    example_before = {
        name: (root / name).read_bytes().decode() for name in (".claude/settings.json", "CLAUDE.md")
    }
    result = json.loads(evidence.cli(root, *arguments, "--yes").stdout)
    assert result["status"] == "complete"
    assert result["untracked"] == [".claude/settings.json", "CLAUDE.md"]
    assert _git(root, "rev-parse", "HEAD") == initial
    assert _git(root, "show", "HEAD:notes.txt") == "base\n"
    assert _git(root, "show", ":notes.txt") == "staged note\n"
    assert (root / "notes.txt").read_text() == "unstaged note\n"
    rendered = json.loads((root / ".claude/settings.json").read_text())
    assert rendered["model"] == "qa-model"
    assert rendered["permissions"]["allow"] == settings["permissions"]["allow"]
    assert (root / "CLAUDE.md").read_text().endswith("@AGENTS.md\n")
    assert (root / "AGENTS.md").read_bytes().endswith(b"Existing guidance.\n")
    tracked = _git(root, "ls-files").splitlines()
    assert "CLAUDE.md" not in tracked
    assert ".claude/settings.json" not in tracked
    evidence.cli(root, "check")
    _git(root, "add", "loadout", ".gitignore")
    _project_example(root, example_before, evidence)
    source = _instruction_source(root)
    source.write_bytes(source.read_bytes() + b"QA source edit.\n")
    evidence.cli(root, "sync")
    synced = (root / "AGENTS.md").read_bytes()
    assert synced.endswith(b"QA source edit.\n")
    (root / "AGENTS.md").write_text("External QA edit.\n")
    drift = evidence.cli(root, "sync", expected=1)
    assert "AGENTS.md" in drift.stderr + drift.stdout
    assert (root / "AGENTS.md").read_text() == "External QA edit.\n"
    (root / "AGENTS.md").write_bytes(synced)
    evidence.cli(root, "sync")
    evidence.cli(root, "check")
    evidence.save(result="Pass", scenarios=["Q02", "Q05", "Q14"], source=str(source))


def test_global_runtime_fields_and_source_managed_skill(tmp_path, fake_home, evidence):
    root = _repository(tmp_path / "global source")
    pi = fake_home / ".pi/agent/settings.json"
    pi.parent.mkdir(parents=True)
    pi.write_text(json.dumps({"defaultModel": "qa-model", "lastChangelogVersion": "qa-runtime"}))
    arguments = ["init", "global", str(root), "--json"]
    for agent in ("claude", "codex", "opencode", "pi"):
        arguments.extend(("--harness", agent))
    result = json.loads(evidence.cli(root, *arguments, "--yes").stdout)
    assert result["status"] == "complete"
    source = root / "loadout"
    assert (source / "loadout.toml").is_file()
    machine = tomllib.loads((fake_home / ".config/loadout/config.toml").read_text())
    assert Path(machine["source"]) == source
    evidence.cli(root, "check", "--global")
    runtime = json.loads(pi.read_text())
    pi.write_text(json.dumps({**runtime, "lastChangelogVersion": "qa-runtime-2"}, indent=2) + "\n")
    evidence.cli(root, "sync", "--global")
    assert json.loads(pi.read_text())["lastChangelogVersion"] == "qa-runtime-2"
    evidence.cli(root, "integrate", "skill", "install", "--yes")
    installed = [
        fake_home / ".claude/skills/loadout/SKILL.md",
        fake_home / ".codex/skills/loadout/SKILL.md",
        fake_home / ".config/opencode/skills/loadout/SKILL.md",
        fake_home / ".pi/agent/skills/loadout/SKILL.md",
    ]
    bundled = ROOT / "src/loadout/_skills/loadout/SKILL.md"
    body = bundled.read_text().split("---\n", 2)[2]
    assert all(path.read_text().endswith(body) for path in installed)
    authored = list((source / "skills").rglob("loadout/SKILL.md"))
    assert authored
    assert all(path.read_bytes() == bundled.read_bytes() for path in authored)
    evidence.cli(root, "integrate", "skill", "status")
    evidence.cli(root, "integrate", "skill", "install", "--yes")
    evidence.cli(root, "check", "--global")
    assert json.loads(pi.read_text())["lastChangelogVersion"] == "qa-runtime-2"
    evidence.cli(root, "integrate", "skill", "uninstall", "--yes")
    assert all(not path.exists() for path in installed)
    evidence.cli(root, "check", "--global")
    evidence.save(
        result="Pass", scenarios=["Q03", "Q14", "Q20"], authored=[str(path) for path in authored]
    )


def test_global_source_directory_copy_is_not_read(tmp_path, fake_home, evidence):
    root = _repository(tmp_path / "dotfiles global")
    native = fake_home / ".claude/settings.json"
    copy = root / "claude/settings.json"
    native.parent.mkdir(parents=True)
    copy.parent.mkdir()
    native.write_text('{"model":"live-qa-model"}\n')
    copy.write_text('{"model":"source-qa-model"}\n')
    _git(root, "add", "claude/settings.json")
    _git(root, "commit", "-qm", "source configuration")
    arguments = ["init", "global", str(root), "--harness", "claude", "--json"]
    preview = json.loads(evidence.cli(root, *arguments, "--dry-run").stdout)
    assert preview["complete"] is True
    assert {candidate["path"] for candidate in preview["candidates"]} == {str(native)}
    result = json.loads(evidence.cli(root, *arguments, "--yes").stdout)
    assert result["status"] == "complete"
    assert json.loads(native.read_text())["model"] == "live-qa-model"
    assert copy.read_text() == '{"model":"source-qa-model"}\n'
    evidence.cli(root, "check", "--global")
    evidence.save(result="Pass", scenarios=["Q04"])


@pytest.mark.parametrize("template", ["frontend", "backend"])
def test_bundled_template_after_init_through_cli(tmp_path, evidence, template):
    root = _repository(tmp_path / template)
    arguments = ["init", "project", "--harness", "codex", "--json"]
    result = json.loads(evidence.cli(root, *arguments, "--yes").stdout)
    assert result["status"] == "complete"
    evidence.cli(root, "template", "add", template, "--vendored")
    evidence.cli(root, "sync")
    config = tomllib.loads((root / "loadout/config.toml").read_text())
    assert config["templates"] == [template]
    assert config["template"][template]["vendored"]
    vendored = root / "loadout/templates" / template
    assert {path.name for path in vendored.iterdir() if path.is_dir()} == CATEGORIES
    instructions = (vendored / "instructions.md").read_text().strip()
    assert instructions
    assert instructions in (root / "AGENTS.md").read_text()
    evidence.cli(root, "check")
    evidence.save(result="Pass", scenarios=["Q17"], template=template)


def _read_pty(descriptor, process, predicate, timeout=30):
    chunks = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ready, _, _ = select.select([descriptor], [], [], 0.05)
        if ready:
            try:
                chunk = os.read(descriptor, 65536)
            except OSError:
                break
            if not chunk:
                break
            chunks.append(chunk)
            if predicate(b"".join(chunks)):
                return b"".join(chunks)
        elif process.poll() is not None:
            break
    return b"".join(chunks)


@pytest.mark.parametrize("action", ["decline", "interrupt"])
def test_interactive_confirmation_cancellation(tmp_path, evidence, action):
    root = _repository(tmp_path / action)
    before = _git(root, "rev-parse", "HEAD")
    command = [*CLI, "init", "project", "--harness", "codex"]
    master, slave = os.openpty()
    process = subprocess.Popen(
        command, cwd=root, stdin=slave, stdout=slave, stderr=slave, start_new_session=True
    )
    os.close(slave)
    transcript = b""
    try:
        prompt = b"Apply this migration? [y/N]"
        transcript = _read_pty(master, process, lambda output: prompt in output)
        assert prompt in transcript
        assert process.poll() is None
        assert _git(root, "status", "--porcelain") == ""
        if action == "decline":
            os.write(master, b"n\n")
        else:
            process.send_signal(signal.SIGINT)
        transcript += _read_pty(master, process, lambda _: False)
        assert process.wait(timeout=5) == 0
        expected = (
            b"declined; no changes made" if action == "decline" else b"cancelled; no changes made"
        )
        assert expected in transcript
        assert _git(root, "rev-parse", "HEAD") == before
        assert _git(root, "status", "--porcelain") == ""
        evidence.save(result="Pass", scenarios=["Q04"], pending_observed=True, action=action)
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        os.close(master)
        evidence.data["events"].append(
            {
                "command": command,
                "cwd": str(root),
                "exit": process.returncode,
                "pty": transcript.decode(errors="replace"),
            }
        )
        evidence.save()


def test_prepare_source_managed_host_fixture(tmp_path, fake_home, evidence):
    root = _repository(tmp_path / "host fixture")
    evidence.cli(root, "init", "project", "--harness", "claude", "--yes", "--json")
    source = root / "loadout/skills/loadout"
    shutil.copytree(ROOT / "src/loadout/_skills/loadout", source)
    evidence.cli(root, "sync")
    installed = root / ".claude/skills/loadout/SKILL.md"
    body = (source / "SKILL.md").read_text().split("---\n", 2)[2]
    assert installed.read_text().endswith(body)
    isolated_config = fake_home / ".claude"
    isolated_config.mkdir()
    evidence.save(
        host_fixture={
            "cwd": str(root),
            "config_directory": str(isolated_config),
            "installed_skill": str(installed),
            "skill_sha256": hashlib.sha256(installed.read_bytes()).hexdigest(),
            "host_execution": "not run",
        }
    )
