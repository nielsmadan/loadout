from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import subprocess
import tempfile
import traceback
from collections import Counter
from pathlib import Path

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

CASES = Path(__file__).parent / "fixtures/init_corpus.json"
# The pinned clones, one per case or per case's `input`; read as data, never run.
DEFAULT_INPUTS = Path.home() / "wrksp/loadout/corpus-inputs"
XDG_DIRECTORIES = (
    ("XDG_CONFIG_HOME", ".config"),
    ("XDG_DATA_HOME", ".local/share"),
    ("XDG_STATE_HOME", ".local/state"),
    ("XDG_CACHE_HOME", ".cache"),
)


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


def arguments(case: dict, root: Path) -> list[str]:
    args = ["init", case["scope"], *(["--root"] if case["scope"] == "project" else []), str(root)]
    for agent in case["agents"]:
        args += ["--harness", agent]
    return args


def deploy(case: dict, root: Path, home: Path) -> None:
    """Install a dotfiles checkout the way its README does, so init reads a live deployment."""
    for source, target in case.get("deploy", []):
        shutil.copytree(
            root / source,
            home / target,
            symlinks=True,
            ignore=shutil.ignore_patterns(".git"),
            dirs_exist_ok=True,
        )


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


def corpus_environment(home: Path) -> dict[str, str]:
    """`isolated_environment` with every XDG base directory inside the fake HOME and an
    empty global Git config, so neither the machine's nor the user's settings reach a run."""
    env = isolated_environment(home)
    for name, relative in XDG_DIRECTORIES:
        env[name] = str(home / relative)
    empty = home.parent / f"{home.name}.gitconfig"
    empty.write_text("")
    env["GIT_CONFIG_GLOBAL"] = str(empty)
    return env


def scope_arguments(case: dict, root: Path) -> list[str]:
    return ["--root", str(root)] if case["scope"] == "project" else ["--global"]


def run_case(case: dict, retained: Path, directory: Path, *, fetch_public: bool = False) -> dict:
    """Preview, apply, then check, sync and check again; a refusal must change nothing."""
    home = directory / "home"
    env = corpus_environment(home)
    root = clone_input(case, retained, directory, env, fetch_public=fetch_public)
    deploy(case, root, home)
    logs = directory / "logs"
    identity(root, env)
    assert git(root, "rev-parse", "HEAD", env=env).decode().strip() == case["revision"]
    assert git(root, "status", "--porcelain", env=env) == b""
    tracked = git(root, "ls-files", "-z", env=env).split(b"\0")[:-1]
    tree = git(root, "ls-tree", "-rz", "--name-only", "HEAD", env=env).split(b"\0")[:-1]
    assert tracked == tree
    before, before_home = entries(root), entries(home)
    initial_index = (root / ".git/index").read_bytes()
    preview_result = command([*arguments(case, root), "--dry-run", "--json"], env, logs, "preview")
    preview = json.loads((logs / "preview.stdout").read_bytes())
    assert entries(root) == before, "preview changed worktree"
    assert (root / ".git/index").read_bytes() == initial_index, "preview changed index"
    assert entries(home) == before_home, "preview changed fake home"
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
            [*arguments(case, root), "--yes", "--json"], env, logs, "refusal"
        )
        assert_exit(result["apply_refusal"], 2)
        assert entries(root) == before
        assert (root / ".git/index").read_bytes() == initial_index
        assert git(root, "rev-parse", "HEAD", env=env).decode().strip() == case["revision"]
        assert entries(home) == before_home, "refusal changed fake home"
        codes = sorted({issue["code"] for issue in preview["issues"]})
        result["status"] = f"refused ({', '.join(codes)}); nothing changed"
        return result
    assert_exit(preview_result)
    result["init"] = command([*arguments(case, root), "--yes", "--json"], env, logs, "init")
    assert_exit(result["init"])
    head = git(root, "rev-parse", "HEAD", env=env).decode().strip()
    assert head == case["revision"], "init must not commit"
    staged = git(root, "diff", "--cached", "--name-only", env=env).decode().splitlines()
    assert set(staged) <= set(preview["untrack_paths"]), "init stages only the untracking"
    for label, verb in (("check", "check"), ("sync", "sync"), ("check-again", "check")):
        result[label] = command([verb, *scope_arguments(case, root)], env, logs, label)
        assert_exit(result[label])
    result["status"] = "converted"
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--inputs", type=Path, default=DEFAULT_INPUTS)
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
        retained = args.inputs / case.get("input", case["case"])
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
    converted = sum(case["status"] == "converted" for case in summary["cases"])
    print(f"converted {converted} of {len(summary['cases'])}", flush=True)
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
        "deploy": [
            [".claude", ".claude"],
            [".codex", ".codex"],
            [".config/opencode", ".config/opencode"],
            [".pi/agent", ".pi/agent"],
        ],
        "deploy_basis": "Synthetic fixture installs each tree at the corresponding fake-home root",
    }


if __name__ == "__main__":
    main()
