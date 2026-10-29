# H-1 Stage 3 — targeted invalidation: evidence

Backlog item: `docs/HARDENING_BACKLOG.md` H-1. Plan:
`docs/H1_STAGE3_WILDCARD_REMOVAL_PLAN.md`. This document covers plan §4
steps 1–3 and the verification gate for them: finding the real stale-cache
gaps, fixing them with per-viewer generation bumps, and proving the fixes.

**Status: step 3 implemented and gated. H-1 stays OPEN.** The legacy
wildcard clearing is still in the code, on the owner's instruction
(2026-09-16: "Do not remove the old wildcard mechanism yet"). Removing it
is plan step 4 and needs its own approval.

## 1. Method

- **Freshness matrix** (`AutoGrader/tests_cache_matrix_support.py`). For
  each cached read and viewer: read it (warms the cache), run the real
  mutation, read again (cached answer), then read with the cache bypassed
  (the truth). The verdict is FRESH (truth moved and the cache moved with
  it), UNAFFECTED (neither moved), STALE (truth moved, cache did not) or
  SPURIOUS (cache moved, truth did not). A read whose payload differs
  between two immediate reads is refused rather than guessed.
- **Legacy wildcards disabled** during every matrix run, so only the
  generation counters are in play. The results therefore show what users
  will see once the wildcards are removed.
- **Production write paths only.** Mutations go through the real endpoint,
  service function or management command. Fixture rows carry only fields
  production sets (plan §0); the one exception is the P5 credit bucket,
  created with the fields every top-up path writes (see its test).
- **Real Redis and real PostgreSQL** throughout.
- The harness proves itself first
  (`AutoGrader/tests_cache_matrix_selftest.py`): an uninvalidated write is
  reported STALE, a precise bump FRESH, a bump without a data change
  UNAFFECTED. Mutating the harness (forcing FRESH; making the bypass a
  no-op) fails those self-tests.

Baseline before any change: full suite on the Stage 3 parent `284a4b3`,
4,170 tests OK (skipped 21), exit 0
(`docs/evidence/h1_stage3/baseline/summary.txt`).

## 2. Confirmed stale-cache cases: 12

Each case was first proven STALE against the unfixed code (commits
`e61bf0e`, `31a3158`), then fixed (`5e09d75`), then re-proven FRESH by the
same test.

> **Count correction.** The progress report sent on 2026-09-16 said "10
> confirmed stale-cache bugs" above a table listing 12. The correct count is
> **12**: G1, G2, G3, G4, G5, G6, G8, G9, P1, P3, P4, P5.

> **Correction: G2 was not stale in production.** Commits `e61bf0e` and
> `31a3158` and the 2026-09-16 report said G2 was stale under both
> mechanisms. It was not. The StudentCourse receiver runs the legacy
> `*studentadmin*` wildcard, a substring pattern that matches the
> `studentadmins:` keys, so a withdrawal did clear them. The claim came from
> searching for the literal prefix. The same check showed that G3's other
> students (`studentsubmissions:*`) and G4's school admins (`*user*`) were
> also covered by a wildcard. The code comments and test docstrings were
> corrected in the gated commit.

The table describes what each viewer saw with the legacy wildcards
disabled, which is what production would show once they are removed.

| Case | What went stale | Who saw it | Test |
|---|---|---|---|
| G1 | Publishing or editing an assignment | The course teacher's and enrolled students' assignment lists | `assignments/tests_cache_matrix_g1.py` |
| G2 | Withdrawing a student | **Security:** the withdrawn student's cached course summary kept returning 200 with course data; the truth was 404 | `dashboard/tests_cache_matrix_g2.py` |
| G3 | Publishing all grades | Every student in the batch except the first | `assignments/tests_cache_matrix_g3.py` |
| G4 | Moving a teacher to another school | **Security:** the old school's admin kept a cached 200 for that teacher; the truth was 404 | `users/tests_cache_matrix_g4.py` |
| G5 | Renaming a course | Enrolled students' cached course | `classrooms/tests_cache_matrix_g5.py` |
| G6 | Editing a school-owned session | Everyone, including the admin who made the edit | `classrooms/tests_cache_matrix_g6.py` |
| G8 | A user editing their own profile | Every superadmin's cached user list | `users/tests_cache_matrix_g8.py` |
| G9 | Deleting an AI generation session | The owner's own session list | `assignments/tests_cache_matrix_g9.py` |
| P1 | Claiming a submission for grading | The teacher's submission list, for the whole grading run | `students/tests_cache_matrix_p1.py` |
| P3 | Adding a message to a generation session | The owner's cached session, which nests the messages | `assignments/tests_cache_matrix_p3.py` |
| P4 | The four data-repair management commands | Teachers' lists and school admins' dashboards | `assignments/tests_cache_matrix_p4.py`, `assignments/tests_cache_matrix_p4_all_commands.py` |
| P5 | Granting or consuming credits | The user's own cached `users/me` balance | `users/tests_cache_matrix_p5.py` |

