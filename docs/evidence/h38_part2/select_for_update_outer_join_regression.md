# H-38 part 2 — a real regression: `SELECT ... FOR UPDATE` on a nullable outer join

## What happened

`assignments/views.py`'s `save_generated_assignment_draft` action
(`generated-drafts/{message_id}/save`, ~line 1461) locked its row with:

```python
draft_message = get_object_or_404(
    AssignmentGenerationMessage.objects.select_for_update()
    .select_related("session__course", "session__user")
    .filter(
        teacher_course_access_q(request.user, prefix="session__course__"),
        id=message_id,
        session__user=request.user,
        role=AssignmentGenerationRole.ASSISTANT,
    )
)
```

`teacher_course_access_q` (see `classrooms/models.py`) ORs across whether the
course's session is reachable, including a `session__isnull=True` branch.
Chained onto the `"session__course__"` prefix, that resolves to a condition
on `Course.session` — a **nullable** FK (`classrooms/models.py`, `Course.session
= models.ForeignKey(Session, ..., null=True)`). To evaluate an
`OR`-branch that can be true when a nullable FK is null, Postgres has to plan
that join as a **LEFT OUTER JOIN**, not an inner join. Postgres refuses to
take `SELECT ... FOR UPDATE` through the nullable side of an outer join:

```
django.db.utils.NotSupportedError: FOR UPDATE cannot be applied to the
nullable side of an outer join
```

The result: **every** teacher hitting this endpoint got a 500 — not just
removed teachers. This was a regression introduced by H-38's own access-check
helper (`teacher_course_access_q`), not a pre-existing bug and not specific to
the removed-teacher scenario the helper was written to close.

## How it was masked

The dynamic test added for this site
(`RemovedTeacherDraftSaveTests.test_save_generated_assignment_draft_refuses_removed_teacher`
in `billing/tests/test_h38_part2_removed_teacher_routes.py`) asserted only
`self.assertNotIn(response.status_code, (200, 201, 202, 204), ...)` — "did not
succeed" — because the 500 was observed and initially read as plausible
"removed teacher correctly refused" behaviour (a non-2xx status). It is not:
a 500 here means Postgres rejected the query outright, before the H-38 access
check itself ever gets a chance to allow or refuse anything. The loose
assertion could not tell the difference between "refused because not
authorized" and "broken for everyone," so it passed on both a correctly
secured endpoint and a completely broken one.

The coordinator caught it by reading the query plan (the nullable-FK OR
clause forcing an outer join under lock) and confirming empirically against
an **active, never-removed** teacher fixture — an active teacher being
unable to save a draft is unambiguous evidence of a bug, whereas a
removed teacher getting a non-2xx status is ambiguous. That is exactly why
`ActiveTeacherDraftSaveTests` (a positive control) was added in this pass:
it is the test that would have caught the original regression immediately.

## The fix

`save_generated_assignment_draft` now runs the access check as its own
**unlocked** query first (same `teacher_course_access_q` filter, no
`select_for_update()`, so Postgres is free to plan the outer join), then
takes the lock with a **second, tightly-scoped `.get(pk=...)`** — a plain
primary-key lookup, which is never an outer join regardless of what other
filters exist elsewhere in the model:

```python
access_check = get_object_or_404(
    AssignmentGenerationMessage.objects.filter(
        teacher_course_access_q(request.user, prefix="session__course__"),
        id=message_id,
        session__user=request.user,
        role=AssignmentGenerationRole.ASSISTANT,
    )
)
draft_message = get_object_or_404(
    AssignmentGenerationMessage.objects.select_for_update().select_related(
        "session__course", "session__user"
    ),
    pk=access_check.pk,
)
```

### Why this option, not `select_for_update(of=("self",))`

The coordinator offered two options: `of=("self",)` to scope the lock to only
the base table, or splitting into an unlocked access check plus a locked
by-pk fetch. `of=("self",)` was not used here because:

- It still requires Django/psycopg to translate `of=("self",)` correctly for
  this specific query shape (a `.filter()` combining a multi-hop `OR`
  condition with `select_related` two levels deep); verifying that produces
  the exact SQL Postgres needs, for this exact join tree, is more surface
  area to get subtly wrong under time pressure on a security-relevant lock.
