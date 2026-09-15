# Changelog

All notable user-facing changes to loadout. While the project is on `0.x` it follows
[Semantic Versioning](https://semver.org) loosely: breaking changes may land in a minor
release and are called out under **Breaking Changes**.

## [0.9.1] - 2026-09-15

### Bug Fixes

- Adopt existing configuration in the installation check

## [0.9.0] - 2026-09-15

### Features

- Bootstrap loadout package
- Load instruction fragments
- Describe global instruction targets
- Render instruction files from fragments
- Write and drift-check generated files
- Add sync and check commands
- Parse source declarations
- Resolve fragment names across sources
- Render instruction files from a manifest
- Add explain command
- Parse permission rules
- Declare permission targets in the manifest
- Render claude mcp policy and pi permissions
- Render codex rules and mcp permissions
- Render antigravity permissions
- Render claude permissions from a base document
- Render opencode permissions from a base document
- Generate permission files from the manifest
- Render project-scope permissions from merged committed and personal tiers
- Add init and harness add commands
- Read the machine config
- Render only the active profile
- Write generated files to their destinations
- Add global scope to the command line
- Make output optional when destinations is non-empty
- Add overwrite guard
- Generate to destinations without a staging copy
- Add a synthetic permission fixture guarded by a shape test
- Add synthetic instruction fragments, manifest and bases
- Add a synthetic project fixture exercising both tiers
- Respect destination dir env vars of agents when generating
- Merge permissions across every source that provides them
- Compose a target's base document from settings fragments
- Declare a profile as its own manifest file
- Stop emitting the superseded tool's paths and name
- Give each agent a preset of where its slices are written
- Declare an agent and its slices instead of naming every target
- Give each slice its own input document
- Compose several of an agent's slices into one file
- Let a slice contribute one key instead of the whole document
- Resolve a fragment through the profile's variants
- Share defaults across agents and swap fragments by declaration
- Add the hooks slice
- Render the hooks slice to claude and codex
- Render skills as trees to every harness
- Read existing permission files back into a source
- Read hook documents back into a source
- Run hooks on opencode and pi through generated adapters
- Render plugin enablement for claude, codex and pi
- Let a source offer templates and a slice resolve a tree
- Hash a template tree by content
- Resolve a template by name, vendored copy first
- Let a project declare templates and record vendored provenance
- Merge a project's templates as the lowest permission tier
- Declare and vendor a template from the command line
- Sync a vendored template, refusing a modified copy
- Report a diverged vendored template from check
- Collect what a render could not honour into one report
- Give opencode its own global instruction document
- Print what a render could not honour from check and sync
- Render instructions at project scope
- Render skills at project scope
- Report the opencode skill race at global scope too
- Let a manifest switch off an automatic slice
- Let the source state a catch-all default
- Parse and render mcp server definitions
- Render mcp server definitions at project scope
- Read mcp server definitions back into a source
- Report an mcp server no policy mentions
- Render mcp server definitions at global scope
- Write codex mcp and plugin config into config.toml directly
- Manage codex settings from a versioned fragment
- Carry a module's own config to the path it reads
- Carry claude module config too
- Add bundled loadout skill
- Write claude mcp servers into claude.json directly
- Accept what sync last wrote as a third variant
- Remove a multi-line owned key instead of corrupting the file
- Own a key in order to remove it
- Carry OpenCode plugin files through module-config
- Support native artifact routes
- Guard native artifact deployments
- Plan native configuration migrations
- Apply recoverable configuration migrations
- Guide configuration adoption
- Bundle project starters
- Add guarded git integration
- Support nested codex defaults
- Add shared template catalogs
- Add explicit composition operators
- Add documentation site
- Add release automation
- Add Factory Droid harness support
- Report the installed version

### Bug Fixes

- Read and write files as explicit utf-8
- Add exit code 4 for internal errors
- Reject malformed manifests
- Restore manifest path in instructions and permissions table errors
- Raise on a corrupt preserved-key output file instead of silently discarding it
- Reject bases and preserve keys that reinstate read-own-output, exit 3 on user data errors
- Emit the bare command form in project pi permissions
- Stop claude project output colliding with runtime settings
- Preserve foreign keys in multi-purpose project outputs
- Render project scope from sync and check
- Correct project scope cli and scaffold defects
- Correct the claude hook event count and how it was established
- Track the expected output a global gitignore was hiding
- Widen the renderer union so a value renderer type-checks
- Narrow an unrecognised-event claim one file cannot support
- Keep a quoted argument intact when reading codex project rules
- Guard copied files against being overwritten by sync
- State what a generated file does instead of forbidding edits
- Stop a reworded banner reading as a hand edit
- Stop a banner-only difference reading as a hand edit
- Keep the final newline when rewriting a declared key
- Stop harness add from discarding the rest of the project config
- Refuse to sync a vendored template with no recorded provenance
- Refuse two project slices writing one path
- Print notices under a non-utf8 locale
- Write the full diff when an aborted sync truncates it
- Read marketplaces only from a plugins fragment
- Adopt root-level global manifest
- Preserve pi changelog cursor
- Let a merged servers slice write when the last server is removed
- Degrade rather than fail when the record cannot be written
- Record the keys a slice owns rather than its fragment's
- Preserve large configuration migrations
- Harden configuration adoption
- Record effective copied output modes
- Minimize initialization artifacts
- Point release checks at the renamed docs recipe
- Drop the GitHub CLI from the release flow

[0.9.1]: https://github.com/nielsmadan/loadout/compare/v0.9.0..v0.9.1
[0.9.0]: https://github.com/nielsmadan/loadout/tree/v0.9.0

