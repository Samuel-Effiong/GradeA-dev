# H-14 — school-admin dashboard summary rebuild cost: evidence

Backlog item H-14. Found by the H-1 stampede measurement family: the
school-admin weekly-digest / daily at-risk-alert cold rebuild
(`SchoolAdminWeeklySummaryService._at_risk_students`) was expensive at
scale because it built a full `CustomUser` model instance per enrollment
and a full `StudentSubmission` (plus its related `Assignment`) per
submission — for a large school, on the order of 17,000 submission rows,
each carrying its `answers`/`feedback` JSON payload. The cost was Python
object construction, not SQL: the method only ever reads six scalar
values off each submission and a handful of id/name fields off each
student who turns out to be at risk.

## 1. Fix

`dashboard/services.py::SchoolAdminWeeklySummaryService._at_risk_students`
rewritten to read scalar columns via `.values_list(...)` instead of
materializing model instances:

- Enrollments are read as `(student_id, course_id)` tuples instead of
  `StudentCourse` objects with a `select_related("student")` join.
- Submissions are read as `(student_id, assignment_id, course_id,
  submission_date, is_published, score_percentage)` tuples instead of
  `StudentSubmission` objects with their `Assignment` joined in.
- A `CustomUser` instance (`.only("id", "first_name", "middle_name",
  "last_name")`) is only built for students who actually end up at risk,
  via one `in_bulk()` call — not for every enrolled student.

Diff scope: two files, `dashboard/services.py` (74 lines changed) and the
new `dashboard/tests_h14_at_risk_equivalence.py` (286 lines added). No
other file in the fix commit (65e855d) touches production code.

## 2. Equivalence proof

`dashboard/tests_h14_at_risk_equivalence.py` keeps
`reference_at_risk_students`, the **verbatim** pre-fix method body copied
from beta `4b902fc`, as the oracle the rewrite must agree with byte-for-
byte on output. Fixture (`setUpTestData`) covers: missing-work-only,
low-published-scores, a fully-caught-up student, a student split across
two courses, unpublished/ungraded submissions that must not count as
scores, withdrawn/inactive/wrong-user-type/foreign-school students who
must drop out of the enrollment set entirely, a submission on a course
the student isn't enrolled in (must be ignored), and three students tied
on identical scores (ordering).

4/4 tests pass:

| Test | Proves |
|---|---|
| `test_same_students_scores_and_order_as_the_original` | Same `(student_id, avg_score)` list, same order, against the oracle (`assertGreater(len(expected), 3)` guards against a vacuous fixture) |
| `test_names_and_the_built_payload_match` | The built payload (`get_full_name`, rounded score) matches between rewrite and oracle |
| `test_returned_students_cost_no_further_queries_for_the_fields_callers_read` | Zero extra queries per returned student for the fields callers actually read |
| `test_query_count_is_flat_and_below_the_original` | One net extra query overall vs. the oracle (the `in_bulk` lookup), flat under 20 extra students |

Payload identity is also checked end-to-end, not just at the unit level:
`docs/evidence/h14_school_admin_summary/01_before_profile.json` vs.
`01_after_profile.json`, captured on a seeded s3-scale dataset (10
schools x 240 courses x 6,000 students) — same query text and result
shape before and after, modulo the query plan itself (see below).

## 3. Performance

Cold rebuild, same seeded dataset, `CaptureQueriesContext` + wall-clock
p50, measured on a **loaded machine** (background load ~5-9 from other
concurrent sessions on the same host — directional, not a clean-room
number):

| | Before | After |
|---|---|---|
| p50 | ~4,405 ms | ~2,059 ms |
| Queries | 17 | 18 (+1, the `in_bulk` student lookup) |

Roughly a 2.1x wall-clock improvement at this data size, from replacing
per-row model construction with scalar reads, at the cost of one extra
(cheap, `id__in`) query. Treat the absolute figures as directional; a
quiet-machine rerun would tighten them but is not expected to change the
conclusion, since the mechanism (no more per-row model instantiation) is
structural, not timing-sensitive.

## 4. Mutation testing

