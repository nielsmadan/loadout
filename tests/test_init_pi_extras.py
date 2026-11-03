"""What init does with a Pi permission document holding more than loadout carries.

loadout 1.0 renders only Pi's shell and MCP rules into pi-permission-system's config. Any other
key the extension reads is refused when dropping it changes what Pi allows, and noted when it
does not (the user's ruling on review question 1).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import loadout

pytestmark = pytest.mark.migration_integration

GLOBAL = ".pi/agent/extensions/pi-permission-system/config.json"
PROJECT = ".pi/extensions/pi-permission-system/config.json"


def _git(root: Path) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    return root


def _write(path: Path, document: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document))
    return path


def _global(tmp_path: Path, home: Path, document: object, capsys) -> tuple[int, dict, Path]:
    original = _write(home / GLOBAL, document)
    argv = ["init", "global", str(_git(tmp_path / "src")), "--harness", "pi", "--dry-run"]
    code = loadout.main([*argv, "--json"])
    return code, json.loads(capsys.readouterr().out), original


def _project(tmp_path: Path, document: object, capsys) -> tuple[int, dict, Path]:
    root = _git(tmp_path / "p")
    original = _write(root / PROJECT, document)
    argv = ["init", "project", "--root", str(root), "--harness", "pi", "--dry-run", "--json"]
    code = loadout.main(argv)
    return code, json.loads(capsys.readouterr().out), original


@pytest.mark.parametrize("scope", ["global", "project"])
def test_a_deny_with_a_reason_is_refused_as_a_rule_init_cannot_read(
    tmp_path: Path, fake_home: Path, capsys, scope: str
) -> None:
    document = {"permission": {"bash": {"npm *": {"action": "deny", "reason": "Use pnpm"}}}}
    code, preview, original = (
        _global(tmp_path, fake_home, document, capsys)
        if scope == "global"
        else _project(tmp_path, document, capsys)
    )

    label = "pi" if scope == "global" else "pi-project"
    assert code == 2
    assert preview["issues"] == [
        {
            "code": "uncarried-rule",
            "paths": [str(original)],
            "message": (
                "The source has no place for these rules, so converting would lose them: "
                f"{label}: decision {{'action': 'deny', 'reason': 'Use pnpm'}} for 'npm *'."
            ),
        }
    ]


UNCARRIED = (
    "Pi would run without them, since loadout 1.0 carries only Pi's shell and MCP rules; remove "
    "them from this file, or leave Pi out of this init by naming only the other harnesses with "
    "--harness"
)
EXAMPLE = {
    "$schema": "https://raw.githubusercontent.com/gotgenes/pi-packages/main/packages/pi-permission-system/schemas/permissions.schema.json",
    "debugLog": False,
    "permissionReviewLog": True,
    "yoloMode": False,
    "doublePressToConfirm": True,
    "toolInputPreviewMaxLength": 400,
    "toolTextSummaryMaxLength": 120,
    "piInfrastructureReadPaths": [],
    "authorizerChain": [],
    "shellTools": {"exec_command": {"commandArgument": "cmd", "workdirArgument": "workdir"}},
    "permission": {
        "*": "ask",
        "path": {"*": "allow", "*.env": "deny", "*.env.*": "deny", "*.env.example": "allow"},
        "read": "allow",
        "write": "deny",
        "edit": "deny",
        "bash": {
            "*": "ask",
            "git *": "ask",
            "git status": "allow",
            "git diff": "allow",
            "npm *": {"action": "deny", "reason": "Use pnpm instead"},
        },
        "mcp": {"*": "ask", "mcp_status": "allow", "mcp_list": "allow"},
        "skill": {"*": "ask"},
        "external_directory": {
            "*": "ask",
            "~/development/*": "allow",
            "~/.cargo/registry/*": "allow",
        },
    },
}
RULES = {"bash": {"*": "ask", "pwd": "allow"}, "mcp__docs__search": "allow"}


def _run(
    scope: str, tmp_path: Path, home: Path, document: object, capsys
) -> tuple[int, dict, Path]:
    if scope == "global":
        return _global(tmp_path, home, document, capsys)
    return _project(tmp_path, document, capsys)


def _refusal(keys: list[str]) -> str:
    return (
        "The source has no place for these settings, so converting would change what the "
        f"harness does: {', '.join(keys)}: {UNCARRIED}."
    )


def _refused(code: int, preview: dict, original: Path) -> str:
    assert code == 2
    [issue] = preview["issues"]
    assert (issue["code"], issue["paths"]) == ("uncarried-setting", [str(original)])
    message: str = issue["message"]
    return message


@pytest.mark.parametrize("scope", ["global", "project"])
def test_an_original_with_only_shell_and_mcp_rules_converts(
    tmp_path: Path, fake_home: Path, capsys, scope: str
) -> None:
    code, preview, _ = _run(scope, tmp_path, fake_home, {"permission": RULES}, capsys)

    assert (code, preview["issues"]) == (0, [])


def test_a_global_top_level_ask_is_noted(tmp_path: Path, fake_home: Path, capsys) -> None:
    document = {"permission": {"*": "ask", **RULES}}
    code, preview, _ = _global(tmp_path, fake_home, document, capsys)

    destination = fake_home / GLOBAL
    assert code == 0
    assert (
        f'{destination}: pi: the top-level "*" is "ask", which the extension applies without '
        "it, so it is not carried"
    ) in preview["notes"]


@pytest.mark.parametrize(
    ("scope", "verdict"), [("global", "allow"), ("global", "deny"), ("project", "ask")]
)
def test_a_top_level_fallback_whose_loss_changes_pi_is_refused(
    tmp_path: Path, fake_home: Path, capsys, scope: str, verdict: str
) -> None:
    """A project's `"*"` replaces the global one when the two merge, so even `ask` matters there."""
    document = {"permission": {"*": verdict, **RULES}}
    code, preview, original = _run(scope, tmp_path, fake_home, document, capsys)

    assert _refused(code, preview, original) == _refusal(['permission."*"'])


