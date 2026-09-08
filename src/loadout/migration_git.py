from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import tempfile
from collections.abc import Callable
from concurrent.futures import Executor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .artifacts import FrozenFile
from .deployment import atomic_install
from .discovery import RUNTIME_FILES
from .errors import LoadoutError
from .git_privacy import ignored_paths
from .migration_models import MigrationPlan
from .migration_paths import entry_path


def git(
    root: Path,
    *arguments: str,
    index: Path | None = None,
    data: bytes | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[bytes]:
    environment = dict(os.environ)
    for key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        environment.pop(key, None)
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    if index is not None:
        environment["GIT_INDEX_FILE"] = str(index)
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        input=data,
        capture_output=True,
        env=environment,
        check=False,
    )
    if check and result.returncode:
        raise LoadoutError(
            f"Git {' '.join(arguments[:2])} failed in {root}: "
            + result.stderr.decode(errors="replace").strip()
        )
    return result


def literal(path: str) -> str:
    if (
        not path
        or path == "."
        or "\0" in path
        or Path(path).is_absolute()
        or ".." in Path(path).parts
    ):
        raise LoadoutError(f"invalid root-relative Git path: {path!r}")
    return ":(top,literal)" + path


def relative(root: Path, path: Path) -> str | None:
    path = _root_path(root, path)
    if not path.is_relative_to(root):
        return None
    value = path.relative_to(root).as_posix()
    literal(value)
    return value


def _root_path(root: Path, path: Path) -> Path:
    if not path.is_relative_to(root):
        for ancestor in reversed(path.parents):
            resolved = ancestor.resolve()
            if resolved.is_relative_to(root):
                return resolved / path.relative_to(ancestor)
    return path


def index_bytes(path: Path) -> bytes | None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise LoadoutError(f"Git index must be a regular file: {path}")
    return path.read_bytes() if path.exists() else None


def identity(root: Path) -> tuple[str | None, str | None]:
    head = git(root, "rev-parse", "--verify", "HEAD", check=False)
    symbolic = git(root, "symbolic-ref", "-q", "HEAD", check=False)
    return (
        head.stdout.decode().strip() if head.returncode == 0 else None,
        symbolic.stdout.decode().strip() if symbolic.returncode == 0 else None,
    )


def _entries(root: Path, *arguments: str) -> tuple[str, ...]:
    return tuple(os.fsdecode(p) for p in git(root, *arguments, "-z").stdout.split(b"\0") if p)


def excluded(path: Path, private: tuple[Path, ...]) -> bool:
    return any(
        path.is_relative_to(p) or path.resolve().is_relative_to(p.resolve()) for p in private
    )


def _security(path: str) -> bool:
    parts = Path(path).parts
    return any(
        part
        in RUNTIME_FILES
        | {
            ".git",
            ".loadout-state",
            ".ssh",
            "secrets",
            ".gnupg",
            ".aws",
            ".azure",
            ".kube",
            "node_modules",
            "__pycache__",
        }
        or part.startswith(".env.")
        for part in parts
    )


def _entry(root: Path, path: Path) -> Path:
    path = _root_path(root, path)
    for parent in reversed((path, *path.parents)):
        if parent.is_relative_to(root) and parent.is_symlink():
            return parent
    return path


def _ignored(root: Path, paths: tuple[str, ...]) -> tuple[str, ...]:
    if not paths:
        return ()
    result = git(
        root,
        "check-ignore",
        "--no-index",
        "-z",
        "--stdin",
        data=b"\0".join(os.fsencode(p) for p in paths) + b"\0",
        check=False,
    )
    if result.returncode not in {0, 1}:
        raise LoadoutError(f"could not inspect Git privacy in {root}")
    return tuple(os.fsdecode(p) for p in result.stdout.split(b"\0") if p)


@dataclass(frozen=True)
class GitPreparation:
    root: Path
    existing: bool
    head: str | None
    symbolic: str | None
    index_path: Path | None
    original_index: bytes | None
    baseline: tuple[str, ...]
    privacy_paths: tuple[str, ...]
    ignored: tuple[str, ...]
    private: tuple[Path, ...]
    authored_privacy: tuple[tuple[Path, bool], ...] = ()
    policy_paths: tuple[Path, ...] = ()
    privacy_policy: tuple[tuple[str, str | None], ...] = ()

    def preview(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "initialize": not self.existing,
            "head": self.head,
            "symbolic_ref": self.symbolic,
            "baseline_paths": list(self.baseline),
        }