**Stale in production today: P1, P3, P4 and P5.** Those writes fire no
signal (`QuerySet.update()`, `bulk_update`) or had no receiver, so neither
mechanism reached them. The other eight, G2 and G4 included, were covered
by a legacy wildcard and would have become stale only when the wildcards
are removed. The security-relevant G2 and G4 were therefore not live leaks.
Without these fixes, removing the wildcards would have created them.

## 3. Items in the plan that are not bugs

Each was checked against the current code before any test or fix was
written. They are recorded so nobody re-investigates them.

| Item | Why it is not a live gap |
|---|---|
| G7 — school changes not reaching superadmins | `SchoolViewSet.list` and `retrieve` are custom overrides that compute every response live; neither touches the cache. The separate superadmin `dashboard/schools` family is keyed on `SCOPE_ANY_SCHOOL`, which `clear_school_cache` already bumps. |
| G10 — `my_students` staleness | `student-course/my-students` is not cached at all. |
| G11 — reassigning a course's teacher | No production write path (API, admin action or management command) changes `Course.teacher` after creation. |
| G12 — `CourseCategory` has no receiver | The viewset is unrouted (backlog H-6), so no reachable write exists. |
| P2 — Google sign-in PENDING → ENROLLED | Built and run against the real endpoint: the result was FRESH. The `user.save(update_fields=...)` just before the promotion always bumps the student's own generation, which is the only generation their cached course list depends on. The test was removed rather than kept as a false claim. |

`strip_duplicate_option_letters` (one of the P4 commands) repairs data
that no cached payload displays: every read that shows options renders
them through `_strip_leading_option_letter`, which already removes every
leading marker. Its coverage is the wiring test in §5.

## 4. Fixes

All are per-viewer or per-entity generation bumps. None adds a wildcard,
and no cache key was widened.

| Case | Change |
|---|---|
| G1 | `assignments/signals.py` `_bump_assignment_scopes`: bump the **course's** teacher (`Assignment.teacher` is never set), plus every enrolled student, in one query and one pipelined round trip. |
| G2 | `dashboard/views.py` `StudentAdminDashboardView` `summary`, `assignments`, `overview`: build the keys with `versioned_key`, scoped to the student. No new receiver: enrolment changes already bump the student. |
| G3 | `students/signals.py` `invalidate_submission_caches_bulk`, called from `publish_all_grades`: every newly published student in one pipelined call. |
| G4 | `users/signals.py` `viewer_scopes_for_users`: also bump the SCHOOL_ADMIN users of the old and the new school. |
| G5 | `classrooms/signals.py` `_course_scopes`: also bump every enrolled student. Course, Topic and enrolment receivers all route through it. |
| G6 | `classrooms/signals.py` `clear_session_cache`: bump the school's admins and teachers, `created_by` and every superadmin. |
| G8 | `users/signals.py` `clear_user_cache`: bump every superadmin; `Settings` gets the same viewer fan-out as `CustomUser`. |
| G9, P3 | `assignments/signals.py`: the session receiver bumps its owner; a new receiver on `AssignmentGenerationMessage` does the same. |
| P1 | `students/services.py` `_claim_submission_for_grading`: a successful claim calls `invalidate_submission_caches`, as the FAILED release already did. |
| P4 | `assignments/signals.py` `bump_assignment_course_scopes_bulk`, called once per batch from all four commands' `_flush`. |
| P5 | `billing/signals.py`: a receiver on `CreditBucket` bumps the wallet owner. It is cache invalidation, not the accounting roll-up that file warns against; every `CreditBucket` write site uses `create()` or `save()`. |

