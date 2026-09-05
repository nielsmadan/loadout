from __future__ import annotations

import base64
import json
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .artifacts import _no_symlinks
from .deployment import MAX_MODE, FrozenFile, atomic_install
from .errors import LoadoutError
from .git_hooks import EVENTS, HOOK_MODE, hook_content, hooks_directory, local_directory
from .migration_git import git


@dataclass(frozen=True)
class Image:
    kind: str = "absent"
    content: bytes = field(default=b"", repr=False)
    mode: int = 0

    def document(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "content": base64.b64encode(self.content).decode(),
            "mode": self.mode,
        }

    @classmethod
    def parse(cls, value: dict[str, Any]) -> Image:
        if (
            not isinstance(value, dict)
            or set(value) != {"kind", "content", "mode"}
            or not isinstance(value["kind"], str)
            or not isinstance(value["content"], str)
            or type(value["mode"]) is not int
        ):
            raise LoadoutError("invalid migration journal image fields")
        image = cls(value["kind"], base64.b64decode(value["content"], validate=True), value["mode"])
        if (
            image.kind not in {"absent", "file", "symlink", "directory"}
            or not 0 <= image.mode <= MAX_MODE
        ):
            raise LoadoutError("invalid migration journal image")
        if image.kind in {"directory", "absent"} and image.content:
            raise LoadoutError("invalid directory or absent image content")
        if image.kind == "symlink" and (not image.content or b"\0" in image.content):
            raise LoadoutError("invalid migration journal symlink")
        return image


def snapshot(path: Path) -> Image:
    try:
        metadata = path.lstat()
    except FileNotFoundError:
        return Image()
    mode = stat.S_IMODE(metadata.st_mode)
    if stat.S_ISLNK(metadata.st_mode):
        return Image("symlink", os.fsencode(os.readlink(path)), mode)
    if stat.S_ISDIR(metadata.st_mode):
        return Image("directory", mode=mode)
    if stat.S_ISREG(metadata.st_mode):
        return Image("file", path.read_bytes(), mode)
    raise LoadoutError(f"unsupported migration entry: {path}")


def install(path: Path, image: Image) -> None:
    _no_symlinks(path.parent)
    current = snapshot(path)
    if current.kind == "directory" and image.kind != "directory":
        path.rmdir()
    elif current.kind == "symlink" or (current.kind == "file" and image.kind != "file"):
        path.unlink()
    if image.kind == "file":
        atomic_install(path, FrozenFile(image.content, image.mode))
    elif image.kind == "directory":
        path.mkdir(mode=image.mode, exist_ok=True)
        path.chmod(image.mode)
    elif image.kind == "symlink":
        path.symlink_to(os.fsdecode(image.content))
        if os.chmod in os.supports_follow_symlinks:
            os.chmod(path, image.mode, follow_symlinks=False)
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


@dataclass(frozen=True)
class Operation:
    path: Path
    before: Image
    after: Image
    phase: str

    def document(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "before": self.before.document(),
            "after": self.after.document(),
            "phase": self.phase,
        }

    @classmethod
    def parse(cls, value: dict[str, Any]) -> Operation:
        if (
            not isinstance(value, dict)
            or set(value) != {"path", "before", "after", "phase"}
            or not isinstance(value["path"], str)
            or value["phase"]
            not in {"topology", "source", "deploy", "retire", "registration", "ignore", "git-hook"}
        ):
            raise LoadoutError("invalid migration journal operation fields")
        path = Path(value["path"])
        if not path.is_absolute() or ".." in path.parts:
            raise LoadoutError("invalid migration journal path")
        return cls(path, Image.parse(value["before"]), Image.parse(value["after"]), value["phase"])


def protected(path: Path, *, tracking: bool = True) -> None:
    _no_symlinks(path)
    if not path.exists():
        return
    metadata = path.stat()
    if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise LoadoutError(f"migration state must be private and owned by this user: {path}")
    if not tracking:
        return
    probe = git(
        path if path.is_dir() else path.parent, "ls-files", "-z", "--", str(path), check=False
    )
    if (probe.returncode != 0 and b"not a git repository" not in probe.stderr) or probe.stdout:
        raise LoadoutError(f"migration state must be untracked: {path}")


