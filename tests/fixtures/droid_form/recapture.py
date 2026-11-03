"""Recheck captured-droid-1.2.0.json against another Droid version, black-box.

    python3 recapture.py write   <droids folder>   # the 188 before-files, into an empty folder
    (in Droid: /droids, create one droid in that same location)
    python3 recapture.py compare <droids folder>   # each file against what Droid 1.2.0 left

Run it where that Droid runs, never against a droids folder in real use.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

FIXTURE = json.loads(
    (Path(__file__).parent / "captured-droid-1.2.0.json").read_text(encoding="utf-8")
)["files"]


def write(folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name, entry in FIXTURE.items():
        (folder / name).write_bytes(entry["before"].encode("utf-8"))
    print(f"wrote {len(FIXTURE)} droids to {folder}")


def compare(folder: Path) -> int:
    differ = [
        name
        for name, entry in FIXTURE.items()
        if not (folder / name).is_file()
        or (folder / name).read_bytes() != entry["after"].encode("utf-8")
    ]
    print(f"{len(FIXTURE) - len(differ)} of {len(FIXTURE)} as Droid 1.2.0 left them")
    for name in differ:
        print(f"  differs: {name} ({FIXTURE[name]['case']})")
    return 1 if differ else 0


if __name__ == "__main__":
    if sys.argv[1:2] == ["write"] and len(sys.argv) == 3:
        write(Path(sys.argv[2]))
    elif sys.argv[1:2] == ["compare"] and len(sys.argv) == 3:
        sys.exit(compare(Path(sys.argv[2])))
    else:
        sys.exit(__doc__)
