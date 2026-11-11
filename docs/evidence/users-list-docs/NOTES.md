# Users list Swagger docs, beta (brief 6, SM -cf, 2026-11-11): run notes

The owner approved this as a bug fix to beta (11 Nov). It changes docs only,
not behavior. Base origin/beta 4a20bea4b, branch task/users-list-docs,
worktree Grade-Automator-Plus-users-docs, test DB test_users_docs_beta.

## Environment (declared before any run)

- venv: ~/Documents/Virtualenvs/users_docs_env, NEW for this task (the old
  h284_deps_env is gone). Built with the system python3.12 (3.12.10) from
  requirements.txt and requirements-dev.txt. Per the standing rule, I delete
  it when the task is finished, after checking that nothing is using it.
- Every test run: --settings=settings_worktree, --keepdb, --noinput, under
  flock ~/.machine-fullsuite.lock.

## Probe M0 (2026-11-11, no DB, manage.py shell): what two inputs mean today

- `apply_ordering(CustomUser.objects.all(), ["username"])` raises FieldError
  "Cannot resolve keyword 'username'" (CustomUser sets `username = None`), so
  `?ordering=username` is a 500 on beta 4a20bea4b. Reported to the SM. Ruling
  1: the SM is asking the owner; until then, leave it and use no example
  with it.
- IsoDateTimeField().clean("2026-11-01") = 2026-11-01 00:00:00+00:00: a date
  alone means midnight UTC at the start of that day (SM ruling 3: say so in
  the docs).
- Rulings 2, 4 and 5 (SM -cf): replace the exact Q-S2 sentence check at
  users/tests_user_list_signals.py:429 with a check on the plain wording;
  document that each enrollment filter is checked on its own; the
  has_hit_80_percent finding goes to the owner, and the docs stay true to the
  code.

### R1 red (declared): the new tests on the unchanged base

Label users.tests_user_list_docs, 7 tests (build_suite countTestCases = 7).
UserEnrollmentFilter has 20 filters. Expected: Ran 7, FAILED (failures=49,
errors=0). Every test fails, and each failing subtest counts once:
- every filter has its own description: 20 (none has one);
- signal filters say teacher and super admin: 5;
- date filters say "included": 4;
- every ordering key has its own line: 14 (the 4 column keys, the 9 sort
  keys and signals);
- highest first and the example: 1;
- plan rank and credits: 1;
- jargon: 4 (ascending, descending, null and raw appear; tiebreak and
  tie-break do not).

R1 result (R1_red.log): Ran 7, FAILED (failures=44, errors=4). Every test
fails on the base, so the tests are red as intended.
**Prediction wrong** (declared failures=49, errors=0):
- The 4 date-filter subtests were ERRORS (KeyError 'description'), not
  failures. The base gives those parameters no description key at all, and
  the test read it with [] instead of .get(). This is a test defect, fixed
  after R1 (both direct reads now use .get()). It does not change what the
  test checks.
- "Every filter has its own description" failed 19 times, not 20.
  enrollments__enrollment_status__in already carries django-filter's generic
  sentence "Multiple values may be separated by commas." (6 words), which
  passes the check. That was not foreseen.
- So 49 - 4 (now errors) - 1 (passed) = 44 failures; the 4 errors make 48.
The other counts matched: signal filters 5, ordering key lines 14, highest
first 1, plan rank and credits 1, jargon 4.
M1_base_descriptions.txt holds every base description, as generated
(manage.py shell, SchemaGenerator, no DB).

## The fix (docs only)

- users/filters.py: FILTER_HELP, one plain help_text for each of the 20
  filters, set on UserEnrollmentFilter.base_filters[...].extra. Only
  drf-spectacular reads it; no filtering changes.
- users/views.py: ORDERING_HELP replaces the old `ordering` description. It
  gives one line per key, says `-` means highest first, and gives the example
  `-signals,email` (SM ruling 1: no example with username). The username line
  says honestly that it is a 500 today (pending the owner).
- users/tests_user_list_signals.py: SM ruling 2. The exact Q-S2 sentence is
  replaced by the same rule in plain words, compared with whitespace folded.
  billing's ORDERING_DOC and billing/tests/test_intent_signals.py are
  untouched.

### R2 green (declared)

Labels users.tests_user_list_docs, users.tests_user_list_signals and
billing.tests.test_intent_signals: 55 tests (countTestCases). Expected: Ran 55,
OK. The billing module is included to show the leads endpoint's own ordering
text (ORDERING_DOC) is unchanged.

R2 result (R2_green.log): Ran 55, FAILED (failures=1). **Prediction wrong.**
test_it_spells_out_the_plan_rank_and_the_credits failed: the text wraps the
line between "Trial," and "Beta", so the substring check with one space
missed. This is a test defect: the description is Markdown with wrapped
lines, which Swagger joins. Fixed by folding whitespace in that test (the
same as the signals test does), with no change to the docs.

### R2b (declared): the same 3 labels, 55 tests. Expected: Ran 55, OK.

R2b result (R2b_green.log): Ran 55, OK, as declared.
Pre-commit on the 4 changed files: black reformatted users/filters.py
(line splits only, no text changed); every other hook passed.

### N1 near (declared)

The users app plus the beta guard list: guards60.txt, the same 60 labels as
briefs 4 and 5. The SM's "67-label" list is the staging list; 59 of the 66
standing labels exist on beta, plus AutoGrader.tests_live_server_backlog_guard
= 60. The others are not on beta. countTestCases = 2165. Run serially (no
--parallel), so the redis isolation fork tests run.
Environment: ~/Documents/Virtualenvs/users_docs_env (Python 3.12.10,
Django 5.2.17, DRF 3.18.2, drf-spectacular 0.28.0),
--settings=settings_worktree, --keepdb, --noinput, under flock
~/.machine-fullsuite.lock. Expected: Ran 2165, OK (skipped count not
predicted).

