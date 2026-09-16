from __future__ import annotations

from dataclasses import dataclass

from .agents import known_agents
from .errors import LoadoutError


@dataclass(frozen=True)
class SkillPolicy:
    name: str
    agents: tuple[str, ...]
    render: bool = True


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
        unknown_keys = sorted(set(block) - {"agents", "render"})
        if unknown_keys:
            raise LoadoutError(
                f"{label}.{name}: unrecognised key(s) {', '.join(unknown_keys)}; "
                f"'agents' and 'render' are the keys it accepts"
            )
        agents = block.get("agents")
        if not isinstance(agents, list) or not all(isinstance(agent, str) for agent in agents):
            raise LoadoutError(f"{label}.{name}.agents must be a list of strings")
        if len(agents) != len(set(agents)):
            raise LoadoutError(f"{label}.{name}.agents contains duplicates")
        unknown_agents = sorted(set(agents) - known_agents())
        if unknown_agents:
            raise LoadoutError(f"{label}.{name}: unknown agent(s) {', '.join(unknown_agents)}")
        render = block.get("render", True)
        if not isinstance(render, bool):
            raise LoadoutError(f"{label}.{name}.render must be a boolean")
        policies.append(SkillPolicy(name, tuple(agents), render))
    return tuple(policies)


def selected_skill_agents(
    name: str, available: tuple[str, ...], policies: tuple[SkillPolicy, ...]
) -> tuple[str, ...]:
    for policy in policies:
        if policy.name == name:
            return tuple(agent for agent in available if agent in policy.agents)
    return available


def render_skill_for(name: str, policies: tuple[SkillPolicy, ...]) -> bool:
    for policy in policies:
        if policy.name == name:
            return policy.render
    return True
