# H-237: rebase onto beta + regression verification (fixes-coordinator)

Scope of this doc: this is NOT a from-scratch build evidence doc (the code and
its own unit tests were already written and committed as `8497d70a`/`c20a1355`
by the time this task started). This covers what the fixes-coordinator role
was asked to do: confirm why the branch wasn't landable, rebase it onto
current beta, and re-verify nothing broke. It does not re-run or redo the
original author's Gate 1 design work.

## 1. Diagnosis (independently confirmed, not taken on the Senior Manager's word)

- Branch tip before this task: `c20a1355`, parent chain `8497d70a` -> `7f70305a`
  (H-235's evidence commit).
- `git merge-base task/h237-at-risk-due-work-only eca3d034` = `7f70305a` —
  confirms the branch was cut before Batch 21 (`b57b7a92`/`eca3d034`) landed.
- `git diff --stat 7f70305a..eca3d034`: 132 files, +6622/-88, spanning the
  `epic-i-ai-call-record-2` work (`ai_processor/*`, new `students/migrations`
  0032-0034, `students/models.py`, `students/services.py`, reading-label
  columns) plus Batch 20/21 evidence.
- `git diff --stat 7f70305a..c20a1355` (H-237's own changes): 7 files —
  `AutoGrader/tests_cache_invalidation_coverage.py`, `assignments/models.py`,
  `dashboard/services.py`, `dashboard/tests_h14_at_risk_equivalence.py`,
  `dashboard/tests_h237_at_risk_counts_due_work.py`, `dashboard/views.py`,
  `students/serializers.py`.
- **No file-level overlap** between the two sets — diagnosis of "beta moved
  on substantially" is correct, but the two change-sets don't touch the same
  files, so a straight rebase was in fact low-risk (confirmed below: it had
  zero conflicts).

## 2. Rebase

`git rebase eca3d034` from `c20a1355` completed with **zero conflicts**:

| Old SHA | New SHA | Title |
|---|---|---|
| `8497d70a` | `b50c5d0a` | H-237 tests: at_risk counts only submissions to published, due work |
| `c20a1355` | `7a01d13f` | H-237: at_risk counts only submissions to published, due work |

Confirmed content-preserving, not just conflict-free: diffed H-237's own
touched files (`7f70305a..c20a1355` vs `eca3d034..7a01d13f`) — the only
differences are line-number shifts and blob hashes from Batch 21 inserting
lines earlier in `dashboard/views.py`; the actual added/removed text is
byte-identical.

New branch tip: **`7a01d13f`** on top of beta `eca3d034`.

## 3. Regression

Worktree: `/home/bond-servant-in-training/Documents/Projects/Grade-Automator-Plus-h237-at-risk-due-work-only`, `settings_worktree` (`test_h237_at_risk_due_work_only`, isolated per `scripts/task-worktree.sh`).

- **Targeted** (`dashboard.tests_h237_at_risk_counts_due_work`,
  `dashboard.tests_h14_at_risk_equivalence`, `dashboard.tests_h233_at_risk_values`):
  30/30 OK.
- **Full affected-app regression** (`dashboard classrooms students assignments AutoGrader`):
  **3229 tests, OK (skipped=16)**, 716s. Ran in background due to length;
  full output confirms `OK (skipped=16)` with no failures/errors.
- **Cache-invalidation coverage** (`AutoGrader.tests_cache_invalidation_coverage`):
  **20/20 OK** — confirms the four `FAMILIES` entries H-237 renamed with the
  `:risk1` token (`schooladmins:...:view__summary:...`,
  `teacheradmins:...:view__overview`, `...:view__students:...`,
  `...:view__all_students:...`) are correctly registered; no unregistered
  cache key gap (the class of bug that caused the 2026-10-13 CI-red incident).
- **pre-commit** on all 7 changed files: all hooks passed (black, mypy,
  flake8, isort, bandit, detect-secrets, the custom PII-logging guard).
- **drf-spectacular schema** (`--fail-on-warn`): 68 warnings (53 unique) / 33
  errors (8 unique) on `7a01d13f` — ran the identical command against plain
  `eca3d034` (no H-237 changes) and got the **exact same counts**, confirming
  this is the pre-existing baseline and H-237 introduces nothing new.

Test DB `test_h237_at_risk_due_work_only` dropped after the run (via
psycopg2 — the configured Postgres password contains `@`, which breaks a
plain libpq URL/`psql` invocation).

## 4. What this doc does NOT cover (flagging, not hiding)

The original H-237 commit (`c20a1355`/`8497d70a`) has no `docs/evidence/`
entry of its own and no recorded mutation chain, unlike its siblings in the
same at-risk series (H-233, H-234, H-235 each have a mutation chain under
`docs/evidence/`). This task's brief was rebase + regression, not a from-
scratch Gate 1-10 build — I have not run mutation testing, concurrency,
adversarial, or any of the other gates against H-237's logic itself, and I'm
not presenting the above as if it covers them. Flagging for the Senior
Manager to decide whether a mutation chain is wanted before landing, given
the series precedent.

## Tree state

Branch `task/h237-at-risk-due-work-only` at `7a01d13f`, worktree clean,
nothing uncommitted besides this evidence file (about to be committed).
