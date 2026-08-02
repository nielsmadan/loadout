# loadout

One source of truth for AI coding-agent configuration, rendered out to every harness.

## Install

    just install

## Use

    loadout sync            # regenerate generated files under the current repo
    loadout check           # exit 1 if any generated file has drifted

## Exit codes

| Code | Meaning |
| ---- | ------- |
| 0 | clean — nothing to do, or drift check found no differences |
| 1 | drift — generated files are out of date, run `loadout sync` |
| 2 | usage error — invalid or missing command-line arguments |
| 3 | source error — a fragment or fragments directory is missing |
| 4 | internal error — an unexpected exception; a traceback is printed to stderr |
