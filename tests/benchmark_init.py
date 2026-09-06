from __future__ import annotations

import argparse
import json
import shutil
import statistics
import tempfile
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

PAYLOAD = bytes(range(256)) * 4
SKILL = (
    b"---\nname: corpus\ndescription: Inert migration benchmark fixture.\n---\n\nRead references.\n"
)


def fixture(root: Path, count: int, environment: dict[str, str]) -> dict[str, bytes]:
    root.mkdir()
    git(root, "init", "-q", env=environment)
    identity(root, environment)
    expected = {".claude/skills/corpus/SKILL.md": SKILL}
    expected.update(
        {f".claude/skills/corpus/references/{index:04}.bin": PAYLOAD for index in range(count)}
    )
    for name, content in expected.items():
        target = root / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        target.chmod(0o644)
    git(root, "add", "--", ".claude", env=environment)
    git(root, "commit", "-qm", "synthetic benchmark input", env=environment)
    return expected


def run_case(directory: Path, count: int) -> dict:
    environment = isolated_environment(directory / "home")
    root = directory / "repo"
    expected = fixture(root, count, environment)
    logs = directory / "logs"
    arguments = ["init", "--project", "--root", str(root), "--harness", "claude", "--yes", "--json"]
    result = command(arguments, environment, logs, "init")
    result.update(
        support_files=count, input_files=len(expected), input_bytes=sum(map(len, expected.values()))
    )
    journals = list((root / ".loadout-state/migrations").glob("*/journal.json"))
    if journals:
        journal = json.loads(journals[0].read_bytes())
        if journal["version"] == 2:
            journal = json.loads(
                (journals[0].parent / f"payload-{journal['payload']}.json").read_bytes()
            )
        result.update(
            operations=len(journal["operations"]), journal_bytes=journals[0].stat().st_size
        )
        result["payload_bytes"] = sum(
            path.stat().st_size for path in journals[0].parent.glob("payload-*.json")
        )
    if result["timeout"]:
        result["correctness"] = "not completed; censored failure"
        result["journal"] = str(journals[0]) if journals else None
        if journals:
            preserved = directory / "interrupted-journal"
            shutil.copytree(journals[0].parent, preserved)
            result["preserved_journal"] = str(preserved / "journal.json")
            result["recovery"] = command(
                ["init", "--recover", str(journals[0]), "--yes", "--json"],
                environment,
                logs,
                "recovery",
            )
        return result
    assert_exit(result)
    for name, content in expected.items():
        assert (root / name).read_bytes() == content
        assert (root / name).stat().st_mode & 0o777 == 0o644
    indexed = git(root, "ls-files", "-z", env=environment).decode().split("\0")
    assert all(name not in indexed for name in expected)
    assert "loadout/config.toml" in indexed
    ignored = git(root, "check-ignore", *expected, env=environment).decode().splitlines()
    assert set(expected) <= set(ignored)
    for label, args in (
        ("check", ["check", "--root", str(root)]),
        ("staged", ["check", "--staged", "--root", str(root)]),
    ):
        result[label] = command(args, environment, logs, label)
        assert_exit(result[label])
    result.update(
        correctness="complete bytes, filesystem mode 0644, generated index removals/ignores, source staged, check and staged check",
        output_files=len(expected),
        output_bytes=sum(map(len, expected.values())),
        payload_sha256=digest(PAYLOAD),
    )
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    directory = args.evidence or Path(tempfile.mkdtemp(prefix="loadout-init-benchmark-"))
    directory.mkdir(parents=True, exist_ok=True)
    summary = {"identity": code_identity(), "warmup": None, "runs": [], "aggregation": {}}
    print(f"evidence: {directory}", flush=True)
    summary["warmup"] = run_case(directory / "warmup", 25)
    write_json(directory / "results.json", summary)
    for count in (25, 100, 1000):
        for repetition in range(1, 4):
            result = run_case(directory / f"{count}-{repetition}", count)
            summary["runs"].append(result)
            write_json(directory / "results.json", summary)
            print(
                f"{count}-{repetition}: {result['seconds']:.3f}s exit={result['exit']} timeout={result['timeout']}",
                flush=True,
            )
        durations = [
            r["seconds"]
            for r in summary["runs"]
            if r["support_files"] == count and not r["timeout"]
        ]
        summary["aggregation"][str(count)] = {
            "completed": len(durations),
            "median_seconds": statistics.median(durations) if durations else None,
            "range_seconds": [min(durations), max(durations)] if durations else None,
        }
        write_json(directory / "results.json", summary)
    if summary["warmup"]["timeout"] or any(run["timeout"] for run in summary["runs"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
