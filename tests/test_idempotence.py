from __future__ import annotations

from pathlib import Path

import loadout


def _snapshot(*roots: Path) -> dict[str, bytes]:
    return {
        str(path): path.read_bytes()
        for root in roots
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _assert_stable(first: dict[str, bytes], second: dict[str, bytes], floor: int) -> None:
    # A snapshot that caught nothing compares equal to another empty one, so the
    # floor is what separates "stable" from "never looked". See AGENTS.md: a pass
    # proves nothing until you confirm the check fired.
    assert len(first) >= floor, f"vacuous: only {len(first)} files snapshotted"
    drifted = [name for name in first if first.get(name) != second.get(name)]
    assert not drifted, f"rendered different bytes on the second run: {drifted}"
    assert set(first) == set(second), f"file set changed: {set(first) ^ set(second)}"


def test_global_sync_renders_the_same_bytes_twice(root: Path, fake_home: Path, capsys) -> None:
    """Rendering is a pure function of the source, so a second sync must be a no-op.

    Byte-identity against `tests/fixtures/expected/` renders once, so a
    non-deterministic renderer shows up there as a flaky suite rather than a red
    one. Here it is deterministic: the drift is between two runs of the same
    source, and nothing else has to agree for the failure to appear.

    The empty target is the case that hides it. A `sync` onto files that already
    exist reuses whatever is on disk, so a value that is unstable only when it has
    no prior text — a separator, a blank line between composed blocks — renders
    once correctly and never again.
    """
    assert loadout.main(["sync", "--root", str(root), "--force"]) == 0
    first = _snapshot(fake_home, root)
    capsys.readouterr()

    code = loadout.main(["sync", "--root", str(root)])
    error = capsys.readouterr().err
    _assert_stable(first, _snapshot(fake_home, root), floor=20)

    # Unstable output reaches the user as a spurious "modified outside loadout",
    # accusing them of an edit the renderer made, so the guard's verdict is part
    # of what this pins.
    assert code == 0, f"second sync exited {code}:\n{error}"


def test_project_sync_renders_the_same_bytes_twice(project: Path, capsys) -> None:
    """Project scope composes contributors per path the way global scope does."""
    assert loadout.main(["sync", "--root", str(project), "--force"]) == 0
    first = _snapshot(project)
    capsys.readouterr()

    code = loadout.main(["sync", "--root", str(project)])
    error = capsys.readouterr().err
    _assert_stable(first, _snapshot(project), floor=5)
    assert code == 0, f"second sync exited {code}:\n{error}"