@pytest.mark.parametrize("scope", ["global", "project"])
def test_every_other_surface_is_refused_in_one_issue_naming_each(
    tmp_path: Path, fake_home: Path, capsys, scope: str
) -> None:
    surfaces = {"read": "allow", "write": "deny", "path": {"*.env": "deny"}, "skill": "ask"}
    code, preview, original = _run(
        scope, tmp_path, fake_home, {"permission": {**RULES, **surfaces}}, capsys
    )

    keys = ["permission.read", "permission.write", "permission.path", "permission.skill"]
    assert _refused(code, preview, original) == _refusal(keys)


GATING = {
    "yoloMode": (True, False),
    "piInfrastructureReadPaths": (["~/notes"], []),
    "authorizerChain": (["reviewer"], []),
    "shellTools": ({"exec_command": {"commandArgument": "cmd"}}, {}),
}


@pytest.mark.parametrize("scope", ["global", "project"])
@pytest.mark.parametrize("key", list(GATING))
def test_a_setting_that_changes_what_pi_allows_is_refused(
    tmp_path: Path, fake_home: Path, capsys, scope: str, key: str
) -> None:
    document = {key: GATING[key][0], "permission": RULES}
    code, preview, original = _run(scope, tmp_path, fake_home, document, capsys)

    assert _refused(code, preview, original) == _refusal([key])


@pytest.mark.parametrize("key", list(GATING))
def test_a_global_setting_at_the_extensions_default_is_noted(
    tmp_path: Path, fake_home: Path, capsys, key: str
) -> None:
    value = GATING[key][1]
    code, preview, _ = _global(tmp_path, fake_home, {key: value, "permission": RULES}, capsys)

    assert code == 0
    assert (
        f"{fake_home / GLOBAL}: pi: {key} is {json.dumps(value)}, which the extension applies "
        "without it, so it is not carried"
    ) in preview["notes"]