## OWNER rulings (via SM -cf, 2026-11-11): no longer docs only

1. username: REMOVE it from COLUMN_KEYS (option a). `?ordering=username` is
   then ignored like any unknown key and leaves the enum. Test: 200, red
   before the fix.
2. Enrollment filters CHECKED TOGETHER: course and/or session given with
   status or status__in must match the SAME enrollment (one Exists with all
   the given lookups), still inside visible_enrollments. Each filter alone
   is unchanged. My reading, flagged to the SM: all four (course, session,
   status, status__in) go into the one Exists whenever two or more are
   given, so course + session together also means the same enrollment.
   enrollments__course__isnull stays as it is.
3. The 80% signal is not in this brief.

N1 (above) was declared and started on the docs-only tree before these
rulings arrived. Its result stands for that tree; N2 will cover the final
one.

### R3 red (declared): the ruling tests, before the behavior change

users.tests_user_list_enrollment_match, 10 tests (countTestCases). The tree
has the docs changes but no behavior change yet, so its behavior is beta's.
Expected: Ran 10, FAILED (failures=3, errors=3):
- username: 3 ERRORS (subtests username, -username, -signals,username: the
  test client re-raises the FieldError);
- the docs example `-signals,email`: passes;
- together: 3 FAILURES (course+status, session+status, status__in+course
  each also list `both`, which is withdrawn or completed only in B);
- alone: 4 pass (unchanged behavior);
- a teacher cannot match B's enrollment: passes (404 on the base too; it
  guards that the merge keeps visible_enrollments).

**N1 and R3 INTERRUPTED (2026-11-11, about 19:35).** The user switched Claude
profile, and that ended this session's background shells.
- N1 stopped part way through: no "Ran" line. The progress dots show one
  "E" before the stop, with no traceback, because the run never reached its
  summary. N1 has no result and is superseded by N2 (the final tree), which
  will show whether that error is real.
- R3 never started: it was waiting on the lock, and its log is empty. It is
  re-run below as R3, with the same declaration.

R3 result (R3_red.log): Ran 10, FAILED (failures=7). **Prediction wrong**
(declared failures=3, errors=3):
- username: 3 FAILURES, not errors. The view answers 500 itself ("500 !=
  200"), so the test client has nothing to re-raise. They are still red.
- The 3 together tests failed, as declared.
- test_a_teacher_cannot_match_another_teachers_enrollment_together FAILED
  (200 != 404), though it was declared to pass. **Test defect:** its first
  GET (the check that teacher A can see the student) fills the retrieve
  cache, which ignores query params (a known backlog item, brief 4). The
  filtered GET was then answered from that cached 200. Fixed with
  cache.clear() between the two GETs.

### R3b (declared): the same label after the test fix, still before the behavior change

Expected: Ran 10, FAILED (failures=6): the 3 username subtests and the 3
together tests. The teacher test now passes (404 on the base).
R3b result (R3b_red.log): Ran 10, FAILED (failures=6), as declared.

## The behavior fix (owner rulings 1 and 2)

- users/user_list.py: "username" removed from COLUMN_KEYS. It drops out of
  ordering_fields and the enum, and DRF's remove_invalid_fields ignores it.
- users/filters.py: ENROLLMENT_LOOKUPS. filter_queryset puts the four
  enrollment filters given into one Exists (visible_enrollments plus every
  lookup given); every other filter runs as before. filter_enrollment is the
  single-filter form, the same Exists as the old per-filter methods, which it
  replaces. The docs text now says the filters match together.
- users/views.py: the username line removed from ORDERING_HELP.
- users/tests_user_list_sorting.py: OLD_ORDERING_KEYS loses username, and the
  enum test now asserts username and -username are absent. This is the
  expected kill of the existing test by ruling 1.

### R4 green (declared)

Labels: users.tests_user_list_docs users.tests_user_list_enrollment_match users.tests_user_list_signals users.tests_user_list_sorting users.tests_user_enrollment_filter_oracle billing.tests.test_intent_signals.
countTestCases = 136. Expected: Ran 136, OK.
R4 result: Ran 136, OK, as declared.

## Lean rules from here (SM -98, 2026-11-11): one line per run, mismatches noted and not stopped on

- SM -98 confirmed my reading (course, session and status go into one Exists) and asked for a course+session test. OWNER: remove enrollments__enrollment_status__in (EnrollmentStatusInFilter is used nowhere else; the other "__in" hits in the repo are ORM lookups).
- R5 red (the new tests, against the base product files swapped in, then restored; sha256 OK): Ran 5, FAILED (failures=7). Every new test is red on beta's code.
- R6 green (the same 6 labels as R4): expected 137, Ran 137, OK.
- Note: users.tests_user_enrollment_filter_oracle still passes enrollments__enrollment_status__in in two tests. It is now ignored, so those rows still pass (nothing leaks), but they no longer test a filter. Left as they are.
- N2 near on 1c7d52730 (users + guards60, serial, users_docs_env: Python 3.12.10, Django 5.2.17, DRF 3.18.2, drf-spectacular 0.28.0, --settings=settings_worktree --keepdb, flock): expected Ran 2176.
  N2 result: Ran 2176, OK (skipped=5), as expected.
- SM review (brief 6 accepted): the oracle's 2 status__in rows switched to the single status filter (own-status now has a 404 control), and search gets plain text (DRF SearchFilter: icontains on first_name, last_name, email; each word must match one of them, so that sentence was added to the SM's wording). R7: oracle + users docs, expected 28, Ran 28, OK.
