from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

from benchmark_init import fixture
from init_evidence import (
    assert_exit,
    code_identity,
    command,
    digest,
    git,
    isolated_environment,
    write_json,
)

INTERRUPTED_INIT = """
import sys
import loadout
from loadout import migration_transaction
from loadout.errors import LoadoutError

def interrupt(journal):
    raise LoadoutError("benchmark staging interruption")

migration_transaction._finish = interrupt
raise SystemExit(loadout.main(sys.argv[1:]))
"""


def journal_storage(path: Path) -> dict:
    payloads = {p.name: p.read_bytes() for p in path.parent.glob("payload-*.json")}
    return {
        "payload_count": len(payloads),
        "payload_bytes": sum(map(len, payloads.values())),
        "payload_sha256": {name: digest(content) for name, content in sorted(payloads.items())},
        "cursor_bytes": path.stat().st_size if path.exists() else 0,
    }


def run(directory: Path) -> dict:
    environment = isolated_environment(directory / "home")
    root, logs = directory / "repo", directory / "logs"
    expected = fixture(root, 25, environment)
    index = (root / ".git/index").read_bytes()
    with patch("init_evidence.CLI", [sys.executable, "-I", "-c", INTERRUPTED_INIT]):
        initial = command(
            ["init", "--project", "--root", str(root), "--harness", "claude", "--yes", "--json"],
            environment,
            logs,
            "init",
        )
    result = {
        "identity": code_identity(),
        "runner_sha256": digest(Path(__file__).read_bytes()),
        "support_files": 25,
        "initial": initial,
    }
    assert_exit(result["initial"], expected=1)
    assert json.loads((logs / "init.stdout").read_bytes())["status"] == "interrupted"
    baseline = git(root, "rev-parse", "HEAD", env=environment)
    journal = next((root / ".loadout-state/migrations").glob("*/journal.json"))
    cursor = json.loads(journal.read_bytes())
    payload = json.loads((journal.parent / f"payload-{cursor['payload']}.json").read_bytes())
    result.update(operations=len(payload["operations"]), before=journal_storage(journal))
    result["recover"] = command(
        ["init", "--recover", str(journal), "--yes", "--json"], environment, logs, "recover"
    )
    assert_exit(result["recover"])
    recovery = json.loads((logs / "recover.stdout").read_bytes())
    assert recovery["status"] == "recovered" and recovery["conflicts"] == []
    assert recovery["journal"] is None
    for name, content in expected.items():
        assert (root / name).read_bytes() == content
        assert (root / name).stat().st_mode & 0o777 == 0o644
    assert not (root / "loadout/config.toml").exists()
    assert git(root, "rev-parse", "HEAD", env=environment) == baseline
    assert (root / ".git/index").read_bytes() == index
    assert git(root, "diff", "--name-only", env=environment) == b""
    result["after"] = journal_storage(journal)
    assert not journal.parent.exists()
    assert result["after"] == {
        "payload_count": 0,
        "payload_bytes": 0,
        "payload_sha256": {},
        "cursor_bytes": 0,
    }
    result["assertions"] = (
        "original bytes and 0644 modes restored; migration config removed; baseline HEAD and "
        "original index preserved; clean tracked worktree; recovery journal and payloads removed"
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    directory = (
        args.evidence or Path(tempfile.mkdtemp(prefix="loadout-recovery-benchmark-"))
    ).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    print(f"evidence: {directory}", flush=True)
    results = run(directory)
    write_json(directory / "results.json", results)
    print(
        json.dumps(
            {key: results[key] for key in ("operations", "before", "after", "assertions")}, indent=2
        )
    )
