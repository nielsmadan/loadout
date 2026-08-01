## Git Policy

This machine runs autonomous dev tasks. You may run state-changing git commands (`add`, `commit`, `branch`, `checkout`, `merge`, `rebase`, `push`, etc.) as needed to complete the task, without asking first. Prefer doing the work on a branch rather than the default branch, and keep history clean per the commit policy below.

Still exercise judgement on irreversible operations that affect shared work: don't force-push a shared branch, delete a remote branch, or `git reset --hard` / `git clean -f` away uncommitted work that isn't yours to discard, unless the task clearly calls for it.

Read-only inspection (`status`, `log`, `diff`, `show`, `branch` listing, etc.) is always fine.