def _privacy_paths(plan: MigrationPlan, root: Path) -> tuple[str, ...]:
    return tuple(
        sorted(
            {
                value
                for candidate in plan.inventory.candidates
                for path in (candidate.path, candidate.canonical)
                if path is not None and (value := relative(root, _entry(root, path))) is not None
            }
        )
    )


def _baseline(plan: MigrationPlan, root: Path, *, existing: bool) -> tuple[str, ...]:
    if existing:
        paths = {
            value
            for path in plan.checkpoint_paths
            if (value := relative(root, _entry(root, path))) is not None
        }
    else:
        paths = set(_entries(root, "ls-files", "--cached", "--others", "--exclude-standard"))
    private = (*plan.private_paths, plan.inventory.root / ".loadout-state")
    return tuple(
        sorted(path for path in paths if not excluded(root / path, private) and not _security(path))
    )


def _partial_adopted(root: Path, paths: tuple[str, ...]) -> None:
    staged = set(_entries(root, "diff", "--cached", "--name-only"))
    unstaged = set(_entries(root, "diff", "--name-only"))
    ambiguous = staged & unstaged & set(paths)
    if ambiguous:
        raise LoadoutError(
            "partially staged adopted paths require resolution: " + ", ".join(sorted(ambiguous))
        )


def prepare_git(plan: MigrationPlan) -> GitPreparation:
    selected = plan.inventory.root.resolve()
    if privacy_policy(selected, plan.starter_dependencies) != plan.starter_privacy_policy:
        raise LoadoutError("Git starter privacy policy changed after migration preview")
    privacy_checks = _authored_privacy(plan)
    probe = git(selected, "rev-parse", "--show-toplevel", check=False)
    existing = probe.returncode == 0
    if not existing and b"not a git repository" not in probe.stderr:
        raise LoadoutError(f"cannot inspect Git repository: {selected}")
    root = Path(os.fsdecode(probe.stdout).strip()).resolve() if existing else selected
    if not existing and root in {Path(root.anchor), Path.home().resolve()}:
        raise LoadoutError(
            "new migration repositories require a dedicated directory outside HOME and the filesystem root"
        )
    if not existing:
        with tempfile.TemporaryDirectory(prefix="loadout-git-preview-") as name:
            scratch = Path(name)
            git(scratch, "init", "--bare", "-q")
            result = subprocess.run(
                [
                    "git",
                    "--git-dir",
                    str(scratch),
                    "--work-tree",
                    str(root),
                    "ls-files",
                    "--others",
                    "--exclude-standard",
                    "-z",
                ],
                capture_output=True,
                check=True,
            )
            candidates = tuple(os.fsdecode(p) for p in result.stdout.split(b"\0") if p)
        private = (*plan.private_paths, selected / ".loadout-state")
        baseline = tuple(
            sorted(p for p in candidates if not excluded(root / p, private) and not _security(p))
        )
        paths = _policy_paths(plan, root, baseline)
        return GitPreparation(
            root,
            False,
            None,
            None,
            None,
            None,
            baseline,
            (),
            (),
            tuple(private),
            privacy_checks,
            paths,
            privacy_policy(root, paths),
        )
    if git(root, "ls-files", "--unmerged", "-z").stdout:
        raise LoadoutError("resolve the unmerged Git index before migration")
    head, symbolic = identity(root)
    index_path = Path(
        os.fsdecode(
            git(root, "rev-parse", "--path-format=absolute", "--git-path", "index").stdout
        ).strip()
    )
    original = index_bytes(index_path)
    baseline = _baseline(plan, root, existing=True)
    _partial_adopted(root, baseline)
    privacy = _privacy_paths(plan, root)
    paths = _policy_paths(plan, root, baseline)
    return GitPreparation(
        root,
        True,
        head,
        symbolic,
        index_path,
        original,
        baseline,
        privacy,
        _ignored(root, privacy),
        plan.private_paths,
        privacy_checks,
        paths,
        privacy_policy(root, paths),
    )


