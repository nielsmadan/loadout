from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import tempfile
import tomllib
import traceback
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from init_evidence import (
    assert_exit,
    code_identity,
    command,
    digest,
    git,
    identity,
    isolated_environment,
    write_json,
)
from loadout.destinations import resolve_destination

CASES = Path(__file__).parent / "fixtures/init_corpus.json"


def entries(root: Path) -> dict:
    result = {}
    for directory, dirs, files in os.walk(root):
        dirs[:] = [name for name in dirs if name != ".git"]
        for name in [*files, *(name for name in dirs if (Path(directory) / name).is_symlink())]:
            path = Path(directory) / name
            result[str(path.relative_to(root))] = (
                ["link", os.readlink(path)]
                if path.is_symlink()
                else ["file", digest(path.read_bytes()), stat.S_IMODE(path.stat().st_mode)]
            )
    return result


def arguments(case: dict, root: Path, home: Path) -> list[str]:
    args = [
        "init",
        f"--{case['scope']}",
        "--root" if case["scope"] == "project" else "--source",
        str(root),
    ]
    for agent in case["agents"]:
        args += ["--harness", agent]
    for mapping in case.get("mappings", []):
        source, target, agents, *kind = mapping
        args += [
            "--mapping",
            json.dumps(
                {
                    "source": str(root / source),
                    "destination": str((home if case["scope"] == "global" else root) / target),
                    "agents": agents.split(","),
                    "kind": kind[0] if kind else "harness",
                }
            ),
        ]
    return args


def parse(content: bytes, format_name: str):
    if format_name == "json":
        return json.loads(content)
    if format_name == "toml":
        return tomllib.loads(content.decode())
    return content


def ordered(value):
    if isinstance(value, dict):
        return [(key, ordered(item)) for key, item in value.items()]
    if isinstance(value, list):
        return [ordered(item) for item in value]
    return value


def expected_outputs(preview: dict) -> dict:
    generated = {entry["path"]: entry for entry in preview["generated_writes"]}
    result = {}
    output_free = {entry["path"] for entry in preview.get("required_owned_absences", [])}
    for candidate in preview["candidates"]:
        destination = candidate["destination"]
        if candidate["disposition"] != "migrated":
            continue
        path = Path(candidate["path"])
        assert destination and path.is_file(), ("missing inventoried original", path)
        format_name = candidate["format"]
        name = Path(destination).name
        agents = candidate["agents"]
        partial = format_name in {"json", "toml"} and (
            (name == ".claude.json" and "claude" in agents)
            or (name == "config.toml" and "codex" in agents)
            or (name == "settings.json" and "pi" in agents)
        )
        mode = (
            stat.S_IMODE(Path(destination).stat().st_mode)
            if partial and Path(destination).is_file()
            else stat.S_IMODE(path.stat().st_mode)
            if format_name == "copy"
            else 0o600
        )
        spec = {"path": destination, "format": format_name, "partial": partial, "mode": mode}
        content = parse(path.read_bytes(), format_name)
        if partial:
            if name == ".claude.json" and "claude" in agents:
                content = {"mcpServers": content["mcpServers"]} if "mcpServers" in content else {}
            else:
                runtime = {"projects", "trust"} if "codex" in agents else {"lastChangelogVersion"}
                content = {k: v for k, v in content.items() if k not in runtime}
            if not content:
                output_free.add(destination)
                continue
        if destination in result:
            assert ordered(result[destination]["content"]) == ordered(content), destination
        result[destination] = {**spec, "content": content}
    assert set(result) == set(generated) - output_free, (
        "output inventory differs from inventoried originals",
        set(result) ^ (set(generated) - output_free),
    )
    for path, spec in result.items():
        assert all(generated[path][key] == spec[key] for key in ("format", "partial", "mode")), path
    return result


