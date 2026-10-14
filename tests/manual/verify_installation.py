from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

parser = argparse.ArgumentParser()
parser.add_argument("tool_root", type=Path)
parser.add_argument("work", type=Path)
options = parser.parse_args()
tool_root = options.tool_root.resolve()
work = options.work.resolve()
work.mkdir(parents=True)
cli = tool_root / "bin" / "loadout"
env = os.environ.copy()
env.update(
    XDG_CONFIG_HOME=str(work / "config"),
    GIT_AUTHOR_NAME="Loadout verification",
    GIT_AUTHOR_EMAIL="verification@example.invalid",
    GIT_COMMITTER_NAME="Loadout verification",
    GIT_COMMITTER_EMAIL="verification@example.invalid",
)
records = []


def run(name, args, cwd, expected=0):
    result = subprocess.run(
        [str(cli), *args],
        cwd=cwd,
        env=env,
        input="",
        text=True,
        capture_output=True,
        timeout=45,
        check=False,
    )
    record = {
        "name": name,
        "args": args,
        "cwd": str(cwd),
        "exit": result.returncode,
        "stdout": result.stdout,
        "stderr": result.stderr,
    }
    records.append(record)
    (work / "commands.json").write_text(json.dumps(records, indent=2))
    print(json.dumps({"name": name, "exit": result.returncode}), flush=True)
    assert result.returncode == expected, record
    return result


