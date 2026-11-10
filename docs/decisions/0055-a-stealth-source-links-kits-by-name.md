# 0055 — A stealth source links kits by name

**Status:** accepted (2026-11-08, ruled by the user). Extends kits
([0053](0053-overlays-become-stealth-sources-and-templates-become-kits.md)) to stealth sources
([0045](0045-an-overlay-source-renders-into-repositories-that-have-not-adopted-loadout.md)).

## Context

A stealth source could not use a kit. Its `loadout.toml` is a global manifest, and kits were a
project feature: a project lists them and a global source offers them. A user setting up a
stealth source for `~/wrksp/haufe/dev*` wanted the `react-native` kit from their global source,
whose instructions, skills and one permission are all slices a stealth source renders, and had to
copy each part into the stealth folder by hand, where they stopped following the kit.

## Decision

- **`kits = [...]` in the `[stealth]` table** links kits by name, and
  `loadout kit add KIT --stealth NAME` writes it. The key sits in `[stealth]` rather than at the
  top level because a stealth manifest opens with that table: a line added below `targets` is
  already inside it, and a top-level key would have to go above it. A top-level `kits` is refused
  with where it belongs.
- **A name resolves as a project's linked kit does**: the `kits/` folders of the machine's global
  sources, then the bundled kits. Resolving through the stealth folder's own `[[source]]` entries
  was the alternative, and would have been more self-contained, but one name would then mean
  different kits in a project and in a stealth source on the same machine.
- **Linked only.** A stealth folder is personal and keeps no copy, so `--vendored` is a usage error
  and a `[kit.NAME]` table is refused.
- **The project tier rules apply.** Kit instructions follow the stealth source's own fragments,
  kit rules are the lowest permission tier, and a same-named server or skill in the stealth folder
  replaces the kit's. Every declared harness takes the kit's parts unless the slice is off.
- **A kit that brings hooks or plugins is refused**, naming the file, for the reason those slices
  are refused in a stealth source ([0045](0045-an-overlay-source-renders-into-repositories-that-have-not-adopted-loadout.md)).

## Consequences

A change to a linked kit reaches every target of every stealth source that links it on the next
sync, and `check` shows it as `source-changed` until then. Removing a name from `kits` retires
what it rendered, through the record, as for any output the source stops producing.
