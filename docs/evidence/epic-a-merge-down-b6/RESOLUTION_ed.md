# Bundle 6 merge-down (beta 141c8031 → phase2/epic-a 7b4a6eaf): resolution record

Author of the epic-side work: ed (Security), 2026-10-02. 0b created the branch
`task/epic-a-merge-down-b6` and committed the merge; ed adapts and gates; v2 verifies.
Merge base: 74bfc8d3. Bundle 6 = H-94, H-88/H-93/H-81, H-85, H-99.

## The merge commit, 748d0323 (0b)

No conflicts. `git show --remerge-diff` is empty: the commit holds nothing that is on neither
parent. So there is no resolution to rule on; what follows is what the epic's behaviour
required afterwards, as separate commits.

Files changed on both sides since the merge base, all auto-merged. For each, every line
either side added is present in the merge (checked by script):

| File | Epic since base | Beta since base |
|---|---|---|
| `billing/license_service.py` | +191/-35 | +134/-27 (H-88's grant anchor, H-85's neutral refusal) |
| `billing/models.py` | +98/-0 | +12/-0 (H-88's `grant_anchor_at`) |
| `billing/services.py` | +33/-9 | +22/-0 |
| `classrooms/serializers.py` | +10/-13 | +59/-19 (H-99) |
| `classrooms/services/enrollment.py` | +13/-3 | +42/-0 (H-99) |

The 14 files only beta changed are byte-identical to beta's in the merge. (The b5 record's
line check covered only the both-sides files and missed a renamed one; this one covers both
sets.)

Kept on the epic side and unchanged by the merge (0b checked): `assignments/tasks.py`,
`students/tests_h38_tasks_namespace.py`, `classrooms/tests_h71_student_add_role.py`,
`classrooms/tests_security_penetration.py`, `AutoGrader/tests_beat_locks.py`,
`audit/tests_retention_sweep.py`, `audit/tasks.py`.

## Epic-side adaptation: H-99's refusal is H-71's refusal (SM pre-ruling)

On the epic a refused caller-supplied `@student.local` address carries **no reason code of its
own**. It is the same refusal as H-71's, with the same sentence, so on each route it takes what
the epic already uses for a staff address; a refused placeholder address must be
indistinguishable from a refused staff address, which is also the privacy-correct behaviour.
The catalogue stays closed.

What that needed, test-only, in `classrooms/tests_h99_placeholder_email.py`:

- `test_a_bulk_row_refuses_it` takes S7d's row form: `reason_code` `ROW_STAFF_EMAIL` and
  "Row 1: this email can't be added as a student." (beta pins the rowless text). The
  production code needed no change: the epic's `ENROLLMENT_REFUSAL_CODES` already maps
  `NOT_A_STUDENT_MESSAGE` to that code, and H-99's refusal raises that message.
- Three new tests, one per route (single add, direct add, a bulk row): the answer for the
  placeholder address (the existing pupil's, and one that belongs to nobody) equals the answer
  for a staff address in status and body, apart from the per-response support reference and
  the address the caller sent, where a route echoes it.

Reproduce-first: H-99's test module exactly as merged, run on the epic's code, fails on the
bulk-row test (step 0 of the gate).

**A new intentional divergence from beta**: `classrooms/tests_h99_placeholder_email.py`
(the bulk-row assertion and the three epic-only tests). Future merge-downs keep the epic's
version, with the others listed in the b5 record.

## Cross-side checks before the gate (static, on the merged tree)

- **The epic's `check_no_pii_in_logs` hook** (SM: tell me what it flags): nothing new.
  `billing/license_service.py`, `billing/refresh_timing.py`, `classrooms/serializers.py`,
  `classrooms/services/enrollment.py` and `classrooms/services/roster_import.py`: 0 flagged
  calls each, and none is in the baseline. `billing/services.py`: 17 flagged, already in the
  baseline, the same count as after merge-down b5. Nothing was fixed or baselined. 0b's merge
  commit passed the hook.
- **Migration**: billing `0073_schoolcreditallocation_grant_anchor_at` depends on
  `0072_license_stripe_mutation_intent` and nothing else depends on 0072: one leaf.
  `makemigrations --check` is step 1a of the gate.
- **Beat entries**: identical on the epic and the merged tree, so the guarded-task pin
  (beta's 21 plus the epic's two audit sweeps) needs no change.
- **The epic's no-email bulk path** still calls `DirectAddStudentSerializer` and reports any
  exception from `save()` as `ROW_FAILED`, so H-99's every-attempt-collides case holds there.
- **H-99's nine mutant anchors** all occur exactly once in the epic's versions of
  `classrooms/serializers.py` and `enrollment.py`: the same mutants run in the gate against the
  epic's code, plus four on the adaptation (E1–E4).
- Not checkable by reading, so in the gate's first step: the epic's reason-code,
  error-message, route-coverage and history guards against beta's new code; H-94's rewritten
  hygiene tests on the epic's runner; H-88's tests against the epic's licence code.

## Apps whose production code the merge changes on the epic

`billing` (7 files, one of them the migration) and `classrooms` (3). The regression I asked
for is billing + classrooms + users + AutoGrader + dashboard + audit: the two changed apps;
users and dashboard because their tests reach the add routes (H-99's caller grep); AutoGrader
because H-94 rewrites one of its test modules and the guards live there; audit because the
epic's audit tests exercise the licence paths H-88 and H-85 change.

## Runs

@@RUNS@@