def put(path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


def outputs(project):
    return {
        str(path.relative_to(project)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in project.rglob("*")
        if path.is_file()
        and path.relative_to(project).parts[0] not in {".git", "loadout", ".loadout-state"}
    }


for args in [
    ["--help"],
    ["sync", "--help"],
    ["check", "--help"],
    ["init", "--help"],
    ["template", "add", "--help"],
    ["template", "list", "--help"],
    ["template", "sync", "--help"],
    ["integrate", "skill", "install", "--help"],
    ["integrate", "skill", "status", "--help"],
    ["integrate", "skill", "uninstall", "--help"],
    ["integrate", "git-hooks", "install", "--help"],
    ["integrate", "git-hooks", "status", "--help"],
    ["integrate", "git-hooks", "uninstall", "--help"],
    ["integrate", "completions", "install", "--help"],
    ["integrate", "completions", "status", "--help"],
    ["integrate", "completions", "uninstall", "--help"],
]:
    run("help " + " ".join(args[:-1]), args, work)
run("invalid command", ["not-a-command"], work, 2)
run("conflicting scopes", ["sync", "--root", str(work), "--global"], work, 2)
run("missing global config", ["check", "--global"], work, 3)

project = work / "project with spaces ü"
project.mkdir()
# Project init routes only configuration that already exists, so adopt a setup rather
# than an empty directory: an empty one yields `artifact = []` and nothing to render.
for relative, body in {
    "CLAUDE.md": "# Project\n",
    "AGENTS.md": "# Project\n",
    ".codex/rules/permissions.rules": 'prefix_rule(pattern=["ls"], decision="allow")\n',
    ".claude/settings.json": '{"permissions": {"allow": ["Bash(ls:*)"]}}\n',
    "opencode.json": '{"permission": {"bash": {"ls*": "allow"}}}\n',
    ".pi/extensions/pi-permission-system/config.json": (
        '{"permission": {"bash": {"ls*": "allow"}}}\n'
    ),
    ".claude/skills/install-seed/SKILL.md": (
        "---\nname: install-seed\ndescription: Seed skill.\n---\nSeed body.\n"
    ),
}.items():
    put(project / relative, body)
adopted = outputs(project)
harnesses = [
    value for agent in ("claude", "codex", "opencode", "pi") for value in ("--harness", agent)
]
init = ["init", "project", *harnesses]
preview = json.loads(run("fresh init preview", [*init, "--dry-run", "--json"], project).stdout)
assert preview["complete"] and preview["validated"] and preview["issues"] == []
assert outputs(project) == adopted
applied = json.loads(run("fresh init", [*init, "--yes", "--json"], project).stdout)
assert applied["status"] == "complete"
configuration = project / "loadout"
instructions = "# Installation verification\nKeep this shared setup.\n"
put(configuration / "instructions/main.md", instructions)
adopted_config = tomllib.loads((configuration / "config.toml").read_text())
assert adopted_config["harnesses"] == ["claude", "codex", "opencode", "pi"]
assert adopted_config["instructions"] == ["main"]
put(configuration / "permissions.toml", '[shell]\nallow = ["git status"]\n')
probe = "---\nname: install-probe\ndescription: Verify packaged CLI output.\n---\nProbe body.\n"
put(configuration / "skills/install-probe/SKILL.md", probe)
skill_outputs = [
    project / path / "install-probe/SKILL.md" for path in (".claude/skills", ".agents/skills")
]
run("render all four agents", ["sync"], project)
run("check rendered outputs", ["check"], project)
assert instructions.strip() in (project / "AGENTS.md").read_text()
assert "@AGENTS.md" in (project / "CLAUDE.md").read_text()
assert "git" in (project / ".codex/rules/permissions.rules").read_text()
assert all(path.read_text().endswith("Probe body.\n") for path in skill_outputs)
before = outputs(project)
run("idempotent sync", ["sync"], project)
assert outputs(project) == before

instructions += "Updated shared instruction.\n"
put(configuration / "instructions/main.md", instructions)
run("source change reports drift", ["check"], project, 1)
run("sync source change", ["sync"], project)
run("check updated output", ["check"], project)
assert (project / "AGENTS.md").read_text().endswith(instructions)
assert "@AGENTS.md" in (project / "CLAUDE.md").read_text()
edited = project / "AGENTS.md"
edited.write_text(instructions + "External edit.\n")
run("protect external output edit", ["sync"], project, 1)
assert edited.read_text() == instructions + "External edit.\n"
run("explicit force adoption", ["sync", "--force"], project)
assert edited.read_text().endswith(instructions)
run("check after force", ["check"], project)

config = configuration / "config.toml"
original = config.read_bytes()
config.write_text("[invalid\n")
before = outputs(project)
run("invalid source refuses sync", ["sync"], project, 3)
assert outputs(project) == before
config.write_bytes(original)
repeat = json.loads(run("repeat init", [*init, "--yes", "--json"], project).stdout)
assert repeat["already_initialized"]

run("track bundled template", ["template", "add", "frontend", "--tracked"], project)
run("vendor bundled template", ["template", "add", "frontend", "--vendored"], project)
run("render template", ["sync"], project)
run("check template", ["check"], project)
listed = run("list template", ["template", "list"], project).stdout
assert "frontend" in listed
vendored = configuration / "templates/frontend/instructions.md"
template_body = vendored.read_text()
assert template_body.strip() in (project / "AGENTS.md").read_text()
assert "@AGENTS.md" in (project / "CLAUDE.md").read_text()
run("update unchanged template", ["template", "sync", "frontend"], project)
vendored.write_text(template_body + "\nLocal change.\n")
run("protect edited template", ["template", "sync", "frontend"], project, 1)
assert vendored.read_text() == template_body + "\nLocal change.\n"
vendored.write_text(template_body)
run("check restored template", ["check"], project)

templated = work / "backend template"
templated.mkdir()
result = json.loads(run("backend template init", [*init, "--yes", "--json"], templated).stdout)
assert result["status"] == "complete"
run("backend template add", ["template", "add", "backend", "--vendored"], templated)
run("backend template sync", ["sync"], templated)
backend = (templated / "loadout/templates/backend/instructions.md").read_text().strip()
assert backend in (templated / "AGENTS.md").read_text()
assert "@AGENTS.md" in (templated / "CLAUDE.md").read_text()
run("backend template check", ["check"], templated)

global_source = work / "global source"
global_source.mkdir()
put(
    global_source / "loadout.toml",
    """[[source]]
name = "self"
path = "."
use = ["skills"]

[claude]
skills = true
[codex]
skills = true
[opencode]
skills = true
[pi]
skills = true
""",
)
(global_source / "skills").mkdir()
env.update(
    CLAUDE_CONFIG_DIR=str(work / "global outputs/claude"),
    CODEX_HOME=str(work / "global outputs/codex"),
    PI_CODING_AGENT_DIR=str(work / "global outputs/pi"),
)
put(work / "config/loadout/config.toml", f"source = {json.dumps(str(global_source))}\n")
run("install packaged skill for four agents", ["integrate", "skill", "install", "--yes"], work)
destinations = [
    work / "global outputs/claude/skills",
    work / "global outputs/codex/skills",
    work / "config/opencode/skills",
    work / "global outputs/pi/skills",
]
for destination in destinations:
    for relative in ("SKILL.md", "references/configuration.md", "references/onboarding.md"):
        assert (destination / "loadout" / relative).stat().st_size > 0
run("packaged skill status", ["integrate", "skill", "status"], work)
run("packaged skill reinstall", ["integrate", "skill", "install", "--yes"], work)
run("global check", ["check", "--global"], work)
sentinel = destinations[0] / "notes.txt"
sentinel.write_text("Keep this unrelated file.\n")
run("uninstall packaged skill", ["integrate", "skill", "uninstall", "--yes"], work)
assert sentinel.read_text() == "Keep this unrelated file.\n"
assert all(not (destination / "loadout/SKILL.md").exists() for destination in destinations)
run("global check after uninstall", ["check", "--global"], work)

summary = {
    "status": "passed",
    "commands": len(records),
    "python": sys.version,
    "executable": str(cli),
    "work": str(work),
    "project_outputs": outputs(project),
}
(work / "summary.json").write_text(json.dumps(summary, indent=2))
print(json.dumps(summary, indent=2))
