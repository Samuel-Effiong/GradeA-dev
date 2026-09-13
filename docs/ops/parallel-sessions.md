# Running several AI sessions at once

**One worktree per task.** Never two sessions in the same directory.

```sh
./scripts/task-worktree.sh new    cache-invalidation
./scripts/task-worktree.sh list
./scripts/task-worktree.sh remove cache-invalidation
```

---

## Why a shared directory cannot work

Git's **index** (the staging area) and **stash** are one mutable object per
working tree. Two sessions in the same directory share them. This is not a
discipline problem — there is no way to be careful enough.

All three of the following actually happened while two sessions worked in this
repo on 2026-09-12:

**`git commit --amend` swept in another session's work.** A commit that should
have held 48 files held 76. `--amend` commits the *whole index*, and the other
session had `grading/` and `ai_processor/` staged. It was caught by noticing
the file count — nothing else would have flagged it.

**`git reset` wiped another session's staging.** Recoverable only because the
files happened to be fully staged. Had any file been *partly* staged, the
staged-versus-unstaged distinction would have been destroyed with no way to
reconstruct it.

**A hook and the working tree disagreed permanently.** One session staged part
of `AutoGrader/settings.py` to keep another session's line out of its commit.
That made the staged tree one line shorter than the working tree, and
`detect-secrets` — which scans the *staged* tree — reported a secret at line
1165 while the file on disk said 1164. Three commit attempts, never converged.

And the one that can lose work rather than merely confuse:

> `pre-commit` stashes **all** unstaged changes before running hooks and
> restores them afterwards. If a second session is writing to those files in
> the meantime, the restore collides:
>
> ```
> [WARNING] Stashed changes conflicted with hook auto-fixes... Rolling back fixes...
> ```

## What a worktree gives you

Its own index, its own stash, its own `HEAD`, sharing one object store — so no
re-cloning and no disk bloat. Git also refuses to check out one branch in two
worktrees, which forces the separate branches that stop history contention too.

Proven in this repo: with a file staged in a worktree, `git diff --cached` in
the main checkout showed an entirely different file. Fully isolated.

## Partition by TASK, not by app

A task worktree lives as long as the work does, however many apps it touches.

App-based partitioning breaks here because work crosses app boundaries: the
cache-invalidation effort spans `AutoGrader/`, `dashboard/`, `classrooms/` and
`users/`, and removing the `grading` app was `grading/` **plus one line of
`AutoGrader/settings.py`**. That one line is exactly what tangled the billing
commit — a task worktree would have kept it with its own change.

Session-based partitioning (`session-1`, `session-2`) fails differently: work
outlives a session, so you end up switching branches inside a worktree and the
confusion comes back.

## What the script sets up

| Thing | Why |
|---|---|
| Branch `task/<name>` | Git forbids one branch in two worktrees; this makes that automatic |
| `.env` symlinked | Gitignored, so a fresh worktree has none and Django will not start. Symlinked, not copied, so rotating a secret does not mean hunting down stale copies |
| `settings_worktree.py` | A **unique test database name**. Without it, concurrent test runs truncate each other's tables mid-run and the failures look like real bugs but do not reproduce |
| Shared virtualenv | It lives outside the repo, so there is nothing to install |

Run tests with `--settings=settings_worktree`.

`remove` refuses while the worktree has uncommitted changes. It never decides
what to do with your work.

## Rules that still apply inside a worktree

Isolation removes the cross-session hazards. It does not make these safe:

- **Do not use bare `git stash` / `git stash pop`.** The stash stack is shared
  across all worktrees. Prefer a temporary WIP commit. If you must stash, use
  `git stash push -u -m "<unique-tag>"`, then `git stash apply <sha>` — never
  `pop`, which takes whatever is on top and may be someone else's.
- **Prefer `git commit -F <msg> -- <paths>` over `git add`.** Committing
  explicit paths ignores the index entirely. Two traps, both hit in practice:
  everything after `--` is a pathspec, so `-F -` must come *before* it; and
  the form only works for files git already tracks — a **new** file must be
  `git add`ed first. Add only your own files, then still pass `-- <paths>` so
  the commit cannot pick up whatever else is staged.
- **Avoid `--amend` and `git reset`** unless you have just checked what is
  staged. Both act on the whole index.

## If you are already in a tangle

Do **not** run `git reset` to tidy up — that is what destroys the other
session's staging. Instead:

```sh
git commit -- path/one path/two     # commits only these, ignores the index
```

Then move the remaining work into its own worktree going forward.
