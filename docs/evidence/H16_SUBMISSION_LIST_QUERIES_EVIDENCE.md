# H-16 — teacher submission list per-row queries: evidence

Backlog item: `docs/HARDENING_BACKLOG.md` H-16, proposed owner Section 7,
assigned by the owner 2026-09-15. Found by the H-1 stampede measurement
(`docs/evidence/H1_STAMPEDE_MEASUREMENT.md`): the teacher's
`student-submission-list` ran 63 queries per cold build at every data size.

## 1. Cause

`StudentSubmissionViewSet.get_queryset` returned a bare queryset for the
`list` action. `StudentSubmissionListSerializer` reads, per row, the
submission's `student` (`student_name`), `assignment` (`assignment_title`,
the `max_points` fallback) and `assignment.course` (`course`) — three
relations fetched lazily, once per row, because DRF's paginator evaluates
the page before the serializer runs. 4 fixed queries (count, page,
`UserActivity` insert, credit-wallet lookup) + 3 × page size.

## 2. Fix

`students/views.py` — `StudentSubmissionViewSet.get_queryset`: for the
`list` action only, `select_related("student", "assignment__course")`.
Both relations are non-nullable foreign keys (`StudentSubmission.student`,
`StudentSubmission.assignment`, `Assignment.course`), so the joins are
`INNER JOIN` and cannot widen the tenant-scoped filter already applied
above. `retrieve` and the other actions are untouched.

## 3. Acceptance criteria (from the backlog item) and evidence

### Query count flat in page size (asserted, not budgeted)

Measured with `CaptureQueriesContext` at two data sizes (60 and 240
submissions) and 16 request shapes each (page sizes 1/5/20/50/100/500,
second page, two orderings, search, four filter combinations, teacher and
student), on a cold cache (`cache.clear()` before every request, so the
per-user list-cache mixin cannot mask a query count):

| Shape | Baseline (beta `87acd13`) | After the fix |
|---|---|---|
| teacher, page_size 1 | 7 | 4 |
| teacher, page_size 5 | 19 | 4 |
| teacher, page_size 20 (default) | 64 | 4 |
| teacher, page_size 50 | 154 | 4 |
| teacher, page_size 100 | 304 | 4 |
| teacher, page_size 500 (clamped to 100) | 304 | 4 |
| teacher, every filter/ordering/search shape | 26–65 | 4 (5 with `?assignment=`, which validates the filter id with one extra lookup — itself flat across page size) |
| student, default / page_size 50 | 7 | 4 |

Full before/after JSON (all 32 shapes × 2 data sizes) is in the session
scratchpad (`baseline.json`, `baseline87.json`, `fixed.json`) — reproduced
here as the table above since the scratchpad does not survive between
sessions; the counts at both data sizes (60 and 240 submissions) are
identical for the same shape, confirming flatness in data size as well as
in page size (H-10's flatness convention, applied to this family).

Locked in `students/tests_submission_list_queries.py::SubmissionListQueryFlatnessTest`:
counts equal across page sizes 1/5/20/50/100; the default page size (no
`page_size` param) matches the 1-row-page count; counts equal across
second-page, both orderings, search, review-queue filter and
published-filter shapes; the assignment filter's small/large counts match
each other.

### Identical payload

`fixed.json`'s payload SHA-256 for every one of the 32 shapes is
byte-identical to `baseline.json`'s (`payload_sha256` dict, verified by
diffing the two structures — zero differing keys).

Locked in `SubmissionListPayloadIdentityTest`: the API response, run
through the fix, is compared field-for-field against
`StudentSubmissionListSerializer` run directly over the **plain,
unoptimised** queryset (no `select_related` at all) — so the proof does
not depend on how the optimisation is implemented, only on what it
returns. Covered for both a teacher's full page and a student's own page,
plus an explicit per-field check (`student_name`, `assignment_title`,
`course`, `max_points`) against the live related objects.

### Freshness unchanged

`select_related` changes nothing about what is cached or when the cache is
invalidated — the mixin still caches the fully-rendered `Response.data`,
keyed and versioned exactly as before. No signal, generation scope, or
cache key changed.

Evidence: the three suites that exercise this endpoint's cache family
still pass unmodified: `AutoGrader.tests_cache_user_fanout` (per-viewer
freshness after a mutation), `AutoGrader.tests_cache_invalidation_coverage`
(family coverage map, includes the `studentsubmissions` list key shape),
`students.tests_submission_update_freshness` (freshness with legacy
wildcard sweeps disabled). `students.tests_second_opinion_queue`, which
also reads this endpoint under `?needs_review=`/`?ordering=`, is unchanged.

## 4. Tenancy

`select_related` performs `INNER JOIN`s on top of the tenant filter
(`assignment__course__teacher=user` / `student=user`, already applied
above); an inner join can only narrow a result set relative to the same
`WHERE` clause without it, never widen it. Proven directly in
`SubmissionListTenancyTest`: a teacher sees exactly their own courses'
submissions and not a submission planted on another teacher's course; the
other teacher sees only their own; filtering by another teacher's
assignment id returns nothing or a validation error, never that teacher's
rows; a student sees only their own non-draft submissions, and specifically
not their own submission on a draft assignment; an unscoped user type
(school admin) sees nothing.

## 5. Mutation testing

Three mutants of the fix, each killed by
`SubmissionListQueryFlatnessTest`, each restored by md5 checksum after the
run (not merely by the mutation script's own claim, per project
standard):

| Mutant | What it removes | Result |
|---|---|---|
| M31 | The whole `select_related` call | `FAILED (failures=10)`, restored-ok |
| M32 | Just `"student"` | `FAILED (failures=10)`, restored-ok |
| M33 | Just `"assignment__course"` | `FAILED (failures=10)`, restored-ok |

## 6. Regression

`students` (whole app), `AutoGrader.tests_redis_test_isolation`,
`AutoGrader.tests_cache_user_fanout`, `AutoGrader.tests_cache_invalidation_coverage`,
`AutoGrader.tests_cache_dashboard_freshness` — **318 tests, OK, 1 skipped
(the opt-in billed real-provider test), exit 0**, run on branch
`task/h16-submission-list-queries` at `87acd1387a6dfbf87fb4803a42a9ef9f436bc022`
(beta) plus this change, `--keepdb`, real PostgreSQL + Redis.

`manage.py check`: no issues. `makemigrations --check --dry-run`: no
changes (this is a queryset change only — no model or migration touched).
`pre-commit run --files students/views.py students/tests_submission_list_queries.py`:
every hook passed (black, mypy, flake8, isort, bandit, detect-secrets; the
only auto-fix was end-of-file whitespace on the new test file).

## 7. Diff scope

Two files: `students/views.py` (14 insertions, 2 deletions, confined to
`StudentSubmissionViewSet.get_queryset`) and the new
`students/tests_submission_list_queries.py`. Nothing else in the tree
changed (`git diff --quiet HEAD -- . ':!students/views.py' ':!students/tests_submission_list_queries.py'`
confirmed empty before this evidence file and the backlog update were
added).

## 8. Status

**H-16: COMPLETE.**

All three acceptance criteria named in the backlog item are met and
proven: query count flat in page size (4, from up to 304), identical
payload (byte-for-byte against the unoptimised serializer), freshness
unchanged (existing cache suites pass unmodified). Tenancy is unaffected
(proven, not merely assumed, since the fix adds joins to a security-
relevant queryset). Mutation-tested, regression-tested against the whole
`students` app and every cache suite that touches this endpoint, and
scoped to exactly the two files above.