- The two-query split is easier to reason about and verify: the unlocked
  query is *exactly* the pre-existing access-check shape (nothing new to get
  wrong there), and the locked query is a bare primary-key `.get()`, which by
  construction can never be an outer join. There is no ambiguity about what
  gets locked (the `AssignmentGenerationMessage` row named by `pk`, nothing
  else) versus what stays unlocked (the join used only to decide
  reachability).
- What needs to be locked here is narrow: the single `AssignmentGenerationMessage`
  row being transitioned from `AI_DRAFT` to `SAVED` (`draft_message.assignment`,
  `.assignment_snapshot`, `.metadata` are all written later in the same
  transaction). Nothing about `Course` or `Session` needs a row lock — they
  are read-only in this action. A locked-by-pk fetch expresses that directly.

The small window between the unlocked check and the locked fetch (both
inside the same `transaction.atomic()` block, milliseconds apart) is an
acceptable exposure: the original combined query had no stronger
guarantee either (a school-membership change racing the request was never
excluded), and correctness for the write itself (idempotent-save,
`assignment_id` check) still happens strictly after the lock is acquired.

## Sweep for the same pattern (step 2)

Grepped the H-38 diff (`git diff ad93df2^ 56099ce`) for every
`select_for_update()` call, and separately grepped the whole tree for every
call site of `teacher_course_access_q`, `reachable_courses`, and
`teacher_can_reach_course` (the H-38 access-check family) to check whether
any is combined with `select_for_update()` anywhere in the codebase.

**Only one site combines them: `save_generated_assignment_draft`
(fixed above).** The H-38 diff itself confirms this — `select_for_update`
appears exactly once across the whole `ad93df2^..56099ce` range, at that
site.

Every other call site of the access-check family, checked individually:

| Site | select_for_update in the same query/action? | Disposition |
|---|---|---|
| `assignments/views.py:316` `AssignmentViewSet.get_queryset` | No | Safe — plain `.filter()`/`.annotate()`, no lock |
| `assignments/views.py:780,964,1232` `reachable_courses(...)` via `get_object_or_404` | No | Safe — unlocked course lookups |
| `assignments/views.py:1800` `teacher_can_reach_course` (download-pdf teacher view) | No | Safe — a boolean permission check, no queryset/lock involved |
| `assignments/tasks.py:930` `reachable_courses(user).get(...)` | No | Safe — unlocked Celery task read |
| `classrooms/views.py:1264` `CourseViewSet.get_queryset` | No | Safe |
| `classrooms/views.py:1448` `teacher_can_reach_course` (`remove_student`) | No | Safe — boolean check, no lock |
| `classrooms/views.py:2039,2072,2091` `StudentCourseViewSet.get_queryset`/`my_students` | No | Safe |
| `classrooms/views.py:2387` `TopicViewSet.get_queryset` | No | Safe |
| `classrooms/serializers.py:117` `TopicSerializer.validate_course` (`teacher_can_reach_course`) | No | Safe — boolean check |
| `students/views.py:156` `_assignment_taught_by` | No | Safe — `get_object_or_404`, no `select_for_update()` |
| `students/views.py:358` `StudentSubmissionViewSet.get_queryset` | No | Safe |
| `students/views.py:1387` `StudentViewSet.get_queryset` | No | Safe (and this view is unreachable/unrouted — see `mutation_results_final.md` S3 finding, unrelated to this pass) |
| `dashboard/views.py:3150,3286,3388` `reachable_courses`/`teacher_course_access_q` lookups | No | Safe — unlocked reads |
| `dashboard/services.py:1136` `TeacherAIContextService.build` | No | Safe |
| `users/views.py:297` `CustomUser` queryset with `teacher_course_access_q` OR | No | Safe |

