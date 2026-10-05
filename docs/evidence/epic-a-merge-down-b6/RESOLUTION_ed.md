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

## Epic-side adaptation: H-85 lands in the catalogue as TEACHER_CANNOT_JOIN_YET (founder, option B)

**Found by the gate, not by reading.** The first run of step 1, at e917f52a, was red: 922 tests,
2 failures (`run1_failed_e917f52a_modules_and_guards.txt`). Both have one cause. My static
checks above did not predict it, and the merge was textually clean, because beta changed the
exception's *text* while the epic maps the exception's *class* to a catalogue entry.

- `test_the_add_teachers_response_says_nothing_about_a_subscription` (H-85's own test): on the
  epic `teacher_failure()` answered with S7d's `TEACHER_HAS_INDIVIDUAL_SUBSCRIPTION`, so the
  code's name, the message, the remediation and `params.email` all still told the admin that
  the teacher pays for a plan. This is the reproduce-first evidence for the change.
- The sync-only email-code guard matched beta's constant of the same name inside both raises.

**Decision.** The founder chose option B on 2026-10-05 (relayed by the SM): a neutral entry,
and the old code retired outright.

| | |
|---|---|
| Code | `TEACHER_CANNOT_JOIN_YET` |
| Message | "This teacher can't be added to your school yet. Please ask them to contact support." (H-85's sentence) |
| Remediation | "Ask the teacher to contact support." |
| Params | none |
| Retryable | no |
| Status / class | 422 if ever answered directly, USER: an item code like its neighbours |

`TEACHER_HAS_INDIVIDUAL_SUBSCRIPTION` leaves the enum, the catalogue and the sync-only email
set. It is **not** kept as an audit-only code: `SchoolAdminAuditEventListView` shows a school
admin their school's audit events with `reason_code`, so the disclosure would only move. No
audit event carried the code before this change, and none is added. Support has the reason by
user id in H-85's log line beside each raise.

Commits, after the merge and in this order:

- 17e36892, tests first: `billing/tests/test_s7d_licence_teacher_codes.py` (the pinned entry;
  a new test on code, message, remediation, empty params, not retryable, and no telling word
  in the response), `AutoGrader/tests_reason_codes.py` (the catalogue pin; the entry's message
  equals beta's constant; no `TEACHER_` code, label or text mentions a subscription; the
  retired name is not in the enum), `AutoGrader/tests_codederror_serialization.py` (the email
  exception is three codes).
- e8b9242c, production: `billing/license_service.py` (`teacher_failure()`),
  `AutoGrader/reason_codes.py`, `audit/enums.py`. No model field lists the enum, so there is
  no migration (step 1a checks).
- d4b5f8e0, documents: the QA catalogue (an amendment under the approval record; the E2 row),
  the 08a design (three codes, not four), `docs/backend/billing-licenses.md` (with the client
  note).

**The SM's question: does beta's constant name still trip the guard?** No. The guard matches
names against the values of `SYNC_ONLY_EMAIL_CODES`; with the code out of the set,
`TEACHER_HAS_INDIVIDUAL_SUBSCRIPTION` in a raise matches nothing (checked statically with the
guard's own name walk, then by the guard in step 1). Nothing was renamed:
`billing.license_service.TEACHER_HAS_INDIVIDUAL_SUBSCRIPTION` stays byte-identical to beta, and
so do H-85's test module and both raise sites.

**v2's check list.**

- Both raise sites (`_get_or_invite_teacher`, `_enroll_teacher_internal`): untouched; they
  raise beta's constant.
- The catchers at `license_service.py` 829 and 917 (`_invite_and_enroll_one_teacher`, the
  carry-forward): untouched beta code; they put `str(exc)`, the neutral sentence, in `error`.
  The catcher in `add_teachers_batch` calls `teacher_failure()`, the one place changed.
- `AutoGrader/error_messages.py`: untouched; the exception stays in the passthrough list and
  its text is the neutral sentence.
- Remediation and `params.email`: asserted in the new S7d test and pinned in the catalogue.
- The audit reason code of the same name: gone from `audit/enums.py`.
- `SYNC_ONLY_EMAIL_CODES` and its exact-set test: three codes.
- One stale comment is left on purpose: lines 829 and 1779 say the subscription refusal
  "carries the address". True before H-85, harmless now; 829 is beta's line and I kept the
  pair alike rather than diverge from beta for a comment.

**Callers.** `grep_callers_h85.txt`: ten test modules reach the changed mapping, the route or
the exception; all ten are in step 1.

**Mutants H1-H6** (`mutate.py`): the mapping removed; the mapping pointing at
`TEACHER_EMAIL_OTHER_ROLE` (option C); the message saying why; the remediation saying why; the
entry taking an `email` param; the entry retryable.

**Also in 17e36892, v2's pre-read note on afdc54fc:** `assertSameAnswerAsAStaffAddress`
asserted `status >= 200`, which cannot fail. Each route now names its exact refusal status:
400 (single add), 400 (direct add), 200 (a bulk row).

**For the frontend:** in the add-teachers result the per-teacher code
`TEACHER_HAS_INDIVIDUAL_SUBSCRIPTION` is replaced by `TEACHER_CANNOT_JOIN_YET`; its message and
remediation no longer mention a subscription and it has no `email` param.

**A divergence from beta, new:** none in shared files. The three epic test modules above and
the catalogue are epic-only.

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

All runs waited for 0b's GRANT and were released afterwards. Rules 12/13/16 on each (nice,
`timeout -k 60`, a `systemd-run` scope with MemoryMax and no swap, `systemd-inhibit` with
handle-lid-switch); rule 17 on the mutants (`PYTHONDONTWRITEBYTECODE=1`, the mutated modules'
`__pycache__` deleted before each mutant and after each restore). Mutants and the prefix ran on
their own database (`settings_worktree_mut`), dropped afterwards.

| # | When | Tip | What | Result | File |
|---|---|---|---|---|---|
| 1 | 2026-10-02 17:37 | e917f52a | step 0 prefix, 1a, step 1 (6G) | **RED**: 922 run, 2 failures (H-85 on the epic; one cause). Mutants not run | `run1_failed_e917f52a_modules_and_guards.txt` |
| 2 | 2026-10-05 08:13 | 54cc7c1b | step 0 only | **Stopped by my script**, not by the code: its restore check ran over the whole tree and counted the prefix log, tracked since 2409ca36, as a change. The prefix itself ran as intended (16 run, 4 failures). The check now excludes the evidence folder; same command restarted with 0b's word | `run2_stopped_54cc7c1b_prefix_only.txt` |
| 3 | 2026-10-05 08:15-08:30 | 54cc7c1b | step 0 prefix | 16 run, FAILED (failures=4): `test_a_bulk_row_refuses_it`, H-99's module as merged, on the epic's code. Expected | `prefix_h99_test_as_merged_failing.txt` |
| | | | step 1a `makemigrations --check` | exit 0, no changes | `makemigrations_check.txt` |
| | | | step 1: changed + caller modules + both sides' guards (6G) | **Ran 956 tests in 484.235s, OK** | `modules_and_guards.txt` (last 200 lines; full log sha256 08259779d73e85c6…) |
| | | | step 2: 19 mutants | **19 killed, no survivor** (P1, P3-P10, E1-E4, H1-H6) | `mutation_log.txt`, `mutation_results.json` |
| 4 | 2026-10-05 08:31-09:02 | d22dff5a | ONE regression, 12G, `flock`, timeout 3600: billing classrooms users AutoGrader dashboard audit | **Ran 4441 tests in 1823.511s, OK (skipped=8)**. billing 2136, classrooms 434, users 698, AutoGrader 536, dashboard 270, audit 367 | `regression.txt` (last 200 lines; full log sha256 82f25f59b66d2324…) |

d22dff5a is 54cc7c1b plus run logs: `git diff 54cc7c1b d22dff5a -- . ':!docs/evidence'` is empty, so
steps 1 and 2 and the regression gated the same code. The commits after d22dff5a are evidence only.

Run 1's two failures are both green in run 3: H-85's response test, and the sync-only guard.

Not run, and why: the other apps (assignments, students, ai_processor and the rest). The merge changes
production code in billing and classrooms only; 0b's Gate 10 runs the whole suite after the merge.

## The retired code: what still names it, and whether anything stores it (0b's questions)

`grep_retired_code_repo_wide.txt`. In short: the only code hit is beta's H-85 constant of the same
name, which is the neutral sentence and not a reason code; the documents say "retired"; older evidence
is left as written. No audit event and no task row was ever written with the code (read from the code;
staging was not queried), and an old row would still read. The frontend is outside this repo and must
map the new code.
