# Changelog

All notable user-facing changes to loadout. From 1.0 it follows
[Semantic Versioning](https://semver.org): breaking changes land only in a major release and
are called out under **Breaking Changes**.

## [0.11.0] - 2026-11-08

### Features

- Rename overlays to stealth sources
- Rename templates to kits
- Add stealth init to start a stealth source

### Bug Fixes

- Refuse 0.10.0 overlay leftovers and edited kits on linking
- Refuse unknown tables in permissions.toml

## [0.10.0] - 2026-11-07

### Features

- Add project harness defaults
- Unify skill catalogs
- Check packaging before a release tag exists
- Group integrations under a single command
- Say what loadout is in its help output
- Replace init scope flags with subcommands
- Maintain the project gitignore on sync
- Fold template vendor into add
- Remove artifact routes
- Seed mcp.toml and a source README on global init
- Add exact-match shell rules
- Drop init project --starter
- Drop init global --force
- Take the init global directory as an argument
- Add shell completions for bash, zsh and fish
- Render and record outputs at the end of init
- Add check --json
- Sync prints only the files it changed
- Check tells a source change from a hand edit
- Skills have one source form, skills/<name>/SKILL.md
- Sync retires outputs it no longer renders
- Init adopts a symlinked deployment by de-linking it
- Init reads only the paths loadout manages
- Remove init's --mapping and --select-source
- A project link outside the repository names its target and remedies
- Init names where files land under a link above a harness root
- The de-link notice says which later additions stay invisible
- A project's CLAUDE.md imports AGENTS.md beside other harnesses
- The guard names /loadout as the way to keep an edit made outside loadout
- Check and sync flag a file loadout did not write in a folder a harness reads whole
- Init leaves a conflicting item in place and reports it
- The loadout skill ports outside changes and merges conflicting copies
- An agents slice renders one source agent to every harness
- Init adopts existing subagents into source agents
- A skill's Claude-only frontmatter keys render into Claude's copy only
- A skill can hand its work to a source agent
- Init adopts opencode.jsonc as opencode.json
- Init backs up what it changes instead of committing and journaling it
- A permission target's rules key is refused in favour of permissions = []
- Every init --json answer carries status and exit_code
- Init adopts a secret as it is and names each source file holding one
- MCP servers carry literal headers, so Codex http_headers survive adoption
- Init puts everything back on failure and offers the loadout skill
- Note a heading two instruction fragments both bring
- Point out guidance a template and the project both hold
- Note a heading two global instruction fragments both bring
- Note that skill install's takeover leaves a render policy to remove
- Carry a switched-off plugin as enabled = false
- Render Pi MCP for its built-in support
- Note a shell catch-all init seeds as ask
- Add overlay sources that render personal config into repositories without loadout

### Bug Fixes

- Keep a written path out of the retirement check
- State the version when the formula builds from a local archive
- Run the packaged skill through its new command path
- Keep the CLI reference inside the published docs
- Guard hand edits when git has no baseline
- Treat a missing permissions.toml as empty
- Gitignore only what sync wrote, skills by folder
- List resolvable templates when one is not found
- Help text states scope; template commands explain outside a project
- A symlinked path prefix no longer reads as an external symlink
- Sync and check delete records of roots that no longer exist
- Check no longer deletes records of vanished roots
- A global source without permissions.toml renders as an empty one
- Adoption reads hand-written Codex rules instead of dropping them
- A trailing catch-all becomes the default and names the entries it shadows
- A permission file with no rule in it is not written
- A project Pi config carries a stated catch-all
- A project Pi config with no rule in it is not written
- Init deletes a Codex rules file once its rules are adopted
- Init merges project permissions and MCP servers that differ across harnesses
- Init no longer reads providers/, profiles/ or a plugin/ folder no harness reads
- A project's own settings are adopted into its source and reach every clone
- A lone OpenCode or Pi deny ending in ' *' is adopted as its prefix
- Get Prerelease install checks passing
- Make restore, project sync and secret checks safe against links and partial failures
- Refuse crafted Codex rules, correct init's readiness list and remove dead init code
- Keep project sync and templates inside the project and make restore and check --staged robust
- Render global scope only from this machine's registered source
- Guard shared documents key by key, gate commands on registration and protect owner-only files
- Run git without hook variables, keep output modes and handle non-UTF-8 documents
- Run without git on PATH, guard shared hooks folders and sweep only loadout's temporary files
- Bound the staged link walk, guard external hook advice and name unreadable configs
- Never diff a file that holds a secret
- Stop flagging folders and files the harnesses write themselves
- Refuse a skill or agent named like one a harness writes itself
- Refuse a link loop on Linux, where it reports ELOOP (#2)
- Adopt the shared skills folder only for harnesses loadout renders it for
- Leave entries that are no skill in place and flag only what a harness loads
- Replace a project skills folder linked inside the repository with a real folder
- Count OpenCode as a reader of Claude's skills folders
- Put a project's own instructions before its templates'
- Stop reading prose about a bearer as a bearer token
- Point the template hint at manifests and at bare template add
- Name the adopted files init removed from the index
- Name a project's Claude local settings as kept in place
- Read a skill linked to the same skill in another skills folder once
- Warn when a generated path is still tracked
- Adopt a skill copied identically into a folder loadout renders as one shared copy
- Name the unparsable document in init's refusal
- Refuse an init that would drop a permission rule
- Let skill install take over an unowned copy identical to the bundled skill
- Stop the bundled skill's own text reading as a secret
- Stop the init preview calling agents and project permissions unsupported
- Stop reporting a root file in .agents/skills that no harness loads
- Name --harness when init needs the harnesses chosen
- Exit 130 without a traceback when Ctrl-C stops init
- Describe a project de-link without a link back
- Say where Codex's approval policy is set in the catch-all notice
- Expand ~ in a source path
- Let the source init global writes admit every category
- Let sync --force replace an unparsable project settings file
- List an adopted skill once in .gitignore
- Note Pi settings that no plugins declaration lets render
- Suggest git hooks only when init ran in a repository
- Describe the source layout correctly in the README init writes
- Word the sync abort for a diff withheld over a secret
- Name a refused preserve key once and reject preserve under codex
- Write Droid's hooks.json keyed by event so Droid runs its hooks
- Name Droid's legacy project hooks file in the init preview
- Move Droid's settings.json hooks into the hooks slice at init
- Refuse an init whose dropped settings would change what a harness does
- Stop reading a defaulted bearer reference as an auth variable name
- Name OpenCode MCP rules the way OpenCode names their tools
- Refuse an MCP rule that reaches another server's tools
- Report render's own refusal when init validates its source
- Adopt Codex's own settings into defaults at init
- Initialise the project folder init project --root names
- Stop calling an MCP server no policy names all denied
- Refuse an init whose source would share the other scope's source
- Name a text file that is not UTF-8 instead of crashing on it
- Refuse Pi settings without plugins before sync retires them
- Name a folder loadout cannot write into instead of crashing
- Report a machine config that does not parse at init global
- Say git is not on PATH when a command needs it
- Refuse an unreadable machine config at init global with one ending
- Exit 130 without a traceback when Ctrl-C stops any command
- Leave an interrupted sync for the next sync to finish
- Report a Ctrl-C during init by the phase it arrives in
- Read a symlinked machine config at init global without writing through it
- Name unwritable skill folders and print refusals after the preview
- Leave dot entries in a skills folder alone at init
- Name the skill folder that holds SKILL.md only below its top
- Offer only integrate's own subcommands in zsh completion
- Refuse a Pi rule in a shape init cannot read instead of crashing
- Leave Pi's universal fallback to the permission extension
- Refuse Pi permission keys init cannot carry
- Report every refusal in an original in one init run
- Write droid files in the form Droid rewrites them to
- Take Claude Code's lock when sync writes ~/.claude.json
- Take Pi's lock when sync writes its settings.json
- Correct the template commands' --root and folder wording
- Recognise git 2.56's wording for a broken .git file (#4)

## [0.9.3] - 2026-09-16

### Bug Fixes

- Generate release notes without the GitHub API

## [0.9.2] - 2026-09-15

### Bug Fixes

- Give the formula test its own project directory

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

[0.11.0]: https://github.com/nielsmadan/loadout/compare/v0.10.0..v0.11.0
[0.10.0]: https://github.com/nielsmadan/loadout/compare/v0.9.3..v0.10.0
[0.9.3]: https://github.com/nielsmadan/loadout/compare/v0.9.2..v0.9.3
[0.9.2]: https://github.com/nielsmadan/loadout/compare/v0.9.1..v0.9.2
[0.9.1]: https://github.com/nielsmadan/loadout/compare/v0.9.0..v0.9.1
[0.9.0]: https://github.com/nielsmadan/loadout/tree/v0.9.0

