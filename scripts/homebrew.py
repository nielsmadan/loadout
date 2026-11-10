import argparse
import json
import re
import tomllib
from pathlib import Path

RELEASE_VERSION = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")
FORMULA_URL = re.compile(
    r'^  url "https://github\.com/nielsmadan/loadout/archive/refs/tags/v([^"/]+)\.tar\.gz"$',
    re.MULTILINE,
)


def resources(lock):
    packages = lock["package"]
    pending = ["loadout"]
    visited = set()
    result = []
    while pending:
        name = pending.pop(0)
        if name in visited:
            continue
        visited.add(name)
        matches = [package for package in packages if package["name"] == name]
        if len(matches) != 1:
            raise ValueError(f"Expected one locked package for {name}.")
        package = matches[0]
        for dependency in package.get("dependencies", []):
            if set(dependency) != {"name"}:
                raise ValueError(
                    f"Conditional dependency needs explicit Homebrew handling: {name}."
                )
            pending.append(dependency["name"])
        if name == "loadout":
            continue
        sdist = package.get("sdist", {})
        digest = sdist.get("hash", "").removeprefix("sha256:")
        if not re.fullmatch(r"[a-f0-9]{64}", digest) or not sdist.get("url", "").startswith(
            "https://"
        ):
            raise ValueError(f"Expected a checksummed source archive for {name}.")
        result.append(
            f"  resource {json.dumps(name)} do\n"
            f"    url {json.dumps(sdist['url'])}\n"
            f"    sha256 {json.dumps(digest)}\n"
            "  end\n"
        )
    return "\n".join(result)


def render(version, sha256, root):
    if not RELEASE_VERSION.fullmatch(version):
        raise ValueError("Expected a three-part release version.")
    if not re.fullmatch(r"[a-f0-9]{64}", sha256):
        raise ValueError("Expected a SHA-256 digest for the release archive.")
    lock = tomllib.loads((root / "uv.lock").read_text())
    template = (root / "scripts/loadout.rb.in").read_text()
    return (
        template.replace("@VERSION@", version)
        .replace("@SHA256@", sha256)
        .replace("@RESOURCES@", resources(lock))
    )


def formula_version(content):
    versions = FORMULA_URL.findall(content)
    match = RELEASE_VERSION.fullmatch(versions[0]) if len(versions) == 1 else None
    if match is None:
        raise ValueError("Expected a formula with one Loadout release version.")
    return tuple(int(part) for part in match.groups())


def publish(formula, tap_formula):
    content = formula.read_text()
    release = formula_version(content)
    if tap_formula.exists() and formula_version(tap_formula.read_text()) > release:
        return False
    tap_formula.parent.mkdir(parents=True, exist_ok=True)
    tap_formula.write_text(content)
    return True


def main():
    parser = argparse.ArgumentParser(description="Render and publish Loadout's Homebrew formula.")
    operations = parser.add_subparsers(dest="operation", required=True)
    render_parser = operations.add_parser("render", help="render the formula from uv.lock")
    render_parser.add_argument("version")
    render_parser.add_argument("sha256")
    render_parser.add_argument("output", type=Path)
    publish_parser = operations.add_parser(
        "publish", help="copy a rendered formula into a tap unless the tap holds a newer release"
    )
    publish_parser.add_argument("formula", type=Path)
    publish_parser.add_argument("tap_formula", type=Path)
    args = parser.parse_args()
    try:
        if args.operation == "publish":
            print("updated" if publish(args.formula, args.tap_formula) else "skipped-newer")
            return
        content = render(args.version, args.sha256, Path(__file__).resolve().parent.parent)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(content)
    except (ValueError, OSError) as error:
        parser.exit(1, f"Homebrew preparation stopped: {error}\n")


if __name__ == "__main__":
    main()
