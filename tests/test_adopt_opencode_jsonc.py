"""init adopts opencode.jsonc into opencode.json, dropping its comments, and refuses when
both files exist, since OpenCode reads and merges the two (P-41)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import loadout
from loadout.errors import LoadoutError
from loadout.native_documents import jsonc_to_json

pytestmark = pytest.mark.migration_integration

JSONC = """\
{
  // the model everything uses
  "$schema": "https://opencode.ai/config.json",
  "theme": "tokyonight", /* dark */
  "permission": {
    "bash": {"rm *": "deny", "git status": "allow",},
  },
  "share": "http://not-a-comment",
}
"""


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


def _project(root: Path, files: dict[str, str]) -> Path:
    for relative, content in files.items():
        _write(root / relative, content)
    for args in (("init", "-q"), ("add", "-A"), ("commit", "-qm", "base")):
        subprocess.run(
            ["git", "-C", str(root), "-c", "user.email=t@e.com", "-c", "user.name=t", *args],
            check=True,
            capture_output=True,
        )
    return root


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ('{"a": 1, // one\n"b": [1, 2,],}', {"a": 1, "b": [1, 2]}),
        ('{"url": "http://x//y", "s": "a /* b */ c"}', {"url": "http://x//y", "s": "a /* b */ c"}),
        ('{"q": "say \\"hi\\" // not", /* x */ "n": null}', {"q": 'say "hi" // not', "n": None}),
    ],
)
def test_jsonc_reads_as_its_json(text: str, expected: object) -> None:
    assert json.loads(jsonc_to_json(text)) == expected


def test_an_unclosed_block_comment_is_refused() -> None:
    with pytest.raises(LoadoutError, match="never closed"):
        jsonc_to_json('{"a": 1 /* open')


def test_a_project_opencode_jsonc_becomes_opencode_json(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path / "p", {"opencode.jsonc": JSONC})
    argv = ["init", "project", "--root", str(root), "--harness", "opencode"]

    assert loadout.main([*argv, "--dry-run"]) == 0
    assert f"{root / 'opencode.jsonc'}: comments in opencode.jsonc will be lost" in (
        capsys.readouterr().out
    )
    assert loadout.main([*argv, "--yes"]) == 0

    document = json.loads((root / "opencode.json").read_text())
    assert document["theme"] == "tokyonight"
    assert document["share"] == "http://not-a-comment"
    assert document["permission"]["bash"]["rm *"] == "deny"
    assert not (root / "opencode.jsonc").exists()
    capsys.readouterr()
    assert loadout.main(["check", "--root", str(root)]) == 0


def test_a_global_opencode_jsonc_becomes_opencode_json(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    config = fake_home / ".config/opencode"
    _write(config / "opencode.jsonc", JSONC)
    source = tmp_path / "source"
    source.mkdir()

    assert loadout.main(["init", "global", str(source), "--harness", "opencode", "--yes"]) == 0

    assert json.loads((config / "opencode.json").read_text())["theme"] == "tokyonight"
    assert not (config / "opencode.jsonc").exists()
    capsys.readouterr()
    assert loadout.main(["check", "--global"]) == 0


def test_both_files_present_is_refused_with_both_named(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path / "p", {"opencode.jsonc": JSONC, "opencode.json": "{}\n"})

    code = loadout.main(["init", "project", "--root", str(root), "--harness", "opencode", "--yes"])

    assert code == 2
    err = capsys.readouterr().err
    assert "OpenCode reads both opencode.json and opencode.jsonc" in err
    assert str(root / "opencode.json") in err
    assert (root / "opencode.jsonc").read_text() == JSONC