Shared helpers `superadmin_user_ids()` and `school_admin_user_ids()`
(`users/signals.py`) keep the G4, G6 and G8 fan-out in one place.

**Performance fix found by the gate.** `backfill_assignment_rigor` loads
rows with `.only(...)`, which deferred `course_id`; reading it for the P4
bump cost one query per assignment (8 queries for 1 row, 17 for 10).
Loading `course` makes it flat at 7 (`117c296`).

## 5. Verification gate

### Freshness: every case now FRESH, with isolation checked

Every matrix test asserts the affected viewers are FRESH (with
`expect_changed`, so a test cannot pass by the truth never moving) and at
least one unrelated tenant or role UNAFFECTED:

- G1: another teacher and a student outside the course.
- G2: the withdrawn student's cached summary now goes 200 → 404 together
  with the truth; a classmate's summary is UNAFFECTED.
- G4: the old school's admin goes 200 → 404; the new school's admin sees
  the teacher.
- G5, G6: a student outside the course; an admin at another school.
- G3, G9, P1, P3, P4, P5: an unrelated teacher.
- G8: two superadmins, both FRESH.
- P4 (`backfill_assignment_rigor`, `repair_question_blooms_levels`): the
  school admin's teacher dashboard FRESH, another school's UNAFFECTED.
- P4 wiring, all four commands: exactly the owning teacher, enrolled
  student, course and school generations move; the other school's four do
  not.

**G4 note.** The new school's admin gaining access is not a
mutation-sensitive check. A 404 is never written to cache (the mixin
caches only successful reads), so that direction is fresh with or without
the fix. The security-relevant direction, the old school's admin losing
access, is mutation-tested below.

### Mutation: 17 of 17 caught

Each protection was disabled on its own, the matching test was run, and
the file was restored by an md5-verified copy (never `git checkout`).

| Mutant | Result |
|---|---|
| G1: no enrolled-student fan-out | enrolled student's list STALE |
| G2: summary key unversioned | cached 200 vs truth 404, STALE |
| G3: bump only the first student | one student STALE |
| G4: no school-admin fan-out | old school admin's view STALE (cached 200, truth 404) |
| G5: no enrolled-student fan-out in `_course_scopes` | enrolled student STALE |
| G6: no school-admin fan-out for sessions | admin's own list STALE |
| G8: no superadmin fan-out | both superadmins STALE |
| G9: no session owner bump | owner's list STALE |
| P1: no invalidation on a successful claim | teacher's list STALE |
| P1 retry: same mutant, claim → fail → re-claim | teacher's list STALE on FAILED |
| P3: no message receiver bump | owner's session STALE |
| P4 `strip_html_from_assignment_titles`: no bump | teacher's list STALE |
| P4 `backfill_assignment_rigor`: no bump | wiring fails; school dashboard STALE |
| P4 `repair_question_blooms_levels`: no bump | wiring fails; school dashboard STALE |
| P4 `strip_duplicate_option_letters`: no bump | wiring fails |
| P4 backfill: `course` dropped from `.only()` | query flatness fails (8 != 17) |
| P5: no `CreditBucket` bump | user's `me` STALE |

### Concurrency (`AutoGrader/tests_cache_matrix_concurrency.py`)

Real threads released by a barrier; each closes its own DB connection.

- `publish-all-grades` racing 12 students reading their submission lists:
  every student's read after the burst shows the published result.
- 12 students enrolled into one course at once: 12 rows committed, and
  the teacher's cached course shows exactly 12 students.

### Failure and retry

- **Redis outage** (`AutoGrader/tests_cache_matrix_failure_probe.py`): a
  `ConnectionError` forced on every path `bump_many` can take during a real
  G1 publish. The write succeeded and landed. `bump_generation` already
  catches every exception by design ("NEVER RAISES"), so the new receivers
  inherit that. After Redis returns, the next mutation bumps and the next
  read is current.
- **Celery retry** (`students/tests_cache_matrix_p1.py`): claim, fail,
  claim again. The teacher's cached list moves FAILED → RUNNING.

### Scale (`AutoGrader/tests_cache_matrix_scale.py`)

The G1 publish at 30 and at 300 enrolled students:

| Students | Legacy on: SCAN | Legacy on: queries | Legacy off: SCAN | Legacy off: queries |
|---|---|---|---|---|
| 30 | 9 | 15 | 1 | 15 |
| 300 | 9 | 15 | 1 | 15 |

The query count does not grow with the roster, and the bumps go in one
pipelined round trip. The one SCAN left is `assignments/pdf_cache.py`
clearing that single assignment's rendered PDFs, which plan §2 keeps.

### Before/after Redis invalidation, all 12 paths

`AutoGrader/tests_cache_matrix_measurement.py` runs every fixed write path
with the legacy wildcards on, then off, against 4 bystander schools with
32 cached reads between them. "Gone" counts bystander entries deleted.
"Cold" counts bystander reads that missed the cache afterwards, which also
catches invalidation moved into generation bumps. Cold reads are split
into the 28 keyed only on the bystander user (T) and the 4
`course/my-courses` reads keyed on the global generation (G).

| Path | ON: SCAN | ON: DEL | ON: gone | ON: cold T | OFF: SCAN | OFF: DEL | OFF: bumps | OFF: gone | OFF: cold T | cold G (both) |
|---|---|---|---|---|---|---|---|---|---|---|
| G1 publish assignment | 9 | 32 | 32 | 28 | 1 | 0 | 7 | 0 | 0 | 4 |
| G2 withdraw student | 11 | 32 | 32 | 28 | 0 | 0 | 7 | 0 | 0 | 4 |
| G3 publish all grades | 7 | 24 | 24 | 20 | 0 | 0 | 6 | 0 | 0 | 4 |
| G4 move teacher | 9 | 32 | 32 | 28 | 0 | 0 | 8 | 0 | 0 | 4 |
| G5 rename course | 12 | 32 | 32 | 28 | 0 | 0 | 7 | 0 | 0 | 4 |
| G6 rename school session | 7 | 24 | 24 | 20 | 0 | 0 | 6 | 0 | 0 | 4 |
| G8 user renames self | 9 | 32 | 32 | 28 | 0 | 0 | 7 | 0 | 0 | 4 |
| G9 delete generation session | 1 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 0 |
| P1 claim submission | 7 | 24 | 24 | 20 | 0 | 0 | 5 | 0 | 0 | 4 |
| P3 add generation message | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 0 |
| P4 repair titles | 0 | 0 | 0 | 0 | 0 | 0 | 7 | 0 | 0 | 4 |
| P5 grant credits | 0 | 0 | 0 | 0 | 0 | 0 | 1 | 0 | 0 | 0 |

- **With the wildcards on**, one school's mutation deletes 24–32 of the 32
  cached entries belonging to four unrelated schools.
- **With only the targeted bumps**, every path deletes 0 bystander entries
  and leaves 0 tenant-keyed bystander reads cold. The broad clearing is
  gone, not moved: 1–8 generation bumps per mutation, all on the acting
  school's own entities. (G4, G6 and G8 bump one more in the OFF run
  because the ON run's superadmin still exists.)
- **One broad effect remains, and it predates this work.**
  `course/my-courses` is keyed on the global generation (Stage 2 design,
  backlog **H-15**), so the 9 paths that bump `global` make every
  student's `my-courses` cold. Two of those 9, P1 and P4, invalidated
  nothing at all before this change; they now bump the standard
  submission and assignment scopes, global included, as the approved
  plan specified. H-15 remains the owner's separate decision.

### Security and tenant isolation (`AutoGrader/tests_cache_matrix_tenant_isolation.py`)

School A runs assignment edit, publish-all-grades, course rename, user
rename and student withdrawal back to back. School B's six cached reads
(admin user and session lists, teacher course and assignment views,
student course and submission lists) are byte-identical before and after.

### Regression

- Interim, after the fixes (`5e09d75`): `assignments students classrooms
  users dashboard billing`, fresh DB, 3,083 tests OK (skipped 17), exit 0.
  An earlier attempt at the same run reported one failure and one deadlock
  in `dashboard.tests_dashboard_remediation`. The cause was test runs I
  started against the same test database while it was running; the clean
  rerun alone passed. Not a code defect.
- All 23 Stage 3 tests that existed at the merge of beta `b744c9f`
  (`a3d2801`), fresh DB: OK.