@pytest.mark.parametrize("key", list(GATING))
def test_a_project_setting_at_the_extensions_default_is_refused(
    tmp_path: Path, fake_home: Path, capsys, key: str
) -> None:
    """A project value replaces the global one, so `false` can switch off a global `true`."""
    document = {key: GATING[key][1], "permission": RULES}
    code, preview, original = _project(tmp_path, document, capsys)

    assert _refused(code, preview, original) == _refusal([key])


DISPLAY = {
    "debugLog": True,
    "permissionReviewLog": False,
    "doublePressToConfirm": False,
    "toolInputPreviewMaxLength": 400,
    "toolTextSummaryMaxLength": 120,
}


@pytest.mark.parametrize("scope", ["global", "project"])
def test_a_setting_for_logs_or_prompts_is_noted(
    tmp_path: Path, fake_home: Path, capsys, scope: str
) -> None:
    code, preview, original = _run(
        scope, tmp_path, fake_home, {**DISPLAY, "permission": RULES}, capsys
    )

    label = "pi" if scope == "global" else "pi-project"
    destination = fake_home / GLOBAL if scope == "global" else original
    assert code == 0
    assert [
        f"{destination}: {label}: {key} is not carried; it changes the extension's logs or "
        "prompts, not what it allows"
        for key in DISPLAY
    ] == [note for note in preview["notes"] if "is not carried; it changes" in note]


@pytest.mark.parametrize("scope", ["global", "project"])
def test_a_key_the_extension_does_not_know_is_refused(
    tmp_path: Path, fake_home: Path, capsys, scope: str
) -> None:
    """The extension rejects the whole file over it, so every tool asked before init."""
    document = {"defaultPolicy": {"tools": "allow"}, "permission": RULES}
    code, preview, original = _run(scope, tmp_path, fake_home, document, capsys)

    assert _refused(code, preview, original) == _refusal(["defaultPolicy"])


def _rules_refusal(details: list[str]) -> str:
    return (
        "The source has no place for these rules, so converting would lose them: "
        f"{'; '.join(details)}."
    )


UNREADABLE = [
    "decision {'action': 'deny', 'reason': 'Use pnpm instead'} for 'npm *'",
    "unpaired mcp pattern 'mcp_status'",
    "unpaired mcp pattern 'mcp_list'",
]


def test_the_extensions_example_config_gives_every_refusal_in_one_run(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    """Removing only the keys the first refusal named used to bring a second one."""
    code, preview, original = _global(tmp_path, fake_home, EXAMPLE, capsys)

    keys = [
        "shellTools",
        "permission.path",
        "permission.read",
        "permission.write",
        "permission.edit",
        "permission.skill",
        "permission.external_directory",
    ]
    assert code == 2
    assert preview["issues"] == [
        {"code": "uncarried-setting", "paths": [str(original)], "message": _refusal(keys)},
        {
            "code": "uncarried-rule",
            "paths": [str(original)],
            "message": _rules_refusal([f"pi: {detail}" for detail in UNREADABLE]),
        },
    ]


def test_the_example_config_in_a_project_gives_every_refusal_in_one_run(
    tmp_path: Path, capsys
) -> None:
    """A project also refuses what it would replace, and its mcp map takes no seed."""
    code, preview, original = _project(tmp_path, EXAMPLE, capsys)

    keys = [
        "yoloMode",
        "piInfrastructureReadPaths",
        "authorizerChain",
        "shellTools",
        'permission."*"',
        "permission.path",
        "permission.read",
        "permission.write",
        "permission.edit",
        "permission.skill",
        "permission.external_directory",
    ]
    details = [UNREADABLE[0], "unpaired mcp pattern '*'", *UNREADABLE[1:]]
    assert code == 2
    assert preview["issues"] == [
        {"code": "uncarried-setting", "paths": [str(original)], "message": _refusal(keys)},
        {
            "code": "uncarried-rule",
            "paths": [str(original)],
            "message": _rules_refusal([f"pi-project: {detail}" for detail in details]),
        },
    ]