def compare(expected: dict, relocate=Path) -> list[dict]:
    comparisons = []
    for destination, spec in expected.items():
        path = relocate(destination)
        actual = parse(path.read_bytes(), spec["format"])
        if spec["partial"]:
            actual = {key: value for key, value in actual.items() if key in spec["content"]}
        assert ordered(actual) == ordered(spec["content"]), destination
        assert stat.S_IMODE(path.stat().st_mode) == spec["mode"], destination
        comparisons.append(
            {
                "path": destination,
                "format": spec["format"],
                "mode": spec["mode"],
                "comparison": "complete ordered owned document"
                if isinstance(actual, dict)
                else "exact bytes",
                "output_sha256": digest(path.read_bytes()),
            }
        )
    return comparisons


def reconstruction(root: Path, home: Path, preview: dict, expected: dict, logs: Path) -> dict:
    fresh = root.parent / "reconstruction"
    fresh.mkdir()
    fresh_home = root.parent / "reconstruction-home"
    env = isolated_environment(fresh_home)
    paths = git(root, "ls-files", "-z", "--", "loadout", env=env).split(b"\0")
    for name in filter(None, paths):
        target = fresh / os.fsdecode(name)
        target.parent.mkdir(parents=True, exist_ok=True)
        entry = git(root, "ls-files", "--stage", "--", os.fsdecode(name), env=env)
        mode = entry.split()[0]
        target.write_bytes(git(root, "show", ":" + os.fsdecode(name), env=env))
        target.chmod(0o755 if mode == b"100755" else 0o644)
    source = fresh if preview["scope"] == "project" else fresh / "loadout"
    result = command(["sync", "--root", str(source)], env, logs, "reconstruction-sync")
    assert_exit(result)

    def relocate(path):
        target = Path(path)
        return (
            fresh / target.relative_to(root)
            if target.is_relative_to(root)
            else fresh_home / target.relative_to(home)
        )

    records = tomllib.loads((root / "loadout/artifacts.toml").read_text())["artifact"]
    with patch.dict(os.environ, {**env, "HOME": str(home)}, clear=True):
        private_routes = [
            root / record["output"]
            if preview["scope"] == "project"
            else resolve_destination(record["destination"], "corpus reconstruction")
            for record in records
            if record.get("optional")
        ]
    public = {
        path: spec
        for path, spec in expected.items()
        if not any(Path(path).is_relative_to(route) for route in private_routes)
        and not any(c["destination"] == path and c["private"] for c in preview["candidates"])
    }
    result["comparisons"] = compare(public, relocate)
    output_root = fresh if preview["scope"] == "project" else fresh_home
    actual_paths = {
        path
        for path in output_root.rglob("*")
        if path.is_file()
        and not (preview["scope"] == "project" and path.is_relative_to(fresh / "loadout"))
    }
    assert actual_paths == {relocate(path) for path in public}, (
        "public reconstruction output inventory differs"
    )
    for path in preview["required_absences"]:
        assert not relocate(path).exists(), path
    result["source"] = (
        "actual Git index blobs; Git 100644/100755 modes; no originals, receipts, registration or private files"
    )
    result["private_outputs_omitted"] = len(expected) - len(public)
    result["source_files"] = len(list(filter(None, paths)))
    private_files = [entry for entry in preview["source_writes"] if entry["private"]]
    if private_files:
        for entry in private_files:
            original = Path(entry["path"])
            target = fresh / original.relative_to(root)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(original.read_bytes())
            target.chmod(entry["mode"])
        result["with_private_source"] = command(
            ["sync", "--root", str(source)], env, logs, "reconstruction-private-sync"
        )
        assert_exit(result["with_private_source"])
        result["with_private_source"]["comparisons"] = compare(expected, relocate)
        actual_paths = {
            path
            for path in output_root.rglob("*")
            if path.is_file()
            and not (preview["scope"] == "project" and path.is_relative_to(fresh / "loadout"))
        }
        assert actual_paths == {relocate(path) for path in expected}, (
            "complete reconstruction output inventory differs"
        )
        result["with_private_source"]["supplied_private_files"] = len(private_files)
    return result


