# H-280: school detail answers 400 when a teacher or admin has no name

Branch `task/h280-school-detail-blank-name` from origin/beta `e11e56536`.
Worktree `Grade-Automator-Plus-h280`, test DB `test_AutoGrader_h280`
(untracked `settings_worktree.py`), venv outside the worktree.

## Report (SM)

The owner hit this live on beta-production. `GET /api/v1/schools/<id>/`
gave a 400 with `{"message": "Teacher name: This field may not be blank."}`,
pointing at `field_errors.session_breakdown[].teachers[].teacher_name`.

## Cause

- `SchoolViewSet.retrieve` (`classrooms/views.py:862`) ran its own response
  through `SchoolDetailSerializer(data=data).is_valid(raise_exception=True)`.
- `teacher_name` is `get_full_name()`, which is `""` when a teacher has no
  first, middle or last name. That includes a whitespace-only name, because
  `get_full_name` strips each part.
- `SessionTeacherSerializer.teacher_name` is a plain `CharField`, so as input
  it refuses a blank value and the whole page fails.
- `admin_name` is `CharField(allow_null=True)` with no `allow_blank`, so it
  has the same bug.
- `school_name` would fail the same way for a blank school name.

## Fix (SM default; no new wording)

1. Serialize the response; don't validate it. The response is now
   `SchoolDetailSerializer(data).data`, in instance mode. The OpenAPI schema
   stays as it was: `responses={200: SchoolDetailSerializer}` at
   `classrooms/views.py:261`.
2. A nameless teacher or admin shows their email as the name. The
   established fallback is `user.get_full_name() or user.email`
   (`billing/stripe_service.py:401`). It is the only name-to-email fallback
   in app code. The other pattern, `get_full_name() or user.first_name`
   (`classrooms/serializers.py:919`, `:937`), is a no-op for a nameless user,
   so I did not use it.
   - Teachers: the fallback is applied once, in the `teacher_names` map. The
     SCHOOL-session sort key reads the same map, so a nameless teacher sorts
     by the email that is shown.
   - Admin: `admin.get_full_name() or admin.email`.

## Survey: our own response run through `is_valid`

I swept every `XSerializer(... data=<not request.data/query_params>)`
followed by `.is_valid`, across all apps (single-line and multi-line), with
tests, migrations and docs excluded.

| Site | What it is | Verdict |
|---|---|---|
| classrooms/views.py:862 `SchoolDetailSerializer(data=data)` | the response | **the bug; fixed** |
| billing/views.py:246 `SubscriptionPlanSerializer(data=data)` | `request.data.copy()` plus forced fields, then `save()` | input write, correct |
| users/views.py:2297 `GoogleUserSerializer(data=data)` | Google profile, then user create | input write, correct |
| assignments/views.py:1528, :1716; file_uploads.py:150; services.py:1152 `AssignmentSerializer(data=...)` | assignment create/update | input write, correct |
| classrooms/views.py:1897 `TopicSerializer(data=topics_data, many=True)` | `request.data` reshaped, then `save()` | input write, correct |
| billing/qa_console.py:506, :561 | `_json_body(request)` | input, correct |
| dashboard/views.py:1175, :2950, :4134 `CustomAIPrompt(data=request.data)` | request input | input, correct |

dashboard, users and billing build their response serializers in instance
mode (for example `ConcurrencySerializer({...}).data`), so none of them
validates its own output. No other site needs a change.

Seen, not changed (out of scope; for the SM): the school **list**
(`classrooms/views.py:656`) returns its dict with no serializer, so it cannot
give a 400. It does show `admin_name: ""` for a nameless admin, where the
detail page now shows the email.

## Guards and red tests

Module `classrooms.tests_h280_school_detail_blank_name`, 6 tests:

| Test | Guard |
|---|---|
| T1 nameless teacher in a SCHOOL session → 200, name = email | G1 teacher fallback; G3 no output validation |
| T2 nameless teacher in an INDIVIDUAL session → 200, name = email | G1, G3 |
| T3 whitespace-only name → 200, name = email | G1, G3 |
| T4 nameless admin → 200, admin_name = email | G2 admin fallback; G3 |
| T5 named teacher and admin still show their names | the fallback does not replace real names |
| T6 blank school name → 200, `school_name ""` | G3 alone (no name fallback involved) |

## E1: red run at the red commit (expectations, written before the run)

Command, serial, own DB:
`python manage.py test --noinput --settings=settings_worktree -v 2 classrooms.tests_h280_school_detail_blank_name`

- Expect `Ran 6`, `FAILED (failures=5)`.
- T1, T2, T3, T4 and T6 fail on the status assertion: 400, not 200.
- T5 passes. With real names nothing is blank, so the old code gives 200.
- Gate: `free -m` available ≥ 4000 MB before the run.

## E1 result (853d2f121)

