from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from .errors import LoadoutError
from .harnesses import KNOWN_HARNESSES
from .skills import Skill, renamed_skill

ALIAS = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")


@dataclass(frozen=True)
class SkillPolicy:
    name: str
    agents: tuple[str, ...] | None
    render: bool = True
    alias: str | None = None


def parse_alias(block: dict[str, object], label: str) -> str | None:
    if "name" not in block:
        return None
    alias = block["name"]
    if not isinstance(alias, str) or not ALIAS.fullmatch(alias):
        raise LoadoutError(
            f"{label}.name {alias!r} is not a usable name; use letters, digits, '.', '_' and "
            "'-', starting with a letter or digit"
        )
    if alias.endswith(".local"):
        raise LoadoutError(
            f"{label}.name {alias!r} ends in '.local', which marks a replacement, not a name; "
            "pick another name"
        )
    return alias


def refuse_alias_clashes(label: str, aliases: Sequence[tuple[str, str | None]]) -> None:
    names = {name for name, _ in aliases}
    claimed: dict[str, str] = {}
    for name, alias in aliases:
        if alias is None:
            continue
        if alias == name:
            raise LoadoutError(f"{label}.{name}.name repeats its own name; remove it")
        if alias in claimed:
            raise LoadoutError(
                f"{label}.{claimed[alias]}.name and {label}.{name}.name both rename onto "
                f"{alias!r}; pick a different name for one of them"
            )
        if alias in names:
            raise LoadoutError(
                f"{label}.{name}.name renames onto {alias!r}, which [{label}.{alias}] already "
                "names; pick a different name"
            )
        claimed[alias] = name


def aliased_names(
    kind: str, names: Iterable[str], aliases: Sequence[tuple[str, str | None]]
) -> dict[str, str]:
    present = tuple(names)
    renames = {name: alias for name, alias in aliases if alias is not None and name in present}
    for name, alias in renames.items():
        if alias in present:
            raise LoadoutError(
                f"{kind}s.{name}.name renames onto {alias!r}, the name of another {kind}; "
                "pick a different name"
            )
    return renames


def parse_skill_policies(raw: object, label: str = "skills") -> tuple[SkillPolicy, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, dict):
        raise LoadoutError(f"[{label}] must be a table")
    policies: list[SkillPolicy] = []
    for name, block in raw.items():
        if (
            not isinstance(name, str)
            or not name
            or name in {".", ".."}
            or "/" in name
            or "\\" in name
        ):
            raise LoadoutError(f"{label} contains an invalid skill name: {name!r}")
        if not isinstance(block, dict):
            raise LoadoutError(f"{label}.{name} must be a table")
        if not block:
            raise LoadoutError(
                f"{label}.{name} is empty; set 'agents', 'render' or 'name', or remove the table"
            )
        unknown_keys = sorted(set(block) - {"agents", "render", "name"})
        if unknown_keys:
            raise LoadoutError(
                f"{label}.{name}: unrecognised key(s) {', '.join(unknown_keys)}; "
                f"'agents', 'render' and 'name' are the keys it accepts"
            )
        policies.append(
            SkillPolicy(
                name,
                _agents(block, f"{label}.{name}"),
                _render(block, f"{label}.{name}"),
                parse_alias(block, f"{label}.{name}"),
            )
        )
    refuse_alias_clashes(label, [(policy.name, policy.alias) for policy in policies])
    return tuple(policies)


def _agents(block: dict[str, object], label: str) -> tuple[str, ...] | None:
    if "agents" not in block:
        return None
    agents = block["agents"]
    if not isinstance(agents, list) or not all(isinstance(agent, str) for agent in agents):
        raise LoadoutError(f"{label}.agents must be a list of strings")
    if len(agents) != len(set(agents)):
        raise LoadoutError(f"{label}.agents contains duplicates")
    unknown_harnesses = sorted(set(agents) - KNOWN_HARNESSES)
    if unknown_harnesses:
        raise LoadoutError(f"{label}: unknown harness(es) {', '.join(unknown_harnesses)}")
    return tuple(agents)


def _render(block: dict[str, object], label: str) -> bool:
    render = block.get("render", True)
    if not isinstance(render, bool):
        raise LoadoutError(f"{label}.render must be a boolean")
    return render


def selected_skill_agents(
    name: str, available: tuple[str, ...], policies: tuple[SkillPolicy, ...]
) -> tuple[str, ...]:
    for policy in policies:
        if policy.name == name and policy.agents is not None:
            return tuple(agent for agent in available if agent in policy.agents)
    return available


def render_skill_for(name: str, policies: tuple[SkillPolicy, ...]) -> bool:
    for policy in policies:
        if policy.name == name:
            return policy.render
    return True


def skill_aliases(names: Iterable[str], policies: tuple[SkillPolicy, ...]) -> dict[str, str]:
    return aliased_names("skill", names, [(policy.name, policy.alias) for policy in policies])


def skill_aliases_in(
    declared: Path, trees: tuple[Skill, ...], policies: tuple[SkillPolicy, ...]
) -> dict[str, str]:
    try:
        return skill_aliases((skill.name for skill in trees), policies)
    except LoadoutError as error:
        raise LoadoutError(f"{declared}: {error}") from error


def aliased_skill(skill: Skill, aliases: Mapping[str, str]) -> Skill:
    return renamed_skill(skill, aliases[skill.name]) if skill.name in aliases else skill
