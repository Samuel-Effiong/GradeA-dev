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
