import argparse
import re
import tomllib
from pathlib import Path


def prepare(version, *, check=False):
    if not re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)", version):
        raise ValueError("Expected a three-part release version.")
    replacements = {}
    for filename, field in [
        ("pyproject.toml", "version"),
        ("src/loadout/__init__.py", "__version__"),
    ]:
        path = Path(filename)
        content = path.read_text()
        pattern = rf'^{field} = "([^"]+)"$'
        matches = re.findall(pattern, content, flags=re.MULTILINE)
        if len(matches) != 1:
            raise ValueError(f"Expected one {field} in {filename}.")
        if check and matches[0] != version:
            raise ValueError(f"{filename} has version {matches[0]}, expected {version}.")
        replacements[path] = re.sub(pattern, f'{field} = "{version}"', content, flags=re.MULTILINE)
    if check:
        lock = tomllib.loads(Path("uv.lock").read_text())
        versions = [item["version"] for item in lock["package"] if item["name"] == "loadout"]
        if versions != [version]:
            raise ValueError(f"uv.lock must contain loadout version {version}.")
        return
    for path, content in replacements.items():
        path.write_text(content)


def main():
    parser = argparse.ArgumentParser(description="Prepare or verify Loadout's release version.")
    parser.add_argument("version")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    try:
        prepare(args.version, check=args.check)
    except (ValueError, OSError) as error:
        parser.exit(1, f"Release preparation stopped: {error}\n")


if __name__ == "__main__":
    main()
