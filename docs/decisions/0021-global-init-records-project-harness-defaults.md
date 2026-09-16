# 0021 — Global init records project harness defaults

**Status:** accepted (2026-09-15). Extends
[0010](0010-a-machine-config-locates-the-global-source.md)'s machine-config schema.

## Context

Project init needs an explicit harness set whenever existing project files do not establish
membership. Repeating the same `--harness` options for every new repository is machine-local
ceremony. Inferring membership from shared skill or instruction paths is unsafe because several
harnesses read them.

ADR 0010 established one machine config for the global source and active profile. Harness
preferences have the same ownership: they vary by machine and belong to no project or global
source.

## Decision

The machine config accepts an optional non-empty `harnesses` list of distinct supported names.
For fresh project init, precedence is:

1. explicit `--harness` options;
2. the machine config's `harnesses`;
3. project discovery.

An initialized project always uses its own config. Global init does not consume the machine
default; after resolving its own harness membership, it records that list in the machine config
alongside `source`. A matching registration preserves its profile and comments while updating the
list. `--registration keep` leaves the machine config unchanged.

## Consequences

- A reviewed global init also establishes defaults for later project init.
- Project-only setups still pass `--harness`; `source` remains required in machine config.
- Dry-run previews include the registration write but do not apply it.
- Shared files still do not establish harness membership on their own.
