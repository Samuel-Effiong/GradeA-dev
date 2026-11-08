# Epic C1: expectations (written before any change)

Plan: docs/phase2/architecture/11_epic_c1_implementation_plan.md
(task/plan-e1-c1 31b158382). Branch `task/epic-c1`, base staging
**8aafa47dd** (refresh 20). Owner approval to start: 2026-11-06 (via the
SM, after a pause). No production code yet. No push.

## Rulings and deviations from plan 11 (all recorded before code)

| # | Plan 11 says | Built instead | Source |
|---|---|---|---|
| 1 | Tag routes not registered while the flag is off | Always registered; with the flag off each answers exactly what an unknown URL answers (`json_404`), from `dispatch()`, before authentication and permissions, so no 401/403 reveals the route. Needed because URLs are read once at import (tests could not switch the flag) and because the S2 audit sweep only sees registered routes. While dark the write routes are in S2's `CONCEALED_ROUTES`. | SM ruling 1 |
| 2 | Reuse (POST of an existing name) emits nothing | No TAG_CREATE on a reuse. The S1 middleware still records the request itself, exactly one STATE_CHANGE, as it does for every authenticated write with no named event. | SM ruling 2 with correction (a) |
| 3 | D6 messages only | `remediation=""` for all five codes: each message already says what to do; no new wording | SM ruling 3 |
| 4 | D7 co-teachers may tag shared assignments | Moot: `teacher_course_access_q` grants only the course's own teacher. No co-teacher path; any other teacher gets 404 | SM ruling 4 |
| 5 | D9 admin visibility | Admins see no tags; school and super admins are refused on every tag route | SM ruling 4 |
| 6 | D5 zero-width strip (recommended) | **Owner ruling 2026-11-06, changes FR-C-08's documented rule:** every Unicode Cf (format) and Cc (control) character is removed before the trim and length checks; a whitespace control (tab, newline, CR) becomes a space first; a name empty after removal is TAG_NAME_MISSING. Visible punctuation and emoji are unchanged (owner decision pending; one row of `CHARACTER_RULES`) | Owner, via SM |
| 7 | TAG_RENAME records before/after `name` | ids and labels only (`tag_type`): the audit allow-list refuses names and any text a person wrote (audit/metadata.py) | SM correction (b) |
| 8 | `PUT /assignments/{id}/tags/` as an action on the assignment view | Its own view in the `tags` app: `AssignmentViewSet.http_method_names` excludes PUT, and adding it would open `PUT /assignments/{id}` too | SM correction (b) |
| 9 | Paths with a trailing slash | No trailing slash, as every router here (`trailing_slash=False`) | house style |
| 10 | `affected_count` in TAG_DELETE metadata | Added to `ALLOWED_KEYS` with `tag_type` and `tag_ids` (the reviewed pool) | audit/metadata.py rule |

## Facts checked on 8aafa47dd

H-297 is on staging (Assignment.topic SET_NULL; topics kept by name). TAG_*
actions exist with empty allow-lists (audit/enums.py:65-67,
audit/metadata.py:293-295). B0's `zero_credit_scope`,
`register_zero_credit_operations`, `assert_operation_is_zero_credit` exist; no
production operation is registered yet. `teacher_course_access_q`
(classrooms/models.py:252) grants `course.teacher` only. `Topic.course` and
`Course.teacher` are nullable. A foreign object's 404 already reads "We
couldn't find that item. It may have been deleted." (H-296,
users/exceptions.py). `StudentCourse` defaults to PENDING (fixtures enroll).

## The tests

| File | What | Expected on the base |
|---|---|---|
| assignments/tests_c1_dark_snapshot.py + assignments/c1_snapshots/ | teacher list, list by course, detail; student list, detail: byte-identical while dark | **green** (recorded on 8aafa47dd), must stay green |
| tags/tests_normalize.py | table-driven FR-C-08 incl. the owner's D5 (Cf/Cc removed), NFKC, lower not casefold, limits | red (no `tags.normalize`) |
| tags/tests_models.py | every constraint proved at the database; delete directions (H-297 lesson) | red |
| tags/tests_services.py | the six service functions, their audit events (no names) and FR-B-10 zero-credit | red |
| tags/tests_api.py | the API with the flag on: create/reuse, list (own, q, order, count), rename, impact, delete with count, set tags, the cap, 10,000 ids, isolation (404), roles (403), anonymous (401), one audit event per write, the `tags` field, no N+1 | red |
| tags/tests_dark.py | the flag is off by default; every route x {anonymous, student, teacher} answers byte-identically to an unknown URL; nothing is written | red |
| tags/tests_backfill.py | forward, idempotent rerun, interrupted run completed (Gate 5), dry run, --course, skips with reasons, reverse removes only backfilled rows | red |
| tags/tests_concurrency.py | Gate 3: 10-way create race x100; delete vs attach x100 | red |
| tags/tests_catalog.py | the five approved codes word for word; audit allow-lists; S2 concealed routes | red |

All red tests are expected to fail on import (no `tags` app yet): the red
run proves each test file loads its target, nothing more. The real red for
each behavior is proved by the mutation matrix (plan §5.2, M1-M12) once the
code exists.

## Mutation plan (§5.2, plus the rulings)

| Id | Mutation | Must fail |
|---|---|---|
| M1 | drop `.lower()` | test_variants_resolve_to_one_key |
| M2 | `split()` collapse replaced by `strip()` | test_variants_resolve_to_one_key |
| M3 | partial unique constraint removed | test_one_tag_per_teacher_type_and_normalized_name |
| M4 | list not filtered by teacher | test_the_other_teacher_sees_only_theirs |
| M5 | set_assignment_tags accepts a foreign tag | test_another_teachers_tag_is_not_found_and_nothing_changes |
| M6 | delete_tag ignores expected_count | test_a_stale_count_is_refused_and_nothing_is_deleted |
| M7 | AssignmentTag.tag on_delete=PROTECT | test_deleting_a_tag_keeps_every_assignment_and_submission |
| M8 | a service function outside zero_credit_scope | its zero-credit test |
| M9 | backfill without ignore_conflicts / existence check | test_a_second_run_changes_nothing |
| M10 | reverse deletes USER links | test_reverse_removes_exactly_what_the_backfill_made |
| M11 | flag check removed (dispatch or serializer field) | test_every_route_answers_like_an_unknown_url_for_everyone; snapshots |
| M12 | get_or_create without the IntegrityError re-read | test_one_row_and_one_id_for_every_caller |
| M13 | Cf removal dropped (owner D5) | test_invisible_characters_are_removed |
| M14 | flag check moved after authentication | dark test (anonymous gets 401, not the unknown-URL body) |

## Gates

Gate 1 near run: tags classrooms assignments audit billing (zero-credit),
skips reported against the pre-loss baselines (Chromium reinstalled by the
SM). Gate 6: the list with 500 tags (query count flat). Gate 7: real Postgres
throughout.
