## Code Comments

Default to zero comments. Never write JSDoc-style `/** … */` blocks, multi-paragraph docstrings, or multi-line comment blocks — names and signatures are the documentation. Don't narrate what routine changes do (config flips, version bumps, standard fixes); that rationale belongs in the commit message, not the code. Reserve a single terse `//` line for the genuinely non-obvious: a workaround for a specific bug, a surprising invariant, a "must stay last" ordering. This governs *new* code you write — don't strip a repo's existing comments unless asked.

## Preserve User Edits

When a system-reminder shows the user modified a file (especially "the change was intentional"), treat those edits as load-bearing. When you later edit that file for an unrelated reason, do not reword their comments, rename their variables, or reformat lines they chose to format a certain way. If a refactor genuinely requires changing one of their choices, flag it out loud first — never silently revert it inside a larger edit. When in doubt, keep their version.

## Questions vs. Actions

When the user's message is a question (asking for explanation, comparison, or analysis), respond with text only. Code edits require an explicit imperative ("fix this", "change X", "make Y do Z"). A stop-hook firing on a phrase inside your explanation is not authorization to start editing — at most, rephrase. Confirm the prior turn actually asked for a change before touching files.

## Finishing Tasks

When the user says to finish a task completely, drive it to actual completion — resolve every remaining item (implement it, or make and record an explicit decision) before surfacing what's next. Don't end turns by re-proposing the next phase or asking "want me to move on to X?" while the stated task is unfinished. If a genuine decision is needed to finish, ask that — don't offer to skip ahead.