def lifecycle(root: Path, source: Path, preview: dict, env: dict, logs: Path) -> dict:
    artifacts = tomllib.loads((root / "loadout/artifacts.toml").read_text())["artifact"]
    tree = next(
        record
        for record in artifacts
        if record["format"] == "tree" and record["category"] == "skills"
    )
    target_source = root / "loadout" / tree["source"] / "corpus-lifecycle/SKILL.md"
    if preview["scope"] == "project":
        target = root / tree["output"] / "corpus-lifecycle/SKILL.md"
    else:
        with patch.dict(os.environ, env, clear=True):
            target = (
                resolve_destination(tree["destination"], "corpus lifecycle")
                / "corpus-lifecycle/SKILL.md"
            )
    target_source.parent.mkdir(parents=True)
    records = []
    for label, content in (("addition", b"# First entry\n"), ("edit", b"# Edited entry\n")):
        target_source.write_bytes(content)
        record = command(["sync", "--root", str(source)], env, logs, label)
        assert_exit(record)
        assert target.read_bytes() == content
        records.append(record)
    renamed = target_source.with_name("renamed.md")
    target_source.rename(renamed)
    record = command(["sync", "--root", str(source)], env, logs, "rename")
    assert_exit(record)
    assert target.with_name("renamed.md").read_bytes() == b"# Edited entry\n"
    assert not target.exists()
    records.append(record)
    renamed.unlink()
    record = command(["sync", "--root", str(source)], env, logs, "last-deletion")
    assert_exit(record)
    assert not target.with_name("renamed.md").exists()
    records.append(record)
    record = command(["check", "--root", str(source)], env, logs, "lifecycle-check")
    assert_exit(record)
    records.append(record)
    return {
        "category": "skills",
        "commands": records,
        "observed": [
            "first addition",
            "edit",
            "rename retires old output",
            "last deletion retires output",
        ],
    }


def adopt(case: dict, root: Path, preview: dict, env: dict, logs: Path) -> dict:
    home = Path(env["HOME"])
    expected = expected_outputs(preview)
    result = command([*arguments(case, root, home), "--yes", "--json"], env, logs, "init")
    assert_exit(result)
    result["comparisons"] = compare(expected)
    result["baseline"] = git(root, "rev-parse", "HEAD", env=env).decode().strip()
    changed_baseline = (
        git(root, "diff", "--name-only", case["revision"], "HEAD", env=env).decode().splitlines()
    )
    assert set(changed_baseline) <= set(preview["git"]["baseline_paths"])
    result["baseline_changed_paths"] = changed_baseline
    staged = git(root, "diff", "--cached", "--name-only", env=env).decode().splitlines()
    assert set(staged) <= set(preview["stage_paths"] + preview["unstage_paths"] + [".gitignore"])
    result["staged_paths"] = staged
    indexed = set(git(root, "ls-files", "-z", env=env).decode().split("\0"))
    for entry in preview["originals"]:
        path = Path(entry["path"])
        if entry["action"] == "retire":
            assert not path.exists() and not path.is_symlink(), path
        if entry["action"] == "generated" and path.is_relative_to(root):
            assert str(path.relative_to(root)) not in indexed
            assert git(root, "check-ignore", str(path), env=env).strip()
    source = root if case["scope"] == "project" else root / "loadout"
    for label, args in (
        ("check", ["check", "--root", str(source)]),
        ("sync", ["sync", "--root", str(source)]),
        ("check-again", ["check", "--root", str(source)]),
        ("init-again", [*arguments(case, root, home), "--yes", "--json"]),
        ("staged", ["check", "--staged", "--root", str(source)]),
    ):
        result[label] = command(args, env, logs, label)
        assert_exit(result[label])
    result["reconstruction"] = reconstruction(root, home, preview, expected, logs)
    if case["case"] == "sentry":
        allow = expected[str(root / ".claude/settings.json")]["content"]["permissions"]["allow"]
        assert len(allow) == 96
        result["sentry_allow"] = {
            "count": len(allow),
            "ordered_sha256": digest(json.dumps(allow).encode()),
            "full_list_equal": True,
        }
    if any(
        record["format"] == "tree" and record.get("category") == "skills"
        for record in tomllib.loads((root / "loadout/artifacts.toml").read_text())["artifact"]
    ):
        result["lifecycle"] = lifecycle(root, source, preview, env, logs)
    if case["case"] == "synthetic-global":
        result["example"] = {
            "before": {
                str(Path(path).relative_to(home)): spec["content"]
                if isinstance(spec["content"], dict)
                else spec["content"].decode()
                for path, spec in expected.items()
            },
            "source": {
                str(Path(entry["path"]).relative_to(root)): Path(entry["path"]).read_text()
                for entry in preview["source_writes"]
                if not entry["private"] and Path(entry["path"]).stat().st_size
            },
            "after": {
                str(Path(path).relative_to(home)): Path(path).read_text() for path in expected
            },
        }
    result["status"] = "migrated"
    return result


