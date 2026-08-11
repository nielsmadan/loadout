from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any, TypeGuard

__all__ = ["merge_documents"]


def _is_map(value: object) -> TypeGuard[Mapping[str, Any]]:
    return isinstance(value, Mapping)


def merge_documents(*documents: Mapping[str, Any]) -> dict[str, Any]:
    """Compose JSON documents: RFC 7386, except that arrays concatenate.

    Used by every deep-merged slice — settings, hooks and plugins. Instructions
    concatenate text and permissions union with deny-wins instead; see
    `composition.py` and `permissions/merge.py`.

    - **Maps merge recursively.**
    - A key's **position** comes from its first appearance, its **value** from
      its last. Key order is load-bearing in generated output, so composing must
      not reorder a document that a later fragment only adds to.
    - **Lists concatenate**, in argument order, with no deduplication. Two
      fragments each adding a hook to one event must produce both hooks;
      replacing would force a fragment to restate a list to append to it, which
      is the 208-line-copy problem this mechanism exists to remove. Repeats are
      kept because two fragments naming the same command can be deliberate.
    - **`None` removes a key**, nested or not. This is the only way to take
      something out, and it is why a profile can switch a plugin off without
      deleting the fragment that declares it. Removal is total: a later fragment
      that adds the key back is a first appearance, so it lands at the end
      rather than where the key used to sit.
    - Anything else — a type change, a scalar over a map — is last-wins.

    Arguments are never mutated; nothing shared with them survives into the
    result.
    """
    merged: dict[str, Any] = {}
    for document in documents:
        for key, value in document.items():
            if value is None:
                merged.pop(key, None)
                continue
            existing = merged.get(key)
            if _is_map(existing) and _is_map(value):
                merged[key] = merge_documents(existing, value)
            elif isinstance(existing, list) and isinstance(value, list):
                merged[key] = [*existing, *copy.deepcopy(value)]
            elif _is_map(value):
                # A map arriving over a non-map still merges into itself, so
                # nested None removals inside it are honoured rather than copied
                # through as literal nulls.
                merged[key] = merge_documents(value)
            else:
                merged[key] = copy.deepcopy(value)
    return merged
