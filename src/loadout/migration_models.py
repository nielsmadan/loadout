from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from .artifacts import ArtifactScope

Disposition = Literal["migrated", "runtime-private exclusion", "retained dependency", "unresolved"]
RENDERED_MODE = 0o600


@dataclass(frozen=True)
class RootMapping:
    source: Path
    destination: Path
    agents: tuple[str, ...]
    kind: Literal["harness", "shared", "file"] = "harness"
    category: str | None = None
    destination_template: str | None = None


@dataclass(frozen=True)
class SourceSelection:
    destination: Path
    source: Path


@dataclass(frozen=True)
class EntryState:
    path: Path
    kind: str
    mode: int | None = None
    digest: str | None = None
    link: str | None = None
    device: int | None = None
    inode: int | None = None


@dataclass(frozen=True)
class Issue:
    code: str
    paths: tuple[Path, ...]
    message: str


@dataclass(frozen=True)
class Candidate:
    path: Path
    canonical: Path | None
    destination: Path | None
    agents: tuple[str, ...]
    category: str
    disposition: Disposition
    reason: str
    content: bytes = field(default=b"", repr=False)
    mode: int = 0o644
    private: bool = False
    format: str = "copy"
    personal: bool = False


@dataclass(frozen=True)
class Inventory:
    root: Path
    source_root: Path
    scope: ArtifactScope | None
    agents: tuple[str, ...]
    mappings: tuple[RootMapping, ...]
    candidates: tuple[Candidate, ...]
    preconditions: tuple[EntryState, ...]
    issues: tuple[Issue, ...] = ()
    initialized: Path | None = None
    destination_environment: tuple[tuple[str, str], ...] = ()

    def preview(self) -> dict[str, Any]:
        return {
            "root": str(self.root),
            "source_root": str(self.source_root),
            "scope": self.scope,
            "agents": list(self.agents),
            "initialized": str(self.initialized) if self.initialized else None,
            "mappings": [
                {
                    "source": str(m.source),
                    "destination": str(m.destination),
                    "agents": list(m.agents),
                    "kind": m.kind,
                    "category": m.category,
                }
                for m in self.mappings
            ],
            "candidates": [
                {
                    "path": str(c.path),
                    "canonical": str(c.canonical) if c.canonical else None,
                    "destination": str(c.destination) if c.destination else None,
                    "agents": list(c.agents),
                    "category": c.category,
                    "disposition": c.disposition,
                    "reason": c.reason,
                    "private": c.private,
                }
                for c in self.candidates
            ],
            "issues": [_issue_preview(i) for i in self.issues],
        }


@dataclass(frozen=True)
class SourceWrite:
    path: Path
    content: bytes = field(repr=False)
    mode: int = 0o644
    private: bool = False


@dataclass(frozen=True)
class GeneratedWrite:
    path: Path
    content: bytes = field(repr=False)
    mode: int = 0o644
    format: str = "copy"
    owned: tuple[str, ...] | None = None
    emit_empty: bool = False
    mode_policy: Literal["exact", "preserve-destination"] = "exact"


@dataclass(frozen=True)
class ExpectedOutput:
    path: Path
    format: str
    digest: str
    mode: int | None = None
    owned: tuple[str, ...] | None = None
    fingerprints: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True)
class CategoryReadiness:
    category: str
    agents: tuple[str, ...]
    destinations: tuple[Path, ...]
    state: Literal["routed", "explicit-binding", "unsupported"]
    reason: str = ""


@dataclass(frozen=True)
class OriginalEntry:
    path: Path
    destination: Path | None
    action: Literal["retire", "generated", "retain"]
    reason: str


@dataclass(frozen=True)
class MigrationPlan:
    inventory: Inventory
    source_writes: tuple[SourceWrite, ...] = ()
    generated_writes: tuple[GeneratedWrite, ...] = ()
    expected_outputs: tuple[ExpectedOutput, ...] = ()
    required_absences: tuple[Path, ...] = ()
    obsolete: tuple[Path, ...] = ()
    originals: tuple[OriginalEntry, ...] = ()
    ignores: tuple[str, ...] = ()
    private_paths: tuple[Path, ...] = ()
    checkpoint_paths: tuple[Path, ...] = ()
    preconditions: tuple[EntryState, ...] = ()
    issues: tuple[Issue, ...] = ()
    validated: bool = False
    already_initialized: bool = False
    notes: tuple[str, ...] = ()
    categories: tuple[CategoryReadiness, ...] = ()
    artifact_routes: tuple[tuple[str, Path], ...] = ()

    @property
    def complete(self) -> bool:
        return not self.issues and (self.validated or self.already_initialized)

    def preview(self) -> dict[str, Any]:
        return {
            **self.inventory.preview(),
            "complete": self.complete,
            "validated": self.validated,
            "already_initialized": self.already_initialized,
            "source_writes": [
                {"path": str(w.path), "mode": w.mode, "private": w.private}
                for w in self.source_writes
            ],
            "generated_writes": [
                {
                    "path": str(w.path),
                    "format": w.format,
                    "partial": w.owned is not None,
                    "mode": w.mode,
                    "mode_policy": w.mode_policy,
                }
                for w in self.generated_writes
            ],
            "expected_outputs": [str(o.path) for o in self.expected_outputs],
            "required_absences": [str(p) for p in self.required_absences],
            "obsolete": [str(p) for p in self.obsolete],
            "originals": [
                {
                    "path": str(o.path),
                    "destination": str(o.destination) if o.destination else None,
                    "action": o.action,
                    "reason": o.reason,
                }
                for o in self.originals
            ],
            "ignores": list(self.ignores),
            "private_paths": [str(p) for p in self.private_paths],
            "checkpoint_paths": [str(p) for p in self.checkpoint_paths],
            "issues": [_issue_preview(i) for i in self.issues],
            "notes": list(self.notes),
            "categories": [
                {
                    "category": c.category,
                    "agents": list(c.agents),
                    "destinations": [str(p) for p in c.destinations],
                    "state": c.state,
                    "reason": c.reason,
                }
                for c in self.categories
            ],
        }


def _issue_preview(issue: Issue) -> dict[str, Any]:
    return {"code": issue.code, "paths": [str(p) for p in issue.paths], "message": issue.message}
