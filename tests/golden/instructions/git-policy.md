## Git Policy

Leave git to the user. Do not run git commands that modify state (`add`, `commit`, `checkout`, `branch`, `merge`, `rebase`, `stash`, etc.) unless the user explicitly asks for that specific operation. A few commands are hard-blocked at the harness level — `git push`, `git branch -d/-D`, `git reset --hard`, `git clean -f*` — because they push to a remote or destroy local work; ask the user to run those manually.

Read-only inspection (`status`, `log`, `diff`, `show`, `branch` listing, etc.) is always fine.

**Do not auto-create a branch when asked to commit.** Some harnesses default to "if on the default branch, branch first" — that default does not apply here. When I ask you to commit, commit onto the current branch (including `main`) as-is. Only create or switch branches if I explicitly ask for it.