def _policy_paths(plan: MigrationPlan, root: Path, baseline: tuple[str, ...]) -> tuple[Path, ...]:
    return tuple(
        sorted(
            {
                entry_path(_entry(root, path))
                for path in (
                    *(root / path for path in baseline),
                    *(write.path for write in plan.source_writes),
                    *plan.starter_dependencies,
                    *(
                        path
                        for candidate in plan.inventory.candidates
                        for path in (candidate.path, candidate.canonical)
                        if path is not None
                    ),
                )
            }
        )
    )


def privacy_fingerprint(path: Path) -> str | None:
    if path.is_symlink():
        return "symlink:" + hashlib.sha256(os.fsencode(os.readlink(path))).hexdigest()
    if path.is_file():
        return hashlib.sha256(path.read_bytes()).hexdigest()
    return "directory" if path.exists() else None


def _privacy_repository(parent: Path, cache: dict[Path, Path | None]) -> Path | None:
    parent = parent.resolve()
    directory = parent
    traversed = []
    while directory not in cache:
        traversed.append(directory)
        marker = directory / ".git"
        if marker.exists() or marker.is_symlink() or directory == directory.parent:
            probe = git(parent, "rev-parse", "--show-toplevel", check=False)
            if probe.returncode and b"not a git repository" not in probe.stderr:
                raise LoadoutError(f"could not inspect Git privacy repository: {parent}")
            repository = (
                Path(os.fsdecode(probe.stdout).strip()).resolve() if probe.returncode == 0 else None
            )
            break
        directory = directory.parent
    else:
        repository = cache[directory]
    cache.update((path, repository) for path in traversed)
    return repository


def _privacy_queries(
    root: Path, *, repository: bool, executor: Executor | None
) -> tuple[subprocess.CompletedProcess[bytes], ...]:
    queries = [
        (("config", "--path", "--get", "core.excludesFile"), False),
        (("config", "--bool", "--get", "core.ignoreCase"), False),
    ]
    if repository:
        queries.append(
            (("rev-parse", "--path-format=absolute", "--git-path", "info/exclude"), True)
        )
    if executor is None:
        return tuple(git(root, *args, check=check) for args, check in queries)
    pending = [executor.submit(git, root, *args, check=check) for args, check in queries]
    wait(pending)
    return tuple(result.result() for result in pending)


def privacy_policy(
    root: Path, paths: tuple[Path, ...], *, executor: Executor | None = None
) -> tuple[tuple[str, str | None], ...]:
    values: dict[str, str | None] = {}
    repositories: dict[Path, Path | None] = {}
    logical_repositories: dict[Path, Path | None] = {}
    inspected: set[tuple[Path, Path]] = set()
    files: set[Path] = set()
    configured: set[Path] = set()
    for path in paths:
        logical_parent = path.parent
        if logical_parent not in logical_repositories:
            parent = logical_parent
            while not parent.is_dir():
                parent = parent.parent
            logical_repositories[logical_parent] = _privacy_repository(parent, repositories)
        repository = logical_repositories[logical_parent]
        selected = repository or (root if path.is_relative_to(root) else None)
        values["repository:" + str(logical_parent)] = str(selected) if selected else None
        if selected is None or (logical_parent, selected) in inspected:
            continue
        inspected.add((logical_parent, selected))
        for directory in path.parents:
            if not directory.is_relative_to(selected):
                break
            files.add(directory / ".gitignore")
        if selected in configured:
            continue
        configured.add(selected)
        queries = _privacy_queries(selected, repository=repository is not None, executor=executor)
        excludes, ignore_case = queries[:2]
        if excludes.returncode not in {0, 1} or ignore_case.returncode not in {0, 1}:
            raise LoadoutError(f"could not inspect Git privacy configuration: {selected}")
        exclude_path = (
            Path(os.fsdecode(excludes.stdout).strip())
            if excludes.returncode == 0
            else Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "git/ignore"
        )
        files.add((selected / exclude_path).absolute())
        files.add((selected / exclude_path).resolve())
        values["excludes:" + str(selected)] = str(exclude_path)
        values["ignorecase:" + str(selected)] = ignore_case.stdout.decode().strip() or None
        files.add(
            Path(os.fsdecode(queries[2].stdout).strip())
            if repository is not None
            else selected / ".git/info/exclude"
        )
    values.update(("file:" + str(path), privacy_fingerprint(path)) for path in files)
    return tuple(sorted(values.items()))


