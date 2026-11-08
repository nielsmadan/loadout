"""List every place in this repository that knows about a harness, and whether a new one is there.

    python3 .claude/skills/add-harness/scripts/touchpoints.py NAME [--display "Display Name"]

Run from the repository root. Pure static reading: nothing is imported or executed.
"""

from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from pathlib import Path

FALLBACK = ("claude", "codex", "droid", "opencode", "pi")
DISPLAY = {
    "claude": r"claude",
    "codex": r"codex",
    "droid": r"droid|factory",
    "opencode": r"opencode",
    "pi": r"(?<![\w.-])pi(?![\w-])",
}
SKIP = ("tests/fixtures/expected/", "docs/plans/", "docs/tests/", "CHANGELOG.md", "uv.lock")
TEXT = (".py", ".md", ".toml", ".json", ".yml", ".yaml", ".ts", ".js", ".mjs")
SELF = ".claude/skills/add-harness/"
MIXED_REGISTRY = 2

Registry = tuple[str, int, str, set[str], bool]


def _assigned(node: ast.stmt) -> tuple[str, ast.expr] | None:
    if isinstance(node, ast.Assign) and len(node.targets) == 1:
        target, value = node.targets[0], node.value
    elif isinstance(node, ast.AnnAssign) and node.value is not None:
        target, value = node.target, node.value
    else:
        return None
    return (target.id, value) if isinstance(target, ast.Name) else None


def existing_harnesses(root: Path) -> tuple[str, ...]:
    path = root / "src/loadout/harnesses.py"
    if not path.is_file():
        return FALLBACK
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        assigned = _assigned(node)
        if assigned and assigned[0] == "GLOBAL_PRESET" and isinstance(assigned[1], ast.Dict):
            return tuple(k.value for k in assigned[1].keys if isinstance(k, ast.Constant))
    return FALLBACK


def _strings(node: ast.AST) -> list[str]:
    """String keys of a dict literal, or string items of a set, tuple, list or frozenset."""
    if isinstance(node, ast.Call) and getattr(node.func, "id", "") in {"frozenset", "set", "tuple"}:
        return [s for arg in node.args for s in _strings(arg)]
    if isinstance(node, ast.Dict):
        nested = [s for v in node.values if isinstance(v, ast.Dict) for s in _strings(v)]
        keys = [k.value for k in node.keys if isinstance(k, ast.Constant)]
        return [k for k in keys if isinstance(k, str)] + nested
    if isinstance(node, ast.Set | ast.Tuple | ast.List):
        items = [e.value for e in node.elts if isinstance(e, ast.Constant)]
        return [i for i in items if isinstance(i, str)]
    return []


def _owner(text: str, harnesses: tuple[str, ...]) -> str | None:
    return next((h for h in harnesses if text == h or text.startswith(f"{h}-")), None)


def registries(root: Path, harnesses: tuple[str, ...], new: str) -> list[Registry]:
    found = []
    for path in sorted((root / "src").rglob("*.py")):
        for node in ast.parse(path.read_text(encoding="utf-8")).body:
            assigned = _assigned(node)
            if assigned is None:
                continue
            items = _strings(assigned[1])
            named = {o for s in items if (o := _owner(s, harnesses)) is not None}
            pure = bool(items) and all(_owner(s, harnesses) for s in items)
            if len(named) >= MIXED_REGISTRY or (named and pure):
                has_new = any(s == new or s.startswith(f"{new}-") for s in items)
                where = str(path.relative_to(root))
                found.append((where, node.lineno, assigned[0], named, has_new))
    return found


def branches(root: Path, harnesses: tuple[str, ...]) -> dict[str, int]:
    names = "|".join(harnesses)
    pattern = re.compile(
        r"""(?:harness|agent|renderer)\s*(?:==|!=|in|not in)\s*[\(\{\[]?\s*"""
        rf"""["']({names})(?:-[a-z-]+)?["']"""
    )
    counts = {}
    for path in sorted((root / "src").rglob("*.py")):
        hits = len(pattern.findall(path.read_text(encoding="utf-8")))
        if hits:
            counts[str(path.relative_to(root))] = hits
    return counts


def documents(root: Path, harnesses: tuple[str, ...], new: str) -> list[tuple[str, int, bool]]:
    listed = subprocess.run(
        ["git", "ls-files"], cwd=root, capture_output=True, text=True, check=True
    )
    found = []
    for name in listed.stdout.split():
        if name.startswith((*SKIP, SELF)) or not name.endswith(TEXT):
            continue
        try:
            text = (root / name).read_text(encoding="utf-8").lower()
        except (UnicodeDecodeError, FileNotFoundError):
            continue
        named = sum(1 for h in harnesses if re.search(DISPLAY.get(h, re.escape(h)), text))
        if named >= len(harnesses) - 1:
            found.append((name, named, re.search(new, text) is not None))
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("name", help="the new harness's id, as it will appear in GLOBAL_PRESET")
    parser.add_argument("--display", help="its display name, for the file scan (default: the id)")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    root = args.root.resolve()
    harnesses = tuple(h for h in existing_harnesses(root) if h != args.name)
    new = "|".join(re.escape(n.lower()) for n in filter(None, (args.name, args.display)))

    print(f"existing harnesses: {', '.join(harnesses)}; new: {args.name}\n")
    found = registries(root, harnesses, args.name)
    print(
        f"REGISTRIES naming 2+ harnesses, or only harness names: {len(found)}, "
        f"without {args.name}: {sum(1 for r in found if not r[4])}"
    )
    print("  (a registry naming only some harnesses may rightly leave one out; decide each)")
    for path, line, name, named, has_new in found:
        print(f"  {'ok' if has_new else '--'} {path}:{line} {name} [{', '.join(sorted(named))}]")

    print("\nBRANCHES on a harness or renderer name, per file (read each one):")
    for path, hits in sorted(branches(root, harnesses).items(), key=lambda kv: -kv[1]):
        print(f"  {hits:3} {path}")

    files = documents(root, harnesses, new)
    print(
        f"\nFILES naming {len(harnesses) - 1}+ harnesses: {len(files)}, "
        f"without {args.name}: {sum(1 for f in files if not f[2])}"
    )
    for name, named, has_new in files:
        print(f"  {'ok' if has_new else '--'} {name} ({named})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
