# Verification: H-99, a student's placeholder address never attaches an existing account (ed)

- **Branch:** task/h99-placeholder-email at **b910034e**, on task/beta-batch-6 76cc9b97 (0b's base update dbc65451). Beta line, bundle 6. HIGH.
- **Commits:** c4e008ff (production: classrooms/services/enrollment.py, classrooms/serializers.py, classrooms/services/__init__.py), c2a98fd1 (tests). 80267b89..b910034e is evidence only.
- **Verifier:** v2 (independent), 2026-10-02.
- **Verdict:** **VERIFIED-WITH-NOTES**

## The defect and the fix, as read
- **Before:** a student added with no email got `first.last<N>@student.local`, with N one of 10,000 values. The serializer then looked that address up and attached whatever account already had it: a same-school or no-school account silently, another school's account refused.
- **After:**
  - A generated address is `first.last.<16 hex>@student.local` (64 random bits) and is **only ever created**. `DirectAddStudentSerializer.create` looks an address up only when the caller supplied one.
  - `_create_placeholder_student` skips an address already in use. Its insert runs in its own savepoint, so a refusal by the unique `email` column is retried with a new address. Five attempts, then the views' existing fallback sentence.
  - An address in the placeholder domain **supplied by a caller is refused** with H-71's neutral sentence, before any lookup: in both add forms' `validate_email` and in `enroll_student_by_email`.

## Static checks
| Check | Result |
|---|---|
| Base update dbc65451: `git show --remerge-diff` | Empty; 0 added lines lost |
| Every production site that creates an enrolment | Three: the direct-add serializer, `enroll_student_by_email`, the roster import's name-match attach (by name, H-38-scoped). None attaches by a placeholder address now |
| Callers of `enroll_student_by_email` | Two: the single-add view and the roster import. No student-side flow |
| No existence oracle | Each refusal comes before the lookup, with the same sentence whether or not an account has the address |
| Old and new forms | `is_placeholder_email` is by domain, after strip and lower: old four-digit addresses and new ones are treated alike. A sub-domain (`@sub.student.local`) is not a placeholder |
| The retry inside a transaction | The insert's own `transaction.atomic()` is a savepoint inside `create()`'s transaction, so the IntegrityError does not poison it |
| Logs | The one new line carries the course id only |
| Rule 14 | No bare mock reaches a response: the generator and lookup stand-ins return real strings and accounts |

## ed's gates (read, not repeated: rule 15)
| Gate | Tip | Result |
|---|---|---|
| Reproduce-first on 76cc9b97's production files | 80267b89 | FAILED at import (the weak form, disclosed). The strong reproduction is the pinned-suffix probe on the pre-fix code, plus mutant P1 |
| The new module + 21 caller modules + the batch's guards | 80267b89 | **602 OK** |
| Mutants (9) | 80267b89 | **9 of 9 killed** |
| ONE regression: classrooms, users, dashboard, AutoGrader (the SM's scope) | 4ed59060 | **1794 OK** (skipped=6) |

- **Full logs, hashes recomputed by v2:** the gate `317374e7b53b3430`; the regression `d1c5711ade21d3ec`. No FAIL or ERROR header in either.
- **Rule 17:** mutate.py sets `PYTHONDONTWRITEBYTECODE=1` and deletes `__pycache__` before each mutant and after each restore; EVIDENCE.md states both.
- **Tests grepped for the old behaviour:** ed's grep_callers.txt finds no existing test that passes a placeholder address into an add route, a form or the service. v2's own grep at pre-review found the same.

## v2 run (0b's grant, one 6G slot, rules 16, 13 and 12, scratch worktree at b910034e, DB test_vf2_s1)
**Rule 17:** every test subprocess ran with `PYTHONDONTWRITEBYTECODE=1`. The `__pycache__` of the mutated modules' directories was deleted before the baseline, before each mutant run and after each restore. Each restore was sha-checked against b910034e.

**Baseline: 20 tests OK** (ed's 16 test methods + the probe's 4). Log: runs/h99_b910034e_baseline.log.

**Probe (tests_vf2_h99_probe.py), 4 OK:**
- **C1, a real concurrent double-add.** Two requests in two threads are handed the same generated address. Each is held at the lookup until both have seen the address free; then both insert. Result: 200 twice, two students with two different addresses, one enrolment each in its own course, and at least three addresses generated (the refused insert was retried). Nobody was attached and nobody got a 5xx. ed's own test simulates this with a blind lookup; this is the case with real threads and a real unique-column refusal.
- **B1:** a bulk file with a placeholder-address row between two good rows: that row is refused with the neutral sentence, the other two are imported, and the existing pupil gains no enrolment.
- **O1:** an old four-digit address typed by the pupil's own teacher gets exactly the answer an address nobody has gets, on all three routes: 400 with the sentence on single add and direct add, a failed row with the sentence on bulk. No account and no enrolment is created.
- **S1:** a sub-domain address is treated as an ordinary address.

**Mutants (vf_h99_mutants.py), each run against ed's module and the probe separately** (runs/h99_b910034e_mutants.log):
| Mutant | ed's tests | v2's probe |
|---|---|---|
| T1 the insert has no savepoint | killed (`test_two_requests_picking_one_token_end_as_two_students`) | killed (C1) |
| T2 the shared service refuses only an unknown placeholder address | killed (2 tests) | killed (B1, O1) |

## Notes (none blocks)
1. **One thing that worked before is gone:** attaching an existing roster-only pupil to another course by typing its placeholder address. The API returns `email: null` for these accounts, so no client can know the address. EVIDENCE.md says so; the SM confirms with QA that no client sends one. Only the backend was read.
2. **Records already merged in production are not repaired.** The fix stops new attaches. A read-only detection query is with the founder; it cannot show two same-named pupils of one teacher merged into one record.
3. **Regression scope (SM ruling):** classrooms, users, dashboard and AutoGrader. students, assignments and billing as a whole were not run for this slice (billing's two H-38 modules are in the 602). The bundle's strict full run covers them.
4. **Merge-down (Epic A):** a refused placeholder bulk row will carry S7d's `ROW_STAFF_EMAIL` code, whose name says "staff". The hunks in classrooms/serializers.py and the roster import differ on the epic; the merge-down needs the epic form.
5. **Older, outside H-99, both with the SM:**
   - the API's masking of placeholder addresses uses a case-sensitive `endswith`, so a legacy address stored with capitals would be shown;
   - users/serializers.py skips the email-domain rules for any address ending @student.local, whatever the user type.
6. **Direct add never reuses an account,** even the teacher's own pupil in another course: it always makes a new one. Unchanged by H-99; the bulk import's name match is the way to reuse a pupil.