def _authored_privacy(plan: MigrationPlan) -> tuple[tuple[Path, bool], ...]:
    result = []
    ignored = ignored_paths(
        (
            *(
                c.canonical or c.path
                for c in plan.inventory.candidates
                if c.disposition == "migrated"
            ),
            *plan.starter_dependencies,
        )
    )
    for candidate in plan.inventory.candidates:
        if candidate.disposition != "migrated":
            continue
        path = candidate.canonical or candidate.path
        private = path in ignored
        if private and not candidate.private:
            raise LoadoutError(
                f"Git-derived source privacy changed since discovery: {candidate.path}"
            )
        result.append((path, private))
    for path in plan.starter_dependencies:
        if path in ignored:
            raise LoadoutError(
                f"Git-derived starter privacy changed after migration preview: {path}"
            )
        result.append((path, False))
    return tuple(result)


def ignored_original(path: Path) -> bool:
    return path in ignored_paths((path,))


def verify_authored_privacy(prepared: GitPreparation) -> None:
    ignored = ignored_paths(path for path, _ in prepared.authored_privacy)
    if any((path in ignored) != private for path, private in prepared.authored_privacy):
        raise LoadoutError("Git-derived source privacy changed after migration preview")


def verify_git(prepared: GitPreparation) -> None:
    verify_authored_privacy(prepared)
    if prepared.existing:
        if identity(prepared.root) != (prepared.head, prepared.symbolic):
            raise LoadoutError("Git HEAD or symbolic ref changed after migration preview")
        assert prepared.index_path is not None
        if index_bytes(prepared.index_path) != prepared.original_index:
            raise LoadoutError("Git index changed after migration preview")
        if _ignored(prepared.root, prepared.privacy_paths) != prepared.ignored:
            raise LoadoutError("Git-derived source privacy changed after migration preview")
    elif git(prepared.root, "rev-parse", "--show-toplevel", check=False).returncode == 0:
        raise LoadoutError("a Git repository appeared after migration preview")
    if privacy_policy(prepared.root, prepared.policy_paths) != prepared.privacy_policy:
        raise LoadoutError("Git privacy policy changed after migration preview")


def replace_index(path: Path, before: bytes | None, after: bytes | None) -> None:
    lock = path.with_name(path.name + ".lock")
    try:
        descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as error:
        raise LoadoutError(f"Git index is locked: {lock}") from error
    try:
        with os.fdopen(descriptor, "wb") as handle:
            if index_bytes(path) != before:
                raise LoadoutError("Git index changed before guarded replacement")
            handle.write(after or b"")
            handle.flush()
            os.fchmod(
                handle.fileno(), stat.S_IMODE(path.stat().st_mode) if path.exists() else 0o644
            )
            os.fsync(handle.fileno())
        if after is None:
            path.unlink(missing_ok=True)
        else:
            os.replace(lock, path)
    finally:
        lock.unlink(missing_ok=True)


def _set_paths(root: Path, index: Path, paths: tuple[str, ...], revision: str) -> None:
    for path in paths:
        git(root, "update-index", "--force-remove", "--", path, index=index)
        tree = git(root, "ls-tree", "-z", revision, "--", literal(path)).stdout
        if tree:
            entries = b"".join(
                entry.split(b" ", 1)[0] + b" " + entry.split(b" ", 2)[2] + b"\0"
                for entry in tree.split(b"\0")
                if entry
            )
            git(root, "update-index", "-z", "--index-info", index=index, data=entries)