**Met.** `Ran 6`, `FAILED (failures=5)`. T1, T2, T3, T4 and T6 failed with
`400 != 200`, and T5 passed. The bodies show the live bug:
- `teacher_name` gave "This field may not be blank." for T1, T2 and T3;
- `admin_name` gave the same for T4;
- `school_name` gave the same for T6.

Log: `logs/red_run_853d2f12.txt`.

## Fix commit: expectations (written before the runs)

**E2, green.** The same module, serial: expect `Ran 6`, `OK`.

**E3, targeted.** Serial, own DB, 4 modules (56 tests), each of which
touches `SchoolViewSet`:

- `classrooms.tests_h280_school_detail_blank_name` (6)
- `classrooms.test_views` (36)
- `classrooms.tests_course_roster_scope_sweep` (4)
- `users.tests_superadmin_both_flags` (10)

Expect `Ran 56`, `OK`. No existing test changes: instance-mode output equals
the old `validated → .data` output for every value that used to pass. The
only difference is that `CharField` no longer trims whitespace on output,
which no current test depends on.

**E4, schema.** `manage.py spectacular` at 853d2f121 and at the fix commit
gives byte-identical YAML, because the serializers are unchanged.

**E5, mutants.** One per guard, held until the SM says Batch 33 is finished:

| Mutant | Change | Expected killers |
|---|---|---|
| M1 | `u.get_full_name() or u.email` → `u.get_full_name()` | T1, T2, T3 (blank name, 200) |
| M2 | `(admin.get_full_name() or admin.email)` → `admin.get_full_name()` | T4 |
| M3 | back to `SchoolDetailSerializer(data=data)` plus `is_valid(raise_exception=True)` | T6 (the fallbacks keep T1–T4 green) |

**E6, near run.** Held until the SM says Batch 33 is finished.

## E2/E3 result (f07ec58aa)

**Met.** One serial run of the 4 E3 modules gave `Ran 56`, `OK`. That
includes all 6 H-280 tests, so E2 is met as well. Log:
`logs/targeted_run_f07ec58a.txt.gz`.

E4–E6 are still to do. H-280 is queued behind the H-278 follow-up, per the SM.

## The school list gets the same fallback (SM, 2026-11-03)

SM: the school **list** `admin_name` falls back to `get_full_name() or email`,
using the same helper as the detail view. Red test first.

The helper: a nameless user's name. One module-level function in
`classrooms/views.py` returns `user.get_full_name() or user.email`. It is
used for the list admin, the detail admin and the detail teachers. It
replaces the two inline `or email` expressions from f07ec58aa.

New tests:
- T7 `test_the_school_list_shows_a_nameless_admin_by_email`
- T8 `test_the_school_list_still_shows_a_named_admin_by_name`

**E7, red at this commit (expectations, written before the run).**
Serial, the H-280 module only. Expect `Ran 8`, `FAILED (failures=1)`.
- Only T7 fails: `'' != 'h280-list-admin@x.test'`.
- T8 passes, and T1–T6 still pass, since the detail fix is in.
- Gate: ≥ 4000 MB available. Batch 33 re-run in progress: small serial
  runs only.

**E7 result (3241a7659): met.** `Ran 8`, `FAILED (failures=1)`. Only T7
failed, with `AssertionError: '' != 'h280-list-admin@x.test'`. Log:
`logs/red_run_3241a765.txt`.

## List fix commit: expectations (written before the runs)

The fix adds `_name_or_email(user)` to `classrooms/views.py` and uses it at
the list `admin_name`, the detail `admin_name` and the detail
`teacher_names`. Ordering the list by `admin_name` still sorts on the name
columns, so a nameless admin sorts as blank. That is unchanged and out of
scope.

**E8, green.** The H-280 module, serial: `Ran 8`, `OK`.

**E9, targeted.** The same 4 modules as E3, serial: `Ran 58` (56 + 2), `OK`.

**E4, schema.** `manage.py spectacular` at e11e56536 (beta) and at this fix
gives byte-identical YAML.

**E5, mutants, one per guard.** Serial, one module, disposable detached
worktree, restored by sha256. Allowed during the B33 re-run as small serial
runs.

| Mutant | Change | Expected killers (all others pass) |
|---|---|---|
| M1 | `u.id: _name_or_email(u)` → `u.id: u.get_full_name()` | T1, T2, T3 |
| M2 | detail `_name_or_email(admin) if admin` → `admin.get_full_name() if admin` | T4 |
| M3 | `return Response(SchoolDetailSerializer(data).data)` → the `data=data` plus `is_valid(raise_exception=True)` form | T6 |
| M4 | list `_name_or_email(admin) if admin` → `admin.get_full_name() if admin` | T7 |

Each mutant is expected to give `Ran 8` with failures = the number of
killers: 3, 1, 1 and 1.

**E6, near run.** Held until the SM says B33 is finished.