def clone_input(
    case: dict, retained: Path, directory: Path, env: dict, *, fetch_public: bool
) -> Path:
    root = directory / "repo"
    git(retained, "cat-file", "-e", case["revision"] + "^{commit}", env=env)
    git(directory, "clone", "--local", "--no-checkout", str(retained), str(root), env=env)
    try:
        git(root, "checkout", "--detach", case["revision"], env=env)
    except subprocess.CalledProcessError:
        try:
            git(root, "fetch", "--no-tags", str(retained), case["revision"], env=env)
            git(root, "checkout", "--detach", case["revision"], env=env)
        except subprocess.CalledProcessError as error:
            if not fetch_public:
                raise
            (directory / "local-object-failure.stderr").write_bytes(error.stderr)
            root = directory / "repo-public"
            remote_env = {k: v for k, v in env.items() if k != "GIT_NO_LAZY_FETCH"}
            subprocess.run(
                [
                    "gh",
                    "repo",
                    "clone",
                    f"https://github.com/{case['repository']}.git",
                    str(root),
                    "--",
                    "--filter=blob:none",
                    "--no-checkout",
                ],
                env=remote_env,
                capture_output=True,
                check=True,
            )
            git(root, "fetch", "origin", case["revision"], env=remote_env)
            git(root, "checkout", "--detach", case["revision"], env=remote_env)
    return root


def run_case(case: dict, retained: Path, directory: Path, *, fetch_public: bool = False) -> dict:
    home = directory / "home"
    env = isolated_environment(home)
    root = clone_input(case, retained, directory, env, fetch_public=fetch_public)
    logs = directory / "logs"
    identity(root, env)
    assert git(root, "rev-parse", "HEAD", env=env).decode().strip() == case["revision"]
    assert git(root, "status", "--porcelain", env=env) == b""
    tracked = git(root, "ls-files", "-z", env=env).split(b"\0")[:-1]
    tree = git(root, "ls-tree", "-rz", "--name-only", "HEAD", env=env).split(b"\0")[:-1]
    assert tracked == tree
    before = entries(root)
    before_home = entries(home)
    initial_index = (root / ".git/index").read_bytes()
    preview_result = command(
        [*arguments(case, root, home), "--dry-run", "--json"], env, logs, "preview"
    )
    preview = json.loads((logs / "preview.stdout").read_bytes())
    assert entries(root) == before, "preview changed worktree"
    assert (root / ".git/index").read_bytes() == initial_index, "preview changed index"
    result = {
        **case,
        "tracked_paths": len(tracked),
        "preview": preview_result,
        "candidate_dispositions": dict(Counter(c["disposition"] for c in preview["candidates"])),
        "issues": preview["issues"],
    }
    if not preview.get("complete"):
        assert_exit(preview_result, 2)
        result["apply_refusal"] = command(
            [*arguments(case, root, home), "--yes", "--json"], env, logs, "refusal"
        )
        assert_exit(result["apply_refusal"], 2)
        assert entries(root) == before
        assert (root / ".git/index").read_bytes() == initial_index
        assert git(root, "rev-parse", "HEAD", env=env).decode().strip() == case["revision"]
        assert entries(home) == before_home, "refusal changed fake home"
        result["status"] = "unresolved refusal; no filesystem/index/HEAD/home changes"
    else:
        result.update(adopt(case, root, preview, env, logs))
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-inputs", type=Path, required=True)
    parser.add_argument("--global-inputs", type=Path, required=True)
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--case", action="append")
    parser.add_argument("--fetch-missing-public", action="store_true")
    parser.add_argument("--opencode-input", type=Path)
    parser.add_argument("--synthetic", action="store_true")
    args = parser.parse_args()
    directory = (args.evidence or Path(tempfile.mkdtemp(prefix="loadout-init-corpus-"))).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    summary = {"identity": code_identity(), "cases": []}
    print(f"evidence: {directory}", flush=True)
    cases = json.loads(CASES.read_bytes())
    if args.synthetic:
        cases.append(synthetic_input(directory / "synthetic-input"))
    for case in cases:
        if args.case and case["case"] not in args.case:
            continue
        inputs = args.project_inputs if case["scope"] == "project" else args.global_inputs
        retained = inputs / case["case"]
        if case["case"] == "opencode" and args.opencode_input:
            retained = args.opencode_input
        if case["case"] == "synthetic-global":
            retained = directory / "synthetic-input/repo"
        try:
            result = run_case(
                case,
                retained,
                directory / case["case"],
                fetch_public=args.fetch_missing_public,
            )
        except (AssertionError, OSError, ValueError, subprocess.CalledProcessError) as error:
            result = {**case, "status": "failed", "error": str(error)}
            (directory / f"{case['case']}-failure.txt").write_text(traceback.format_exc())
            if isinstance(error, subprocess.CalledProcessError):
                result["stderr"] = error.stderr.decode(errors="replace")
        summary["cases"].append(result)
        write_json(directory / "results.json", summary)
        print(f"{case['case']}: {result['status']}", flush=True)
    if any(case["status"] == "failed" for case in summary["cases"]):
        raise SystemExit(1)


