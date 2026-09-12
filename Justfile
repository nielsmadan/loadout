[private]
default:
    @just --list

# Prepare this checkout for work: dependencies, hooks, then verify.
setup:
    @uv sync
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
    @uv run pytest -q

lint:
    @uv run ruff check

format:
    @uv run ruff format

typecheck:
    @uv run mypy

docs:
    @uv run --group docs zensical serve -f mkdocs.yml

docs-build:
    @uv run --group docs zensical build -f mkdocs.yml --strict

# Everything CI runs.
check:
    @uv run ruff check
    @uv run ruff format --check
    @uv run mypy
    @uv run pytest -q
