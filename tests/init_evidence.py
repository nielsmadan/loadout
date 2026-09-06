from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
import platform
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

CHECKOUT = Path(__file__).resolve().parents[1]
CLI = [sys.executable, "-I", "-m", "loadout"]
HARNESS_VARIABLES = (
    "CLAUDE_CONFIG_DIR",
    "CODEX_HOME",
    "PI_CODING_AGENT_DIR",
    "XDG_CONFIG_HOME",
    "OPENCODE_CONFIG",
    "OPENCODE_CONFIG_DIR",
    "LOADOUT_TEST_HARNESS_C",
)


def isolated_environment(home: Path) -> dict[str, str]:
    home.mkdir(parents=True)
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in HARNESS_VARIABLES and not key.startswith("GIT_")
    }
    environment.update(
        HOME=str(home), GIT_CONFIG_NOSYSTEM="1", GIT_TERMINAL_PROMPT="0", GIT_NO_LAZY_FETCH="1"
    )
    return environment


def git(root: Path, *args: str, env: dict[str, str]) -> bytes:
    return subprocess.run(
        ["git", "-C", str(root), *args], env=env, check=True, capture_output=True
    ).stdout


def identity(root: Path, env: dict[str, str]) -> None:
    git(root, "config", "user.name", "Loadout corpus fixture", env=env)
    git(root, "config", "user.email", "corpus@example.invalid", env=env)


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def write_json(path: Path, document: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n")


def code_identity() -> dict:
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0"}
    files = sorted((CHECKOUT / "src/loadout").rglob("*.py"))
    return {
        "date": datetime.now(UTC).isoformat(),
        "head": git(CHECKOUT, "rev-parse", "HEAD", env=env).decode().strip(),
        "source_sha256": {
            str(path.relative_to(CHECKOUT)): digest(path.read_bytes()) for path in files
        },
        "runner_sha256": {
            name: digest((CHECKOUT / name).read_bytes())
            for name in (
                "tests/init_evidence.py",
                "tests/run_init_corpus.py",
                "tests/benchmark_init.py",
                "tests/fixtures/init_corpus.json",
            )
        },
        "tracked_patch_sha256": digest(git(CHECKOUT, "diff", "HEAD", "--", "src", env=env)),
        "python": sys.version,
        "platform": platform.platform(),
        "hardware": subprocess.run(
            ["sysctl", "-n", "hw.model", "hw.memsize", "machdep.cpu.brand_string"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip(),
        "git": subprocess.run(
            ["git", "--version"], capture_output=True, text=True, check=True
        ).stdout.strip(),
        "loadout": importlib.metadata.version("loadout"),
        "cli": CLI,
        "cleared_environment": [*HARNESS_VARIABLES, "GIT_*"],
        "concurrency": 1,
    }


def command(arguments: list[str], env: dict[str, str], logs: Path, label: str, timeout=180) -> dict:
    logs.mkdir(parents=True, exist_ok=True)
    start = time.perf_counter()
    try:
        result = subprocess.run(
            [*CLI, *arguments],
            cwd=CHECKOUT,
            env=env,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        record = {"exit": result.returncode, "timeout": False}
        stdout, stderr = result.stdout, result.stderr
    except subprocess.TimeoutExpired as error:
        record = {"exit": None, "timeout": True}
        stdout, stderr = error.stdout or b"", error.stderr or b""
    record.update(seconds=time.perf_counter() - start, argv=[*CLI, *arguments])
    (logs / f"{label}.stdout").write_bytes(stdout)
    (logs / f"{label}.stderr").write_bytes(stderr)
    write_json(logs / f"{label}.json", record)
    return record


def assert_exit(record: dict, expected=0) -> None:
    assert record["exit"] == expected, record


def publish(source: Path, destination: Path) -> None:
    data = json.loads(source.read_bytes())
    content = json.dumps(data, indent=2)
    content = content.replace(str(source.parent), "<evidence>").replace(str(CHECKOUT), "<checkout>")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(content + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    options = parser.parse_args()
    publish(options.source, options.destination)