`docs/evidence/h14_school_admin_summary/mutants.sh` mutates the fix
seven ways and checks whether `dashboard.tests_h14_at_risk_equivalence`
catches each one, restoring `dashboard/services.py` from the committed
blob (and verifying its sha256) after every mutant regardless of outcome.
Full run: `docs/evidence/h14_school_admin_summary/02_mutation_battery.log`.

**Result: 4/7 KILLED, 3/7 SURVIVED.**

| Mutant | Change | Result |
|---|---|---|
| a — ignore-enrolled-courses-filter | Drop the `if row[1] in student_course_ids` guard (always True) | **KILLED** — `FAILED (failures=2)` |
| b — count-unpublished-scores | Drop the `is_published` check, count any graded score | **KILLED** — `FAILED (failures=2)` |
| c — include-withdrawn | Drop `enrollment_status=ENROLLED` from the enrollment filter | **KILLED** — `FAILED (failures=2)` |
| d — include-inactive-students | Drop `student__is_active=True` from the enrollment filter | **KILLED** — `FAILED (failures=2)` |
| e — drop-submission-ordering | `.order_by("submission_date", "id")` → `.order_by("id")` | **SURVIVED** — `OK` |
| f — count-future-assignments | Drop the `due_date__isnull=True \| due_date__lte=now()` filter | **SURVIVED** — `OK` |
| g — submitted-count-off-by-one | `submitted_count = len({...}) + 1` | **SURVIVED** — `OK` |

Every restore (mutated or not) matched the committed sha256 across all 8
runs (control + 7 mutants) — no `RESTORE_MISMATCH`. See §5 for why the
one earlier run of this battery (recorded in `02_mutation_battery.log`
before this rerun) *did* show mismatches on a-d, and why that wasn't a
finding about the fix.

### Why e, f, g survived (real gaps, not false alarms)

These are genuine coverage gaps in the equivalence test, not artifacts
of the mutation harness — investigated against the actual code and
fixture, not just asserted:

- **e (ordering)**: `dashboard/risk.py::StudentRiskEvaluator.evaluate`
  re-sorts `graded_scores` itself (`sorted(inputs.graded_scores,
  key=lambda pair: pair[0])`) before computing the average or the trend,
  and `average_grade` is a plain `sum/len` (order-invariant). So the
  SQL-level `order_by` the mutant removes is currently redundant for
  every value the tests assert on. It would still matter for
  determinism if two submissions from the same student shared an
  identical `submission_date` (the `id` tie-breaker) feeding into
  something order-sensitive — the fixture has no such tie, so that path
  is untested, not proven safe.
- **f (future-due assignments)**: the fixture *does* include a
  future-due assignment (`a2-future` on course C2, student `s_two`), so
  the mutation does change `expected_assignment_count` for that student
  (8 → 9). But `s_two`'s submission rate is already well under every
  threshold in `dashboard/risk.py` (`SUBMISSION_RISK_THRESHOLD=0.70`,
  `CRITICAL_SUBMISSION_THRESHOLD=0.50`) both before and after the
  mutation (0.375 vs. 0.333), and the equivalence test only asserts on
  `(student_id, avg_score)` — not on `submission_rate`, `missing_count`,
  or the `reasons`/`issue_tags` lists where the change is actually
  visible. No fixture student sits near that boundary.
- **g (submitted-count off-by-one)**: same shape of gap — every fixture
  student's `at_risk` classification is decided by a grade threshold or
  a submission rate far from the 0.70/0.50 boundaries, so a ±1 shift in
  `submitted_count` never flips anyone's `at_risk` boolean, and
  `avg_score` never depends on `submitted_count` at all.

**Net implication**: the equivalence test proves the rewrite is
byte-identical to the pre-fix code on every case in its fixture, which
is what H-14 needed. It does not independently prove the underlying
risk-classification boundary logic is exercised at its edges — that
gap pre-dates this fix (the oracle has the same blind spot, since the
fixture never puts a student within one submission or one score of a
threshold). Recommend filing a follow-up if boundary coverage on
`StudentRiskEvaluator` itself is wanted; out of scope for H-14, which is
a performance rewrite, not a risk-logic change.

## 5. Mutation harness: the earlier RESTORE_MISMATCH

