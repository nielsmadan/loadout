[private]
default:
    @just --list

git_cliff := "git-cliff@2.13.1"

# Prepare this checkout for work: dependencies, hooks, then verify.
setup:
    @uv sync
    @lefthook install
    @just doctor

# Verify the tools and checkout state this repo needs.
doctor:
    #!/usr/bin/env bash
    set -uo pipefail
    fail=0
    need() {
        if command -v "$1" >/dev/null 2>&1; then
            printf '  ok       %s\n' "$1"
        else
            printf '  MISSING  %-12s install: %s\n' "$1" "$2"; fail=1
        fi
    }
    need uv "https://docs.astral.sh/uv/getting-started/installation/"
    [ "$fail" -eq 0 ] && printf 'Everything in place.\n'
    exit $fail

install:
    @uv tool install --reinstall --force .

install-editable:
    @uv tool install --reinstall --force --editable .

uninstall:
    @uv tool uninstall loadout

test:
    @uv run pytest -q -m "not integration and not migration_integration"

test-unit:
    @uv run pytest -q -m "not integration and not migration_integration"

test-integration:
    @uv run pytest -q -m "integration and not migration_integration"

test-migration:
    @uv run pytest -q -m migration_integration

test-all:
    @uv run pytest -q

lint:
    @uv run ruff check

format:
    @uv run ruff format

typecheck:
    @uv run mypy

serve-docs:
    @uv run --group docs zensical serve -f mkdocs.yml

build-docs:
    @uv run --group docs zensical build -f mkdocs.yml --strict

build:
    @uv build

changelog:
    @uvx {{git_cliff}} -o CHANGELOG.md

[positional-arguments]
release *args:
    python3 scripts/release.py "$@"

# Unit checks for local work.
check:
    @uv run ruff check
    @uv run ruff format --check
    @uv run mypy
    @uv run pytest -q -m "not integration and not migration_integration"
