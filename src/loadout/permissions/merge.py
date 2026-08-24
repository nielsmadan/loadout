from __future__ import annotations

from collections.abc import Sequence

from .rules import Decision, Rules, dedupe, strictest


def _union(tiers: Sequence[Rules], attr: str) -> list[str]:
    combined: list[str] = []
    for tier in tiers:
        combined.extend(getattr(tier, attr))
    return dedupe(combined)


def _merge_default(tiers: Sequence[Rules]) -> Decision | None:
    """The strictest catch-all any tier states, or None when none does.

    Same resolution as a rule (ADR 0002) and for the same reason. An *unstated*
    default is not a tier saying "ask": counting it as one would let any tier
    that never mentions the key silently tighten one that did.
    """
    stated = [tier.default for tier in tiers if tier.default is not None]
    return strictest(stated) if stated else None


def merge_rules(*tiers: Rules) -> Rules:
    """Union every category across tiers, then resolve deny > ask > allow.

    Category resolution is order-independent: an entry's final decision is the
    strictest verdict any tier assigned it, regardless of which tier that was
    (ADR 0002). Emission order within a category, and conflict resolution in
    `opencode_extra`, are NOT order-independent — both follow tier argument
    order (first tier first, last tier wins on `opencode_extra` key clashes).
    Callers must pass tiers in a fixed order: committed, then personal.
    """
    if not tiers:
        return Rules()

    shell = {c: _union(tiers, c) for c in ("allow", "deny", "ask")}
    mcp = {c: _union(tiers, f"mcp_{c}") for c in ("allow", "deny", "ask")}

    def resolve(cats: dict[str, list[str]]) -> dict[str, tuple[str, ...]]:
        denied = set(cats["deny"])
        asked = [e for e in cats["ask"] if e not in denied]
        blocked = denied | set(asked)
        allowed = [e for e in cats["allow"] if e not in blocked]
        return {
            "allow": tuple(allowed),
            "deny": tuple(cats["deny"]),
            "ask": tuple(asked),
        }

    s, m = resolve(shell), resolve(mcp)

    opencode_extra: dict[str, str] = {}
    for tier in tiers:
        opencode_extra.update(tier.opencode_extra)

    return Rules(
        allow=s["allow"],
        deny=s["deny"],
        ask=s["ask"],
        mcp_allow=m["allow"],
        mcp_deny=m["deny"],
        mcp_ask=m["ask"],
        claude_extra_allow=tuple(_union(tiers, "claude_extra_allow")),
        claude_extra_deny=tuple(_union(tiers, "claude_extra_deny")),
        opencode_extra=opencode_extra,
        default=_merge_default(tiers),
    )
