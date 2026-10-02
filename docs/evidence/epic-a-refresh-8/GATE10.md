# Epic A staging refresh 8: Gate 10 (H-38 N3, command actor, licence-intent audit)

Recorded by Integration & Release (0b), 2026-10-02.

**Scope:** phase2/epic-a `ce0fd315`: three Epic A follow-ups merged since refresh 7 (`a63eb9aa`): H-38 tasks N3 with its follow-up test (`857d6a78`, `6bbb4ace`), the command actor (`8a206ba5`), the resolve-intent audit emit (`ce0fd315`), and the 08a docs note (`ca88188a`). No migrations, no model changes and no settings change against refresh 7.

**Command:** the strict full run under rules 12, 13, 16 and 17: `systemd-inhibit --what=idle:sleep:handle-lid-switch --who=GAP --why="GAP test run" --mode=block systemd-run --user --scope -q -p MemoryMax=12G -p MemorySwapMax=0 nice -n 10 flock ~/.machine-fullsuite.lock timeout -k 60 3600 python manage.py test --settings=settings_worktree --parallel 4 --noinput --verbosity 2`, with `PYTHONDONTWRITEBYTECODE=1` and RACE_COST, AUDIT_BENCH and ENABLE_GRADING_BENCHMARK unset. Whole-repo mypy (passed) and `makemigrations --check` (no changes) ran first.

| Tip | Start–end (WAT) | Result |
|---|---|---|
| `ce0fd315` | 2026-10-02 16:35:42–16:46:40 (658 s wall, 627 s tests) | **Ran 6233 tests, OK (skipped=30)**, exit 0, 0 FAIL, 0 ERROR, 0 blocked outbound, no suspend |

**Tests per app** (counted from the run's per-test lines; they sum to 6233):

| App | Tests | Change from refresh 7's Gate 10 |
|---|---|---|
| ai_processor | 830 | 0 |
| assignments | 663 | 0 |
| audit | 367 | +25 (the command actor's module) |
| AutoGrader | 533 | 0 |
| billing | 2071 | +23 (the intent-audit module and the H-28 phase tests) |
| classrooms | 415 | 0 |
| dashboard | 270 | 0 |
| students | 386 | +24 (H-38 N3's module with its follow-up test) |
| users | 698 | 0 |

**Why this run is a hard gate here:** the slices' own regressions were narrower. The command actor's ran audit, ai_processor, students and billing; the resolve-intent emit's ran billing and audit and had ONE failure (below), so that slice had no green regression of its own. This run is the full green run of all three on the final code, and it covers users, classrooms, assignments, dashboard and AutoGrader, which those regressions left out.

**The one failure in the resolve-intent slice's regression, and this run:** `billing.tests.test_h38_part2_removed_teacher_routes.RemovedTeacherRosterNameMatchTests.test_import_into_an_individual_course_creates_a_new_student` failed once at `84aeb2f1`. It is H-99: a student added without an email gets a placeholder address with a random 4-digit suffix, and the test's two same-named students drew the same one (1 in 10,000). It is a product defect on beta and the epic, fixed on the beta line (bundle 6) and reaching the epic by merge-down; it is unrelated to the slice. Both tests of that class passed in this run.

**Known limit carried into this refresh (H-101, the next Epic A slice):** an intent escalated by the stale-intent check and then again by a flow holding an old in-memory copy gets a second escalation event. No request can reach it.

**The machine:** shared with another project; its manager held their test runs for this run. One short serial GAP run (1a's H-85 delta check) and then two serial 6G runs ran beside it. Log: 8.3 MB, sha256 prefix `5769f598af9b8182`, kept outside the repo at `~/Documents/Projects/GAP-0b-runs/gate10_epic_r8.log`.

**After the run tip:** this record is the only change, so 0 non-docs files differ from `ce0fd315`.