None of the ~90 other `select_for_update()` call sites in the codebase
(billing, students, classrooms.signals, etc. — `UserSubscription`,
`CreditWallet`, `LicenseSubscription`, `StudentSubmission`,
`BackgroundProcessingTask`, and similar) are built on
`teacher_course_access_q`/`reachable_courses`/`teacher_can_reach_course`, or
on any other `Q()` OR-clause spanning a nullable FK — each locks a model by a
plain, single-FK or primary-key filter. No further instance of this exact
bug pattern was found.

## Test hardening (step 3)

In `billing/tests/test_h38_part2_removed_teacher_routes.py`:

- **`RemovedTeacherDraftSaveTests.test_save_generated_assignment_draft_refuses_removed_teacher`**
  (the A3 test): tightened from `assertNotIn(status, (200, 201, 202, 204))` to
  a strict `assertEqual(status, 404)` plus an explicit
  `assertLess(status, 500)` guard.
- **`ActiveTeacherDraftSaveTests.test_save_generated_assignment_draft_succeeds_for_active_teacher`**
  (new): positive control — an active, never-removed teacher saves a draft
  and gets `201 Created`, with the `Assignment` count incrementing and
  `draft_message.assignment_id` populated. This is the test that would have
  caught the original regression immediately.
- Three sibling "loose" assertions using the same
  `assertNotIn(status, (200, 201, 202, 204))` pattern were tightened to
  their empirically-confirmed strict status plus a `< 500` guard:
  `test_upload_assignment_async` (400), `test_grade_all` (400),
  `test_grade_paid_ai` (400).
- **Not tightened, flagged instead:** `test_custom_ai_prompt_on_the_school_course`.
  This route (`dashboard/views.py` `custom_ai_prompt`) empirically returns
  500 for a removed teacher today (confirmed 2026-09-28), via a pre-existing
  status-mapping quirk unrelated to H-38 and unrelated to
  `select_for_update`/outer joins (this action never calls
  `select_for_update()` or `teacher_course_access_q`; the 500 happens while
  building superadmin analytics context for a non-superadmin user).
  Tightening this assertion would turn a genuine, separate, pre-existing bug
  into a newly-failing test in this pass, which is out of scope here. Left
  as a loose "did not succeed" check with an inline comment explaining why,
  and flagged here for separate triage.
  **Correction (2026-09-28, after the rebase onto beta 4b902fc):** the
  "superadmin analytics context" explanation was wrong. The 500 was
  `AIFeatureNotAvailableError` ("No active subscription"), which
  `run_dashboard_ai_chat`'s `except Exception` answered as an explicit 500.
  Beta `f7cd15e` fixed that, and the route now answers 403. The test is now
  strict and has a positive control. See `custom_ai_prompt_500_triage.md`.
- `billing/tests/test_h38_teacher_removal.py` (part 1, ~17 tests) was read
  in full: every status assertion there is already a strict `assertEqual`
  or a narrow `assertIn(status, (403, 404))`/`(402, 403, 404)` tuple, none of
  which admit a 5xx. No changes needed in that file.

## Mutation testing (step 4)

Reverted `save_generated_assignment_draft` to the pre-fix combined
`select_for_update().filter(teacher_course_access_q(...), ...)` shape and
re-ran `RemovedTeacherDraftSaveTests` and `ActiveTeacherDraftSaveTests`.

**Result: KILLED, by both new tests, on the exact predicted error.**

```
django.db.utils.NotSupportedError: FOR UPDATE cannot be applied to the
nullable side of an outer join
...
AssertionError: 500 != 404  (test_save_generated_assignment_draft_refuses_removed_teacher)
AssertionError: 500 != 201  (test_save_generated_assignment_draft_succeeds_for_active_teacher)
```

Reverted the mutation immediately after confirming (`assignments/views.py`
restored to the fixed version from a saved copy, verified byte-identical to
the pre-mutation state).

## Regression run

`billing.tests.test_h38_part2_removed_teacher_routes
billing.tests.test_h38_teacher_removal classrooms.tests_teacher_access_sweep`,
`nice -n 10`, no `--parallel`: **59/59 passed**, clean state, after the fix
and all test changes above. Key observed statuses:

- `save_generated_assignment_draft`, removed teacher: `404`
- `save_generated_assignment_draft`, active teacher (positive control): `201`, `changed=True`
