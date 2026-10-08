# Verification: task/worktree-base-ref @ cd4e8ae (bundle 4 tooling)

**Verifier:** Verification Engineer 2 (v2). **Author:** 1a. **Date:** 2026-09-30. One commit off beta abeda10: `scripts/task-worktree.sh`, `docs/ops/parallel-sessions.md`, `docs/evidence/worktree-base-ref/SANDBOX.md`. No Python changed, so there are no test modules (rule 15: nothing to run under Django).

**Verdict: VERIFIED.**

## Static
- `new <task> [<base-ref>]`: argc is 2 or 3, otherwise usage (exit 64). `remove` is unchanged (exactly 2).
- The base ref is validated with `git rev-parse --verify --quiet "$BASE^{commit}"` **before** `git worktree add`, so an unknown ref leaves no directory and no branch. It is always quoted.
- Without a base, `git worktree add -b "$BRANCH" "$DIR"` is byte-identical to before.
- The usage range grew from 2–8 to 2–11 to include the new example line and the "branches from HEAD unless a base ref is given" note. The completion message shows `(from ${BASE:-HEAD})`. Under `set -u`, `BASE` is safe on every path (`${3:-}` / `${BASE:-}`).

## Independent sandbox (v2's own, separate from the author's SANDBOX.md)
A throwaway `git clone --shared` of the repo in v2's scratchpad, checked out at cd4e8ae, with a local branch `epicbase` → abeda10. It was deleted afterwards; the real repo's worktree and branch lists were unchanged (0 `wtb-*` entries).

| Case | Result |
|---|---|
| `new wtb-default` | rc 0; the branch starts at HEAD (cd4e8ae = cd4e8ae); message "(from HEAD)" |
| `new wtb-based epicbase` | rc 0; the branch starts at abeda10; message "(from epicbase)" |
| `new wtb-unknown no-such-ref` | **rc 64** "unknown base ref"; no directory; no `task/wtb-unknown` branch |
| `new wtb-dash --orphan` (an option-shaped ref) | **rc 64** "unknown base ref: --orphan"; no branch; it is not passed to git as an option |
| `new Bad_Name epicbase` | rc 64 (the kebab-case check, unchanged) |
| `new wtb-toomany epicbase extra` | rc 64 (usage) |
