[private]
default:
    @just --list

# Install `loadout` onto PATH as an editable tool, so ~/ac can call it.
install:
    @uv tool install --editable . --force

test:
    @uv run pytest -q

lint:
    @uv run ruff check

fmt:
    @uv run ruff format

typecheck:
    @uv run mypy

# Everything CI runs.
check:
    @uv run ruff check
    @uv run ruff format --check
    @uv run mypy
    @uv run pytest -q