@dataclass
class Journal:
    path: Path
    operations: tuple[Operation, ...]
    metadata: dict[str, Any]
    next: int = 0
    pending: bool = False
    status: str = "checkpoint"

    def save(self) -> None:
        protected(self.path.parent, tracking=False)
        document = {
            "version": 1,
            "operations": [operation.document() for operation in self.operations],
            "metadata": self.metadata,
            "next": self.next,
            "pending": self.pending,
            "status": self.status,
        }
        atomic_install(self.path, FrozenFile((json.dumps(document) + "\n").encode(), 0o600))
        descriptor = os.open(self.path.parent, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)

    def step(self) -> None:
        operation = self.operations[self.next]
        _no_symlinks(operation.path.parent)
        actual = snapshot(operation.path)
        if self.pending and actual == operation.after:
            self.next += 1
            self.pending = False
            self.save()
            return
        if actual != operation.before:
            raise LoadoutError(
                f"migration entry changed; recover using {self.path}: {operation.path}"
            )
        self.pending = True
        self.save()
        install(operation.path, operation.after)
        self.next += 1
        self.pending = False
        self.save()

    def run(self, *, phase: str | None = None) -> None:
        while self.next < len(self.operations):
            if phase is not None and self.operations[self.next].phase != phase:
                return
            self.step()

    def recover(self) -> tuple[Path, ...]:
        limit = self.next + int(self.pending)
        conflicts: list[Path] = []
        recovered = set(self.metadata.get("recovered_operations", []))
        for index in reversed(range(limit)):
            operation = self.operations[index]
            if index in recovered:
                continue
            if any(path.is_relative_to(operation.path) for path in conflicts):
                conflicts.append(operation.path)
                continue
            try:
                _no_symlinks(operation.path.parent)
                actual = snapshot(operation.path)
                if actual == operation.before:
                    recovered.add(index)
                    continue
                if actual != operation.after:
                    conflicts.append(operation.path)
                    continue
                install(operation.path, operation.before)
                recovered.add(index)
            except (OSError, LoadoutError):
                conflicts.append(operation.path)
            self.metadata["recovered_operations"] = sorted(recovered)
            self.save()
        self.status = "recovery-conflicts" if conflicts else "recovered"
        self.save()
        return tuple(dict.fromkeys(conflicts))

    @classmethod
    def load(cls, path: Path) -> Journal:
        path = path.absolute()
        for parent in (path, path.parent, path.parent.parent, path.parent.parent.parent):
            protected(parent)
        try:
            raw = json.loads(path.read_bytes())
            if (
                not isinstance(raw, dict)
                or set(raw) != {"version", "operations", "metadata", "next", "pending", "status"}
                or raw["version"] != 1
            ):
                raise LoadoutError("unsupported migration journal version")
            if (
                not isinstance(raw["operations"], list)
                or not isinstance(raw["metadata"], dict)
                or type(raw["next"]) is not int
                or type(raw["pending"]) is not bool
            ):
                raise LoadoutError("invalid migration journal fields")
            if raw["status"] not in {
                "checkpoint",
                "checkpoint-committed",
                "checkpoint-index",
                "apply",
                "stage-index",
                "complete",
                "recovered",
                "recovery-conflicts",
            }:
                raise LoadoutError("invalid migration journal status")
            result = cls(
                path,
                tuple(Operation.parse(o) for o in raw["operations"]),
                raw["metadata"],
                raw["next"],
                raw["pending"],
                raw["status"],
            )
            if not 0 <= result.next <= len(result.operations) or (
                result.pending and result.next == len(result.operations)
            ):
                raise LoadoutError("invalid migration journal cursor")
            root = Path(result.metadata["root"])
            if path.parent.parent != root / ".loadout-state/migrations":
                raise LoadoutError("migration journal moved outside its recorded root")
            _validate_scope(result)
            return result
        except (ValueError, KeyError, TypeError) as error:
            raise LoadoutError(f"invalid migration journal: {path}") from error


def _absolute(value: Any) -> Path:
    if not isinstance(value, str):
        raise LoadoutError("invalid journal scope path")
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or "\0" in value or path == Path(path.anchor):
        raise LoadoutError("invalid journal scope boundary")
    return path