def checkpoint(
    prepared: GitPreparation,
    directory: Path,
    committed: Callable[[str, Path], None],
    started: Callable[[str | None, str | None, Path], None],
) -> tuple[str | None, bytes | None, Path]:
    root = prepared.root
    if not prepared.existing:
        git(root, "init", "-q")
    index_path = Path(
        os.fsdecode(
            git(root, "rev-parse", "--path-format=absolute", "--git-path", "index").stdout
        ).strip()
    )
    alternate = directory / "baseline.index"
    started(*identity(root), index_path)
    if prepared.head:
        git(root, "read-tree", prepared.head, index=alternate)
    else:
        git(root, "read-tree", "--empty", index=alternate)
    if prepared.baseline:
        git(root, "add", "-f", "--", *(literal(p) for p in prepared.baseline), index=alternate)
    changed = git(root, "diff", "--cached", "--quiet", index=alternate, check=False).returncode
    if changed not in {0, 1}:
        raise LoadoutError("could not compare the migration checkpoint index")
    if not changed:
        return prepared.head, prepared.original_index, index_path
    if identity(root) != (prepared.head, prepared.symbolic) and prepared.existing:
        raise LoadoutError("Git HEAD changed before the migration checkpoint")
    if index_bytes(index_path) != prepared.original_index:
        raise LoadoutError("Git index changed before the migration checkpoint")
    git(root, "commit", "-m", "chore: checkpoint agent configuration", index=alternate)
    head, _ = identity(root)
    assert head is not None
    committed(head, index_path)
    return head, refresh_index(prepared, directory, head), index_path


def refresh_index(prepared: GitPreparation, directory: Path, head: str) -> bytes:
    refreshed = directory / "refreshed.index"
    if prepared.original_index is not None:
        atomic_install(refreshed, FrozenFile(prepared.original_index, 0o600))
    else:
        git(prepared.root, "read-tree", "--empty", index=refreshed)
    _set_paths(prepared.root, refreshed, prepared.baseline, head)
    return refreshed.read_bytes()


def committed_checkpoint(prepared: GitPreparation, directory: Path) -> str | None:
    head, symbolic = identity(prepared.root)
    if head == prepared.head:
        if prepared.existing and symbolic != prepared.symbolic:
            raise LoadoutError("Git symbolic ref changed during checkpoint")
        return None
    alternate = directory / "baseline.index"
    if (
        head is None
        or not alternate.is_file()
        or (prepared.existing and symbolic != prepared.symbolic)
    ):
        raise LoadoutError("Git HEAD changed outside the recorded checkpoint")
    parents = (
        git(prepared.root, "rev-list", "--parents", "-n", "1", head).stdout.decode().split()[1:]
    )
    expected = [prepared.head] if prepared.head else []
    tree = git(prepared.root, "write-tree", index=alternate).stdout
    if parents != expected or git(prepared.root, "rev-parse", head + "^{tree}").stdout != tree:
        raise LoadoutError(
            "Git HEAD does not match the recorded checkpoint; preserve it and reconcile the index"
        )
    return head


@dataclass(frozen=True)
class Staging:
    additions: tuple[str, ...]
    removals: tuple[str, ...]
    ignore: tuple[str, bytes] | None


def staged_index(
    root: Path,
    directory: Path,
    original: bytes | None,
    staging: Staging,
) -> bytes:
    index = directory / "final.index"
    if original is not None:
        atomic_install(index, FrozenFile(original, 0o600))
    else:
        git(root, "read-tree", "--empty", index=index)
    if staging.removals:
        git(root, "update-index", "--force-remove", "--", *staging.removals, index=index)
    if staging.additions:
        git(root, "add", "-f", "--", *(literal(p) for p in staging.additions), index=index)
    if staging.ignore:
        path, content = staging.ignore
        oid = git(root, "hash-object", "-w", "--stdin", data=content).stdout.strip()
        git(root, "update-index", "--add", "--cacheinfo", "100644", oid.decode(), path, index=index)
    return index.read_bytes()


def indexed_content(root: Path, path: str) -> bytes | None:
    entries = git(root, "ls-files", "--stage", "-z", "--", literal(path)).stdout
    if not entries:
        return None
    mode, oid, _ = entries.split(b"\t", 1)[0].split(b" ")
    if mode not in {b"100644", b"100755"}:
        raise LoadoutError(f"index path must be a regular file: {path}")
    return git(root, "cat-file", "blob", oid.decode()).stdout