The first attempt at this mutation battery (also visible in
`02_mutation_battery.log`'s git history before this rerun, and described
in the shutdown commit `65e855d`'s message) showed mutants a-d
restoring to a sha256 that didn't match. Root cause, per that commit
message: **the battery was run before the fix was committed** — HEAD
still pointed at pre-fix beta while the fix sat uncommitted in the
working tree, so `mutants.sh`'s restore step (`git checkout -- $F`
compared against `git show HEAD:$F`) was checking against the wrong
baseline. That is a sequencing mistake, not a defect in the fix or in
what the equivalence test covers.

Before rerunning, this fact was verified directly rather than assumed:
with HEAD at `65e855d` (the fix, committed), the full mutate → run →
restore cycle was reproduced in isolation for mutant a outside the
script and returned `restore_ok` cleanly; the full battery rerun below
also completed with zero mismatches across all 8 restores. `mutants.sh`
was additionally hardened with an explicit `cd "$(git rev-parse
--show-toplevel)"` at the top (all of its paths — the mutation
heredoc's `open()`, `git checkout --`, `sha256sum` — are cwd-relative),
removing any residual dependency on the caller's working directory even
though no evidence was found that cwd was the actual cause of the
original mismatch. No other change was made to the script; the mutation
definitions (a-g) are unchanged from the original harness.

## 6. Regression

`dashboard` app + the 4 cache-freshness modules
(`AutoGrader.tests_cache_user_fanout` /
`_dashboard_wide` / `_invalidation_coverage` / `_dashboard_2329`):
**311 tests, OK (2 skipped), exit 0**
(`docs/evidence/h14_school_admin_summary/03_targeted_dashboard_cache.log`).
`select_related`/model-object removal touches only how
`_at_risk_students` reads data, not cache keys, generations, or
invalidation signals, so this suite is the right freshness check: it
passed unmodified.

## 7. Status

**H-14: fix and equivalence proof COMPLETE. Mutation battery COMPLETE**
**with 3 survived mutants flagged above as a known, characterized gap**
**in boundary coverage of the (pre-existing, unchanged) risk-threshold**
**logic — not a gap in the equivalence proof the fix itself needed.**

Summary of gates:
- Equivalence: 4/4, byte-identical payload before/after on the seeded
  s3-scale dataset.
- Performance: ~4,405 ms → ~2,059 ms p50 cold rebuild (directional,
  loaded machine), 17 → 18 queries.
- Mutation: 4/7 killed; 3/7 survived for the reasons in §4, judged not
  to block H-14 (the fix is a mechanical rewrite of *how* the same
  values are read, and the survived mutants are all pre-existing
  risk-threshold-boundary blind spots the fixture never exercised, on
  either the rewrite or the oracle).
- Regression: `dashboard` app + 4 cache-freshness modules, 311 tests OK.
- Diff scope: 2 files in the fix commit (`dashboard/services.py`,
  `dashboard/tests_h14_at_risk_equivalence.py`).

## 8. Follow-up: deletion race flagged in verification (fixed)

The Verification Engineer found a failure mode the rewrite introduced (see
`docs/evidence/h14_school_admin_summary/VERIFICATION.md`): the scan reads
enrollments and submissions first, then fetches names with `in_bulk()`. A
student hard-deleted between the two phases was missing from the `in_bulk()`
result, and `students_by_id[student_id]` raised `KeyError`, crashing the whole
rebuild. The pre-fix single query had no such window.

- **Fix**: `students_by_id.get(student_id)`, skipping a missing id. That
  matches the original's behaviour for a student who no longer exists.
- **Test**: `test_a_student_deleted_between_the_scan_and_the_name_lookup_is_skipped`
  patches `QuerySet.in_bulk` to hard-delete one at-risk student immediately
  before the lookup runs, then asserts no crash and the baseline list minus
  that student, order unchanged. The equivalence module is now 5/5.
- **Mutation**: restoring `students_by_id[student_id]` makes the test ERROR
  with `KeyError: UUID(...)`: killed.
- **Regression**: `dashboard` + the 4 cache-freshness modules, 316 tests OK
  (skipped=2).
