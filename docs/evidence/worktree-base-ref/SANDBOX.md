# `scripts/task-worktree.sh new <task> [<base-ref>]`: sandbox run

This was run by 1a (grade-automator-plus-c2) on 2026-09-30, in a throwaway `git clone --shared` of the repository in a scratch directory. So no branch or worktree touched the real repository. The sandbox's HEAD was beta `abeda10`, and the base used was `origin/phase2/epic-a` (`9a91431`). The sandbox was deleted afterwards.

| Case | Result |
|---|---|
| 1. `new t-default` (no base) | rc 0. The branch `task/t-default` is at HEAD `abeda10`, as before. `TEST_DB` is `test_t_default` |
| 2. `new t-based origin/phase2/epic-a` | rc 0. The branch is at `9a91431`, and the banner reads "task/t-based (from origin/phase2/epic-a)". `TEST_DB` is `test_t_based`. `.env` is linked when the root has one (the sandbox had none, and the script warned, as before) |
| 3. `new t-bad no-such-ref` | rc 64, "unknown base ref: no-such-ref". **No directory and no branch created** (the ref is checked before `worktree add`) |
| 4. `new t-x a b` (too many arguments) | rc 64, and the usage is printed, now including the base-ref example |
| 5. `remove t-based` / `remove t-default extra` | rc 0 and the branch is kept, as before / rc 64 |

`sh -n` passes. There is no shellcheck on this machine.
