# Epic F1 (Departments): build notes

Branch `task/epic-f1-build` (from staging e8fc8cc99, staging merged once at f9001c9cc). Plan: `docs/evidence/epic-f1/STEP0_PLAN.md` on `task/epic-f1`. Owner wording W1-W13 used verbatim from `departments/wording.py`. Flag `DEPARTMENTS_ENABLED`, default on outside tests; flag off answers every route as an unknown URL, before authentication.

## Slices (test commit, then code commit)
| Slice | What | Commits |
|---|---|---|
| 1 | Department and DepartmentMembership models, migration 0001, name rule (NFKC, case-folded key, 1-100 chars), audit labels and allow-lists, student data map entries | 1a8210add, 207b4e26c |
| 2 | Admin API (list, create, rename, delete, members add/remove/edit switch), teacher my-departments and roster, access functions, 7 reason codes, Django admin, route-coverage and swagger entries | 385136069, 90a47f449 |
| 3 | `end_memberships()` (by state, idempotent, fails closed); explicit call in `remove_teacher_from_license`; atomic `CustomUser.save()` override (acts only when update_fields writes school or user_type; no user lock when the loaded school is null); bulk path in `record_bulk` | 11a68cdef, 7532f9aad |
| 4 | Membership emails W11, W12: after commit, one per teacher, ids-only log on failure, never undo the change | ad0fb70f0, ff1c26909 |
| 5 | Delete with the content choice and the content registry; one DEPARTMENT_DELETE event | 516987c05 (one commit, see below) |
| 6 | Scope `dpt`; bumps on every write and reap; my-departments cached per viewer with the liveness check first | 13896dfff, fb60fea3a, 41f8b028c (cache key map) |
| 7 | T10 liveness cases, T16 no session field, serializer, access-sweep and registry guards, exact payload key sets | 8d055c7c8 |
| fix | Admin department lookup goes through `access.admin_department` (SM ruling after the Fixer's matrix) | 4a6dd450f |

## Deviations and rulings (all accepted by the SM)
- Slice 5's red tests and code are one commit: the red commit was refused by the isort hook and I did not split it again.
- `audit/metadata.py` `MAX_BYTES` went from 2048 to 4096. A delete event with the 50-id cap plus `item_count`, `delete_choice` and `target_department_id` exceeded 2048 and the whole metadata block was dropped. The cap of 50 ids stays.
- The 409 for a missing content choice carries only a total `item_count`; reason-code params must be scalars. Per-type counts can come with D or F2.
- The audit before/after carry ids and changed_fields only (no department name), per SM ruling.
- The explicit reap call in `remove_teacher_from_license` is backed by the `save()` override, so plant P3 must no-op `end_memberships` itself, not only the explicit call.
- An empty list is rendered by the house renderer as `data: {}`.

## What is not in F1
No content (lessons, library, tags); the registry is empty. No department cap per school (500 members per department is a safety bound). No email for leaving the school, a delete, a rename or a switch.

## Runs
- Slice 2 gate: departments + route coverage + swagger groups + rollback defaults + student map + h273: Ran 156 OK.
- Slice 3 gate: departments users billing audit classrooms rollback-defaults student-map: Ran 5818, one failure caused by my helper unsetting (not blanking) the env flags; rerun OK.
- Near run on 41f8b028c: departments users billing audit AutoGrader classrooms students + rollback defaults + 67 guard labels: Ran 8184, skipped=19, one failure (cache key map), fixed in 41f8b028c.
- Near run on the merged tree: see the SM message for the Ran line.
- `makemigrations --check`: no changes after the merge. Test slot: `gap-test-slot`, `--parallel 3`, flags blank, `settings_worktree`, shared sm_ci_env.
