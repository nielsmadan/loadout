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

# Install `loadout` onto PATH as an editable tool, so ~/ac can call it.
install:
    @uv tool install --editable . --force

test:
    @uv run pytest -q

lint:
    @uv run ruff check

format:
    @uv run ruff format

typecheck:
    @uv run mypy

# Everything CI runs.
check:
    @uv run ruff check
    @uv run ruff format --check
    @uv run mypy
    @uv run pytest -q