- **Strict final gate** on `67ab618`: §6.

## 6. Strict final gate

**PASS** on `168d57e` (2026-09-29): `scripts/strict_gate.py run 168d57e
h1-stage3-gate10-rerun --runs 2`, run under the machine lock. Two consecutive
serial (`--parallel 1`) runs, each on a fresh database: `pre-commit run
--all-files`, `check_migration_safety.py`, `manage.py check` and `makemigrations
--check` all exit 0; full suite **4775 tests OK** (skipped 26) both times;
tree fingerprint identical before and after each run; 0 leftover databases or
connections; `beta` unchanged at `197aa46` throughout. Report and logs:
`docs/evidence/h1-stage3-gate10-rerun/` (its gate table points back into this
document for gates 1-9; gate 8 is PARTIAL, no live gunicorn or deployed run).

**First attempt FAILED** (`docs/evidence/h1-stage3-gate10/`, on `65202ee`): run 1
had one failure, `billing.tests.test_billing_period_anchoring`
`test_local_period_matches_stripe_exactly_across_twelve_renewals`, which failed
on any run dated the 29th-31st. It was pre-existing on beta `197aa46`
(reproduced alone there) and a bug in the test, not H-1: its fake Stripe
chained `+1 month` steps that clamp at Feb 28. Fixed test-only on
`task/fix-renewal-anchor-test` (`3802d64`, independently verified), merged here
at `168d57e`, then the gate was re-run.

## 7. What remains before H-1 closes

1. **Plan step 4:** remove every `delete_cache_patterns` call, both
   definitions, `batched_cache_invalidation` and the warn-once path; rewrite
   the tests that assert wildcard behaviour; add the guard test that fails
   if non-test code calls a wildcard delete. Needs owner approval.
   **Approved 2026-09-28; implemented on `task/h1-step4-wildcard-removal`,
   evidence in `docs/evidence/H1_STEP4_WILDCARD_REMOVAL_EVIDENCE.md`** (the
   status-summary family in §8 was versioned first, and its wildcards-OFF
   tests now assert FRESH).
2. After step 4: two concurrent full suites (H-9 rule) and a strict gate on
   the committed tree.
3. Owner approval, then landing on beta.

## 8. Cache families added on beta since `b744c9f`

**Merge correction.** Merge commit `0ca0404`'s message says "Merge beta
(4b902fc)", but its second parent is local beta **`be78221`** (4b902fc plus
authz-oauth-takeover, the gate-10 baseline and h39-network-guard). Only the sha
in the message is wrong; the merged content is be78221. All evidence below is
against that tree.

**New families.** Every `cache.set` / `get_or_set` / `versioned_key` that beta
added between `b744c9f` and `be78221` (non-test code) was listed. Exactly one
production cache family is new; the other hit is a probe key in
`scripts/strict_gate.py`.

| Family | Introduced by | Written with | TTL |
|---|---|---|---|
| `studentadmins:user_id__<student>:view__status_summary:all` and `...:course__<course>` (`dashboard/views.py` `status_summary`, URL `student-status-summary`) | `24c0a7b` "Add unified assignment status-summary endpoint" | raw `cache.set`, NOT generation-versioned | 15 min |

It was absent from `AutoGrader/tests_cache_invalidation_coverage.py`'s key map,
and beta's own tests (`dashboard/tests.py`) call `cache.clear()` first, so no
existing test measured its freshness. The Stage 3 targeted invalidation predates
it.

**Freshness matrix** (`dashboard/tests_cache_matrix_status_summary.py`, 24 tests,
real Postgres + real Redis, every mutation through the real route or service).
Viewer: the enrolled student, both variants; an unrelated student on both
variants is asserted UNAFFECTED in every cell.

| Write path | Wildcards ON (production today) | Wildcards OFF (step-4 simulation) |
|---|---|---|
| teacher publishes a draft | FRESH | STALE |
| student submits | FRESH | STALE |
| teacher publishes one grade | FRESH | STALE |
| publish-all-grades | FRESH | STALE |
| overdue due date extended | FRESH | STALE |
| assignment unpublished | FRESH | STALE |
| assignment deleted | FRESH | STALE |
| enrolled into a new course | FRESH | `all` STALE |
| withdrawn (PATCH) | FRESH | `all` STALE |
| removed from course (DELETE) | FRESH | `all` STALE |
| course deactivated | FRESH | `all` STALE |
| withdraw → publish → re-enrol | FRESH | STALE: the entry orphaned at withdrawal is served again on return |