def _validate_scope(journal: Journal) -> None:
    metadata = journal.metadata
    root, source = _absolute(metadata["root"]), _absolute(metadata["source_root"])
    if not source.is_relative_to(root):
        raise LoadoutError("journal source escapes its selected root")
    destinations = tuple(_absolute(p) for p in metadata["destinations"])
    registration = tuple(_absolute(p) for p in metadata["registration"])
    if not isinstance(metadata["outputs"], dict):
        raise LoadoutError("invalid journal output inventory")
    for raw, image in metadata["outputs"].items():
        path = _absolute(raw)
        if Image.parse(image).kind != "file" or not any(
            path.is_relative_to(p) for p in destinations
        ):
            raise LoadoutError(f"journal output escapes its declared scope: {path}")
    _validate_hooks(journal)
    for operation in (o for o in journal.operations if o.phase != "git-hook"):
        path = operation.path
        if ".git" in path.parts or path == root or root.is_relative_to(path):
            raise LoadoutError(
                f"journal operation overlaps repository metadata or its root: {path}"
            )
        if operation.phase in {"source", "deploy"}:
            allowed = path.is_relative_to(source) or (
                operation.phase == "deploy" and any(path.is_relative_to(p) for p in destinations)
            )
        elif operation.phase == "retire":
            allowed = path.is_relative_to(root) and not path.is_relative_to(source)
        elif operation.phase == "ignore":
            allowed = path == root / ".gitignore"
        elif operation.phase == "registration":
            allowed = any(
                path == p or (operation.after.kind == "directory" and p.is_relative_to(path))
                for p in registration
            )
        else:
            allowed = any(
                path.is_relative_to(p)
                or (operation.after.kind in {"absent", "directory"} and p.is_relative_to(path))
                for p in destinations
            )
        if not allowed:
            raise LoadoutError(f"journal operation escapes its declared scope: {path}")
    _validate_git(metadata, root)


def _validate_hooks(journal: Journal) -> None:
    for operation in journal.operations:
        if operation.phase == "git-hook":
            _validate_hook(journal.metadata, operation)


def _validate_hook(metadata: dict[str, Any], operation: Operation) -> None:
    hooks = metadata.get("git_hooks")
    if not isinstance(hooks, dict):
        raise LoadoutError("journal Git hook operation lacks its installation plan")
    repository, directory = _absolute(hooks["repository"]), _absolute(hooks["directory"])
    existing = git(repository, "rev-parse", "--show-toplevel", check=False).returncode == 0
    source = Path(hooks["source"])
    if (
        not Path(metadata["root"]).is_relative_to(repository)
        or source.is_absolute()
        or ".." in source.parts
        or repository / source not in {Path(metadata["root"]), Path(metadata["source_root"])}
        or hooks_directory(repository, existing=existing) != directory
        or not local_directory(repository, directory, existing=existing)
        or operation.path.parent != directory
        or operation.path.name not in EVENTS
        or operation.before != Image()
        or operation.after.kind != "file"
        or operation.after.mode != HOOK_MODE
        or operation.after.content != hook_content(operation.path.name, source, hooks["profile"])
        or not any(
            h["path"] == str(operation.path) and h["status"] == "install" for h in hooks["hooks"]
        )
    ):
        raise LoadoutError(f"journal Git hook escapes its installation plan: {operation.path}")


def _validate_git(metadata: dict[str, Any], root: Path) -> None:
    raw_git = metadata["git"]
    if raw_git is not None:
        git_root = _absolute(raw_git["root"])
        if not root.is_relative_to(git_root):
            raise LoadoutError("journal Git root does not enclose its selected root")
        for key in ("additions", "removals"):
            if not isinstance(metadata[key], list) or any(
                not isinstance(p, str) or not p or Path(p).is_absolute() or ".." in Path(p).parts
                for p in metadata[key]
            ):
                raise LoadoutError("invalid journal staging paths")
        if metadata.get("index_path") is not None:
            actual = (
                git(git_root, "rev-parse", "--path-format=absolute", "--git-path", "index")
                .stdout.decode()
                .strip()
            )
            if metadata["index_path"] != actual:
                raise LoadoutError("journal index does not belong to its repository")
