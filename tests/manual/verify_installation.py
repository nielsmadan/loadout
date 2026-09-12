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
    ["explain", "--help"],
    ["harness", "add", "--help"],
    ["template", "add", "--help"],
    ["template", "list", "--help"],
    ["template", "vendor", "--help"],
    ["template", "sync", "--help"],
    ["skill", "install", "--help"],
    ["skill", "status", "--help"],
    ["skill", "uninstall", "--help"],
    ["git-hooks", "install", "--help"],
]:
    run("help " + " ".join(args[:-1]), args, work)
run("invalid command", ["not-a-command"], work, 2)
run("conflicting scopes", ["sync", "--root", str(work), "--global"], work, 2)
run("missing global config", ["check", "--global"], work, 3)

project = work / "project with spaces ü"
project.mkdir()
harnesses = [
    value for agent in ("claude", "codex", "opencode", "pi") for value in ("--harness", agent)
]
init = ["init", "--project", *harnesses]
preview = json.loads(run("fresh init preview", [*init, "--dry-run", "--json"], project).stdout)
assert preview["complete"] and preview["validated"] and preview["issues"] == []
assert list(project.iterdir()) == []
applied = json.loads(run("fresh init", [*init, "--yes", "--json"], project).stdout)
assert applied["status"] == "complete"
configuration = project / "loadout"
routes = tomllib.loads((configuration / "artifacts.toml").read_text())["artifact"]
route_by_output = {route["output"]: route for route in routes}
instructions = "# Installation verification\nKeep this shared setup.\n"
for output in ("CLAUDE.md", "AGENTS.md"):
    put(configuration / route_by_output[output]["source"], instructions)
rule = 'prefix_rule(pattern=["git", "status"], decision="allow")\n'
put(configuration / route_by_output[".codex/rules/permissions.rules"]["source"], rule)
settings = {
    ".claude/settings.json": {"permissions": {"allow": ["Bash(git status:*)"]}},
    "opencode.json": {"permission": {"bash": {"git status*": "allow"}}},
    ".pi/extensions/pi-permission-system/config.json": {
        "permission": {"bash": {"git status*": "allow"}}
    },
}
for output, content in settings.items():
    source = route_by_output[output]["parts"]["permissions"]["source"]
    put(configuration / source, json.dumps(content) + "\n")
probe = "---\nname: install-probe\ndescription: Verify packaged CLI output.\n---\nProbe body.\n"
skill_outputs = []
for route in routes:
    if route.get("category") == "skills":
        put(configuration / route["source"] / "install-probe/SKILL.md", probe)
        skill_outputs.append(project / route["output"] / "install-probe/SKILL.md")
run("render all four agents", ["sync"], project)
run("check rendered outputs", ["check"], project)
for output in ("CLAUDE.md", "AGENTS.md"):
    assert (project / output).read_text() == instructions
assert (project / ".codex/rules/permissions.rules").read_text() == rule
for output, content in settings.items():
    assert json.loads((project / output).read_text()) == content
assert all(path.read_text() == probe for path in skill_outputs)
before = outputs(project)
run("idempotent sync", ["sync"], project)
assert outputs(project) == before

instructions += "Updated shared instruction.\n"
for output in ("CLAUDE.md", "AGENTS.md"):
    put(configuration / route_by_output[output]["source"], instructions)
run("source change reports drift", ["check"], project, 1)
run("sync source change", ["sync"], project)
run("check updated output", ["check"], project)
assert all((project / output).read_text() == instructions for output in ("CLAUDE.md", "AGENTS.md"))
edited = project / "AGENTS.md"
edited.write_text(instructions + "External edit.\n")
run("protect external output edit", ["sync"], project, 1)
assert edited.read_text() == instructions + "External edit.\n"
run("explicit force adoption", ["sync", "--force"], project)
assert edited.read_text() == instructions
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

run("add bundled template", ["template", "add", "frontend"], project)
run("vendor bundled template", ["template", "vendor", "frontend"], project)
run("render template", ["sync"], project)
run("check template", ["check"], project)
listed = run("list template", ["template", "list"], project).stdout
assert "frontend" in listed
vendored = configuration / "templates/frontend/instructions.md"
template_body = vendored.read_text()
assert template_body.strip() in (project / "CLAUDE.md").read_text()
assert template_body.strip() in (project / "AGENTS.md").read_text()
run("update unchanged template", ["template", "sync", "frontend"], project)
vendored.write_text(template_body + "\nLocal change.\n")
run("protect edited template", ["template", "sync", "frontend"], project, 1)
assert vendored.read_text() == template_body + "\nLocal change.\n"
vendored.write_text(template_body)
run("check restored template", ["check"], project)

starter = work / "backend starter"
starter.mkdir()
result = json.loads(
    run("backend starter init", [*init, "--starter", "backend", "--yes", "--json"], starter).stdout
)
assert result["status"] == "complete"
backend = (starter / "loadout/templates/backend/instructions.md").read_text().strip()
assert backend in (starter / "CLAUDE.md").read_text()
run("backend starter check", ["check"], starter)

global_source = work / "global source"
global_source.mkdir()
put(global_source / "loadout.toml", 'artifacts = "artifacts.toml"\n')
destinations = []
blocks = []
for agent in ("claude", "codex", "opencode", "pi"):
    (global_source / "skills" / agent).mkdir(parents=True)
    destination = work / "global outputs" / agent / "skills"
    destinations.append(destination)
    blocks.append(
        "[[artifact]]\n"
        + f'agents = ["{agent}"]\nformat = "tree"\ncategory = "skills"\n'
        + f'source = "skills/{agent}"\ndestination = {json.dumps(str(destination))}\n'
    )
put(global_source / "artifacts.toml", "\n".join(blocks))
put(work / "config/loadout/config.toml", f"source = {json.dumps(str(global_source))}\n")
run("install packaged skill for four agents", ["skill", "install", "--yes"], work)
for destination in destinations:
    for relative in ("SKILL.md", "references/configuration.md", "references/onboarding.md"):
        assert (destination / "loadout" / relative).stat().st_size > 0
run("packaged skill status", ["skill", "status"], work)
run("packaged skill reinstall", ["skill", "install", "--yes"], work)
run("global check", ["check", "--global"], work)
sentinel = destinations[0] / "notes.txt"
sentinel.write_text("Keep this unrelated file.\n")
run("uninstall packaged skill", ["skill", "uninstall", "--yes"], work)
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
