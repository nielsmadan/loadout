from pathlib import Path

import pytest

from loadout.emit import check_all, module_config_files, skill_trees, write_all
from loadout.errors import LoadoutError
from loadout.manifest import Manifest
from loadout.sources import parse_sources


def write(root: Path, name: str, body: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    return path


def manifest(root: Path, overrides: dict[str, list[str]]) -> Manifest:
    return Manifest(
        sources=parse_sources(
            [
                {"name": "company", "path": "company"},
                {"name": "personal", "path": "personal", "overrides": overrides},
            ],
            root,
        ),
        targets=(),
    )


def test_skill_override_replaces_the_entire_tree(tmp_path: Path) -> None:
    write(tmp_path, "company/skills/review/SKILL.md", "company")
    write(tmp_path, "company/skills/review/old.txt", "old helper")
    write(tmp_path, "company/skills/retained/SKILL.md", "retained")
    winner = write(tmp_path, "personal/skills/review/SKILL.md", "personal")
    write(tmp_path, "personal/skills/review/new.txt", "new helper")
    config = manifest(tmp_path, {"skills": ["review"]})
    retained, review = skill_trees(config)
    assert (retained.name, retained.document.read_text()) == ("retained", "retained")
    assert review.document == winner
    assert review.document.read_text() == "personal"
    assert tuple(map(str, review.supporting)) == ("new.txt",)
    assert len(set(config.sources)) == 2


def test_module_override_keeps_the_winners_bytes_and_mode(tmp_path: Path) -> None:
    write(tmp_path, "company/module-config/pi/settings.json", "company")
    write(tmp_path, "company/module-config/pi/retained.json", "retained")
    winner = write(tmp_path, "personal/module-config/pi/settings.json", "personal\n")
    winner.chmod(0o751)
    config = manifest(tmp_path, {"module-config": ["pi/settings.json"]})
    files = {str(name): path for name, path in module_config_files(config, "pi")}
    assert files["settings.json"].read_bytes() == b"personal\n"
    assert files["settings.json"].stat().st_mode & 0o777 == 0o751
    assert files["retained.json"].read_text() == "retained"


def test_declared_overrides_reach_sync_and_check(tmp_path: Path, fake_home: Path) -> None:
    write(tmp_path, "company/skills/review/SKILL.md", "company")
    write(tmp_path, "company/skills/review/old.txt", "old helper")
    write(tmp_path, "company/skills/retained/SKILL.md", "retained\n")
    write(tmp_path, "personal/skills/review/SKILL.md", "personal\n")
    write(tmp_path, "personal/skills/review/new.txt", "new helper")
    write(tmp_path, "company/permissions.toml", '[shell]\nallow=["git status"]\n')
    write(tmp_path, "company/module-config/pi/module.json", "company")
    write(tmp_path, "company/module-config/pi/retained.json", "retained")
    winner = write(tmp_path, "personal/module-config/pi/module.json", "personal\n")
    winner.chmod(0o751)
    write(
        tmp_path,
        "loadout.toml",
        '[[source]]\nname="company"\npath="company"\nuse=["skills","module-config","permissions"]\n'
        '[[source]]\nname="personal"\npath="personal"\nuse=["skills","module-config"]\n'
        '[source.overrides]\nskills=["review"]\nmodule-config=["pi/module.json"]\n[pi]\n',
    )
    write_all(tmp_path)
    output = fake_home / ".pi/agent"
    assert (output / "skills/review/SKILL.md").read_text().endswith("personal\n")
    assert sorted(p.name for p in (output / "skills/review").iterdir()) == ["SKILL.md", "new.txt"]
    assert (output / "skills/retained/SKILL.md").read_text().endswith("retained\n")
    assert (output / "module.json").read_bytes() == b"personal\n"
    assert (output / "module.json").stat().st_mode & 0o777 == 0o751
    assert (output / "retained.json").read_text() == "retained"
    assert check_all(tmp_path) == []


@pytest.mark.parametrize("category", ["skills", "module-config"])
@pytest.mark.parametrize("missing", ["replacing item", "earlier contender"])
def test_override_must_name_both_contenders(tmp_path: Path, category: str, missing: str) -> None:
    for name in ("company", "personal"):
        (tmp_path / name).mkdir()
    item = "review" if category == "skills" else "pi/settings.json"
    filename = (
        "skills/review/SKILL.md" if category == "skills" else "module-config/pi/settings.json"
    )
    source = "company" if missing == "replacing item" else "personal"
    write(tmp_path, f"{source}/{filename}", source)
    config = manifest(tmp_path, {category: [item]})
    with pytest.raises(LoadoutError, match=f"no {missing}"):
        if category == "skills":
            skill_trees(config)
        else:
            module_config_files(config, "pi")


@pytest.mark.parametrize(
    "overrides",
    [
        {"mcp": ["server"]},
        {"skills": ["../review"]},
        {"skills": ["review", "./review"]},
        {"module-config": ["settings.json"]},
        {"module-config": ["unknown/settings.json"]},
        {"module-config": ["pi/../settings.json"]},
        {"skills": "review"},
    ],
)
def test_invalid_override_declarations_are_refused(tmp_path: Path, overrides: object) -> None:
    with pytest.raises(LoadoutError, match="overrides"):
        parse_sources([{"name": "local", "path": ".", "overrides": overrides}], tmp_path)


def test_override_requires_the_category_in_use(tmp_path: Path) -> None:
    with pytest.raises(LoadoutError, match="participate in use"):
        parse_sources(
            [
                {
                    "name": "local",
                    "path": ".",
                    "use": ["instructions"],
                    "overrides": {"skills": ["review"]},
                }
            ],
            tmp_path,
        )
