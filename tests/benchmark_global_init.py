from __future__ import annotations

import argparse
import json
import shutil
import statistics
import tempfile
from pathlib import Path

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


def run_case(directory: Path, count: int) -> dict:
    home = directory / "home"
    environment = isolated_environment(home)
    root, logs = directory / "repo", directory / "logs"
    expected = fixture(root, count, environment)
    baseline = git(root, "rev-parse", "HEAD", env=environment).decode().strip()
    mapping = json.dumps(
        {
            "source": str(root / ".claude"),
            "destination": str(home / ".claude"),
            "agents": ["claude"],
            "kind": "harness",
        }
    )
    result = command(
        [
            "init",
            "--global",
            "--source",
            str(root),
            "--harness",
            "claude",
            "--mapping",
            mapping,
            "--yes",
            "--json",
        ],
        environment,
        logs,
        "init",
    )
    result.update(support_files=count, input_files=len(expected), baseline=baseline)
    write_json(directory / "result.json", result)
    journals = list((root / ".loadout-state/migrations").glob("*/journal.json"))
    if result["timeout"]:
        result["correctness"] = "not completed; censored failure"
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
        write_json(directory / "result.json", result)
        return result
    assert_exit(result)
    assert len(journals) == 1
    cursor = json.loads(journals[0].read_bytes())
    journal = (
        json.loads((journals[0].parent / f"payload-{cursor['payload']}.json").read_bytes())
        if cursor["version"] == 2
        else cursor
    )
    retirements = [op for op in journal["operations"] if op["phase"] == "retire"]
    assert len(retirements) == len(expected)
    for name, content in expected.items():
        assert (home / name).read_bytes() == content
        assert (home / name).stat().st_mode & 0o7777 == 0o644
        assert not (root / name).exists(), name
    indexed = git(root, "ls-files", "-z", env=environment).decode().split("\0")
    assert all(name not in indexed for name in expected)
    assert "loadout/loadout.toml" in indexed
    assert git(root, "rev-parse", "HEAD", env=environment).decode().strip() == baseline
    for label, arguments in (
        ("check", ["check", "--root", str(root / "loadout")]),
        ("staged", ["check", "--staged", "--root", str(root / "loadout")]),
    ):
        result[label] = command(arguments, environment, logs, label)
        assert_exit(result[label])
    result.update(
        operations=len(journal["operations"]),
        retirements=len(retirements),
        output_files=len(expected),
        output_bytes=sum(map(len, expected.values())),
        correctness="exact deployed bytes/modes; mapped originals retired and removed from index; global source staged; baseline retained; check and staged check",
    )
    write_json(directory / "result.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--evidence", type=Path)
    parser.add_argument("--counts", type=int, nargs="+", default=[25, 100, 1000])
    parser.add_argument("--repetitions", type=int, default=3)
    args = parser.parse_args()
    if args.repetitions < 1 or any(count < 1 for count in args.counts):
        parser.error("counts and repetitions must be positive")
    directory = (
        args.evidence or Path(tempfile.mkdtemp(prefix="loadout-global-benchmark-"))
    ).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    if any(directory.iterdir()):
        parser.error("evidence directory must be empty")
    summary = {
        "identity": code_identity(),
        "runner_sha256": digest(Path(__file__).read_bytes()),
        "warmup": None,
        "runs": [],
        "aggregation": {},
    }
    print(f"evidence: {directory}", flush=True)
    summary["warmup"] = run_case(directory / "warmup", 25)
    write_json(directory / "results.json", summary)
    for count in args.counts:
        for repetition in range(1, args.repetitions + 1):
            result = run_case(directory / f"{count}-{repetition}", count)
            summary["runs"].append(result)
            write_json(directory / "results.json", summary)
            print(
                f"{count}-{repetition}: {result['seconds']:.3f}s exit={result['exit']} timeout={result['timeout']}",
                flush=True,
            )
        completed = [
            r["seconds"]
            for r in summary["runs"]
            if r["support_files"] == count and not r["timeout"]
        ]
        summary["aggregation"][str(count)] = {
            "completed": len(completed),
            "median_seconds": statistics.median(completed) if completed else None,
            "range_seconds": [min(completed), max(completed)] if completed else None,
        }
        write_json(directory / "results.json", summary)
    if summary["warmup"]["timeout"] or any(run["timeout"] for run in summary["runs"]):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
