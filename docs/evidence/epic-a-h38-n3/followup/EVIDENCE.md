# H-38 N3 follow-up: a removed teacher who has since joined another school

**Author:** 1a. **Branch:** `task/epic-a-h38-n3-followup`, off
`phase2/epic-a` `857d6a78`, base-updated by 0b onto `ca88188a` (clean merge,
`eefdeb3d`). **Test-only** (SM ruling, 2026-10-02): v2's probe P1 from the
H-38 N3 verification, kept in the suite because it is the case a later
change to how the school is resolved would most likely break.

| Commit | What |
|---|---|
| `b9f090ce` | one test in `students/tests_h38_n3_refusal_audit.py`: `TheTeacherNowBelongsToAnotherSchoolTests.test_the_refusals_stay_with_the_courses_school` |
| `eefdeb3d` | base update onto `ca88188a` (0b) |

**What it pins.** A teacher removed from school A who now belongs to school
B is still refused on A's course. The refused run (whose actor is that
teacher, now a member of B) and the refused batch are both filed under A,
the course's school. A's admin reads both through the real school-admin
audit route; B's admin reads nothing.

**No production change.** Against the epic the branch differs only in the
one test module (and this folder).

## Gate (rule 15.4: the touched module alone)
| Gate | Result | Log |
|---|---|---|
| `students.tests_h38_n3_refusal_audit` at `eefdeb3d` | **24 OK** | `module_eefdeb3d.log` |

Run under 0b's grant with `--settings=settings_worktree`, an empty
`EXEMPT_EMAIL_DOMAINS`, and the wrapper `systemd-inhibit
--what=idle:sleep:handle-lid-switch … --mode=block systemd-run --user
--scope -p MemoryMax=6G -p MemorySwapMax=0 nice -n 10 timeout -k 60 1800`
(rules 12, 13, 16). No mutant was run: the SM asked for the test only, and
v2 reads the diff. The test is not vacuous by construction: it asserts the
event's school equals the course's and differs from the teacher's new one,
which fails if the run's event falls back to the actor's school.

The log's addresses are test fixtures only.