On the four enrolment rows, the `?course=` variant reads FRESH even with the
wildcards off, only because the endpoint's access check runs before the cache
lookup (the student gets 404 instead of a cached body).

**Conclusions.**
1. **No live bug.** With the wildcards in place (the production code), every
   write path invalidates this family: every cell is FRESH. The tests prove
   the freshness, not which pattern does it; by reading the receivers, each
   path reaches a legacy wildcard that matches the key (`*studentadmin*`).
2. **Step-4 prerequisite, proven.** With the wildcards off, the family goes
   stale on every path. Step 4 (wildcard removal, approved 2026-09-28 as a
   separate landing) must first put this family on per-viewer generation bumps.
   The wildcards-OFF tests assert STALE deliberately: they fail by design once
   the family is versioned, which forces them to be flipped to FRESH in that
   change rather than silently passing.

Run: 24 tests, OK (`nice -n 10`, no `--parallel`), on `0ca0404`.


## 9. Rework: an enrolment no longer bumps every classmate (Design A)

**Why.** G5's fix (§4) made the StudentCourse receiver bump every student
enrolled in the course, so each classmate's cached course list, detail and
my-courses showed a new or departed student at once. That makes one
enrolment O(class size) and a roster import O(n²). The step 4 targeted run
hung for 19 hours in the H-25 roster-cost harness at its default scale
(thousands of enrolments into one course, each bumping every earlier one). The per-module
pass on step 4 (`docs/evidence/h1-step4-per-module-8063c44/`) cleared step 4
itself: identical command counts with and without it.

**Design A (SM-approved 2026-09-30), commit `cc14bb0`.**

**Verification status: Design A is NOT yet verified.** The VERIFIED record
`6cf8c1c` (§5–§6) covers stage 3 before this rework. 1a verifies the rework
together with step 4 on the batch-2b candidate.

- An enrolment write bumps a fixed five scopes: the student's `usr`,
  `global`, and the course's owner scopes (`crs`, the teacher's `usr`, the
  school's `sch`). It no longer bumps classmates.
- A student's cached course **list** and **detail** carry the `crs`
  generation of each course the key covers (detail: that course; list:
  every course the student can see), as well as the student's own `usr`
  (`UserCacheMixin.extra_cache_scopes`, overridden on CourseViewSet). A
  teacher's or admin's keys are unchanged.
- The extra generations are read in **one** `MGET` (`versioned_key(...,
  batched=True)` through `get_generations`). A missing or corrupt counter
  reads as the default, and an unreachable Redis returns the defaults without
  raising, the same contract as `get_generation`.
- **my-courses gets no `crs` scope** (deviation approved by the SM): its key
  is already `usr`+`global`, and every enrolment write bumps `global`. A
  comment at the key records that its roster freshness depends on that bump.
- Course and Topic writes keep the per-student fan-out. A rename or topic
  edit is not a per-row roster operation, and those paths were verified in
  §5 and are unchanged.

**Freshness, through the real endpoints**
(`classrooms/tests_cache_course_roster_scope.py`, legacy wildcards
disabled). After an add (`course-students` POST) and a removal
(`course-remove-student` DELETE):

| Read | Viewer | Verdict |
|---|---|---|
| course list | classmate | FRESH |
| course detail | classmate | FRESH |
| my-courses | classmate | FRESH |
| course detail | teacher | FRESH |
| course detail | student of another course | UNAFFECTED |

**Cost of a write, pinned.** One enrolment into a class of 30 and into a
class of 300 (the class filled with `bulk_create`, which sends no signals):
both record exactly the five scopes above and the same Redis commands,
exactly 10 commands in one pipeline, `{"SET": 5, "INCRBY": 5}` (SET NX plus
INCRBY per scope; redis-py sends a pipelined incr as INCRBY). On the
batch-2b candidate, which carries H-25, the same enrolment replays its bump at
commit: `{"SET": 10, "INCRBY": 10}`.

**Cost of a cache hit, measured** (the same test file, `[roster-scope read
cost]`). A hit compared with a teacher's hit on the same URL, both after a
warm-up:

| Hit | Teacher | Student | Difference |
|---|---|---|---|
| course list | 0 queries; Redis GET 2, SET 1 | 1 query; Redis MGET 1, GET 1, SET 1 | +1 query (the student's enrolled course ids); same 3 Redis commands |
| course detail | 0 queries; Redis GET 2, SET 1 | 0 queries; Redis MGET 1, GET 1, SET 1 | none |

The student's `MGET` replaces the `GET` of their own generation, so a hit
costs no additional Redis round trip. The plan estimated +1 query and +1
round trip; the measured cost is +1 query on the list only. The `SET` is
the same on both sides, so it is not part of the difference: it is the activity middleware's heartbeat claim
(`cache.add`, a SET NX on every authenticated request, `users/middleware.py`).
Of the two teacher `GET`s, one is the teacher's own generation and one the
cached payload. The student reads the payload with `GET` and every
generation with one `MGET`.

**Static sweep** (`classrooms/tests_course_roster_scope_sweep.py`). This
test finds every serializer that declares a roster field (`students`,
`student_count`) or nests or subclasses one. It then finds every cached view
class (a `UserCacheMixin` viewset, or one calling `versioned_key`) that uses
such a serializer. Each must key on `SCOPE_COURSE` or `SCOPE_GLOBAL`, or be
allow-listed with a reason. An allow-list entry that no longer matches
fails too. Four entries remain, each for a viewer who is never a student:
SchoolViewSet, CourseCategoryViewSet (never routed, H-6), and the superadmin
and school-admin dashboards. A guard test on synthetic source proves the
sweep flags a nested serializer, a missing hook and a viewer-only
`versioned_key`.

**Mutation battery**, on the batch-2b candidate `018351f`, which carries this
rework, step 4 and batch-2a's retire (A). It ran in a disposable detached
worktree with its own test database, and each mutant was restored and
sha256-checked before the next:

| Mutant | What it breaks | Caught by |
|---|---|---|
| control | nothing (unmutated `018351f`) | 16 tests OK |
| M1 | the StudentCourse receiver bumps every classmate again (the old fan-out) | the pinned write cost (`test_one_enrolment_bumps_five_scopes_at_a_class_of_30_and_of_300`) |
| M2 | a student's course detail loses its `crs` scope | both classmate freshness tests, the detail key-shape test, the hit-cost test (4 failures) |
| M3 | a student's course list loses its `crs` scopes | both classmate freshness tests, the list key-shape test, the hit-cost test, the silent-withdrawal test (4 failures, 1 error) |
| M4 | my-courses drops `global` from its key | both classmate freshness tests, and the static sweep (`test_every_roster_bearing_cached_view_keys_on_a_roster_scope`) |
| M5 | CourseViewSet's `extra_cache_scopes` override is removed | freshness, both key shapes, hit cost, silent withdrawal, and the static sweep (6 failures, 1 error) |
| M6 | the batched generation read ignores the counters | both freshness tests, both batched-read tests, the hit-cost test (5 failures) |

6 of 6 mutants caught, each by the test aimed at it. M2 to M6 also fail the
end-to-end freshness tests. M1 does not, and is not expected to: restoring the
fan-out makes a write slow, not a read stale, which is why the write cost is
pinned. Every restore was sha256-checked against the blob; the
disposable worktree and its database were removed afterwards.

**Regression.** The 47 stage 3 cache, probe and regression modules, one at
a time, each under `timeout -k 60 600` at `cc14bb0`:
46 OK and 1 FAILED, none timed out, 10:44 to 11:04. The failure was the
matrix self-test's silent withdrawal, which Design A makes fresh by
construction; it was fixed in `2987700` (a silent rename instead, plus a test
pinning the withdrawal behaviour), and both modules then passed (18 tests) (`docs/evidence/h1-stage3-per-module-cc14bb0/`).

**On the batch-2b candidate.** The 109-module pass at `8b1c0cf`
(`docs/evidence/batch-2b-candidate-per-module-8b1c0cf/`) gave 107 OK. The
other two failed from retire (A) fixture fallout: students with no
`last_login` were enrolled PENDING. `018351f` fixed both, the same way as
`877c900`, and both then passed. That pass adds the per-module regression
for this rework on top of batch-2a.