def synthetic_input(directory: Path) -> dict:
    env = isolated_environment(directory / "home")
    root = directory / "repo"
    root.mkdir()
    git(root, "init", "-q", env=env)
    identity(root, env)
    files = {
        ".claude/CLAUDE.md": "# Fixture guidance\nKeep literal bytes.\n",
        ".claude/settings.json": '{"model":null,"permissions":{"allow":["Bash(echo:*)","Read(src/**)"],"deny":[]},"hooks":{},"enabledPlugins":{},"env":{}}\n',
        ".claude/skills/probe/SKILL.md": "# Inert corpus skill\nRead run.sh as data only.\n",
        ".claude/skills/probe/run.sh": "#!/bin/sh\nprintf 'inert fixture\\n'\n",
        ".codex/AGENTS.md": "# Codex fixture\nPreserve this file.\n",
        ".codex/config.toml": 'model = "fixture"\n',
        ".config/opencode/AGENTS.md": "# OpenCode fixture\nPreserve this file.\n",
        ".config/opencode/opencode.json": '{"permission":{"bash":{"echo *":"allow","*":"ask"}},"model":"fixture"}\n',
        ".pi/agent/AGENTS.md": "# Pi fixture\nPreserve this file.\n",
        ".pi/agent/settings.json": '{"defaultModel":"fixture"}\n',
    }
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
        path.chmod(0o755 if name.endswith(".sh") else 0o644)
    git(root, "add", "--", *files, env=env)
    git(root, "commit", "-qm", "synthetic global input", env=env)
    return {
        "case": "synthetic-global",
        "scope": "global",
        "repository": "Loadout authored inert fixture",
        "revision": git(root, "rev-parse", "HEAD", env=env).decode().strip(),
        "agents": ["claude", "codex", "opencode", "pi"],
        "mappings": [
            [".claude", ".claude", "claude"],
            [".codex", ".codex", "codex"],
            [".config/opencode", ".config/opencode", "opencode"],
            [".pi/agent", ".pi/agent", "pi"],
        ],
        "mapping_basis": "Synthetic fixture explicitly assigns each source to the corresponding fake-home root",
    }


if __name__ == "__main__":
    main()
