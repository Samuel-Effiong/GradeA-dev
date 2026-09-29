# H-1 step 4 — removing the legacy wildcard cache invalidation: evidence

Backlog item: `docs/HARDENING_BACKLOG.md` H-1. Plan:
`docs/H1_STAGE3_WILDCARD_REMOVAL_PLAN.md` §2 and §4 step 4. Steps 1–3:
`docs/evidence/H1_STAGE3_TARGETED_INVALIDATION_EVIDENCE.md`. Owner approval
for step 4 as a separate landing: 2026-09-28.

**Status: step 4 implemented and gated on targeted runs. H-1 stays OPEN**
until the coordinator's full suite, the two concurrent full suites (H-9
rule) and the strict committed-tree gate pass, the branch is rebased onto
beta, and the owner approves landing (§9).

Commits on `task/h1-step4-wildcard-removal` (stacked on `dd2dc6a`, the gated
Stage 3 tip):

| Stage | Commit | What |
|---|---|---|
| 1 | `ffe90cc` | status-summary family versioned on `usr(student)`; wildcards-OFF tests flipped STALE → FRESH |
| 2 | `99124e4` | this inventory (§2), written before any deletion |
| 3 | `6ba0805` | the wildcards, both definitions, helpers and constant removed |
| 4 | `008b5ac` | tests that asserted wildcard behaviour rewritten; matrix runs the real code |
| 5 | `4691157` | guard test, proven to bite |
| 6 | this commit | targeted gate, mutation checks, this evidence |

## 1. Stage 1 — the status-summary family put on generations first

`StudentAdminDashboardView.status_summary` (`dashboard/views.py`) wrote
`studentadmins:user_id__<id>:view__status_summary:{all|course__<id>}` with a
raw `cache.set` (15 min), so only the legacy `*studentadmin*` wildcard ever
cleared it (Stage 3 evidence §8). Both keys are now built with
`versioned_key(..., [(SCOPE_USER, student.id)])`, exactly as the sibling
`overview` / `summary` / `assignments` keys were for G2. The `?course=`
access check still runs before the cache lookup.

**No new bump was needed, and no gap was found.** The counts read the
student's published assignments, own submissions and active enrolments.
Every one of the 12 write paths already bumps `usr(student)`: assignment
publish/unpublish/edit/delete via the G1 fan-out, submit via the submission
receiver, single and bulk grade publish via `invalidate_submission_caches`
and `_bulk` (G3), enrol/withdraw/remove/course-deactivate via the
StudentCourse and Course receivers (G2, G5). The versioned `student-overview`
control, which reads the same counts under the same scope, was already FRESH
on every row with the wildcards off, and stayed so.

`dashboard/tests_cache_matrix_status_summary.py`: the wildcards-OFF tests
that pinned STALE as the step-4 prerequisite now assert FRESH on all 12
paths, including withdraw → publish → re-enrol, with the unrelated student
UNAFFECTED on both variants in every cell. The key probe now also asserts
the student's live key moved to a new generation and the unrelated
student's did not. Run at stage 1 (wildcards still present): 24 + G2 = 25
OK; `dashboard.tests.StudentDashboardOverviewAPITest` 6 OK. In stage 4 the
suite was collapsed to one class running the real code (§4).

## 2. Inventory, recorded before deletion (at `ffe90cc`)

Every non-test reference to the wildcard mechanism. Line numbers are at
`ffe90cc` (stage 1 committed, nothing removed yet). Searched for
`delete_cache_patterns`, `delete_pattern(`, `batched_cache_invalidation`,
`_delete_now`, `_pending_patterns`, the warn-once flag, `*_PATTERNS`
constants, `scan_iter`/`iter_keys`/`keys(` against the cache.

### 2.1 Definitions (2) and helper machinery

H-4 recorded up to four drifted copies. Two remain: `students/signals.py`
and `assignments/signals.py` had already been switched to import the shared
helper (their comments at lines 14–20 and 28–35 record it), as has
`users/signals.py`.

| # | Where | What | Disposition |
|---|---|---|---|
| D1 | `AutoGrader/cache_utils.py:45` | `delete_cache_patterns` (shared) | removed, with the whole module |
| D1a | `AutoGrader/cache_utils.py:27` | `_delete_now` (the `cache.delete_pattern` loop, line 40; silently no-ops when the backend lacks `delete_pattern`, line 34) | removed |
| D1b | `AutoGrader/cache_utils.py:24, 64` | `_pending_patterns` ContextVar and `batched_cache_invalidation` (no production caller) | removed |
| D2 | `classrooms/signals.py:45` | local `delete_cache_patterns` copy (`cache.delete_pattern`, line 77) | removed |
| D2a | `classrooms/signals.py:35, 62–73` | warn-once path (`_warned_backend_lacks_delete_pattern`) | removed |
| D2b | `classrooms/signals.py:37` | `CACHE_UNAVAILABLE_ERRORS`, used only by D2's `except` | removed |

### 2.2 Call sites (11) and what replaces each

"Replaced by" is the generation bump that already runs in the same
function, immediately before the wildcard call. Each was put there and
proven by steps 1–3 with the wildcards disabled (the Stage 3 evidence
§2–§5); step 4 removes only the wildcard line.

| # | Where | Receiver / function | Patterns cleared | Replaced by (already in place) |
|---|---|---|---|---|
| C1 | `classrooms/signals.py:156` | `clear_school_cache` (School save/delete) | `*superadmin*`, `*schooladmin*`, `schools:*`, `courses:*`, `sessions:*` | `bump_many` `sch(school)`, `anysch`, `global` |
| C2 | `classrooms/signals.py:198` | `clear_session_cache` (Session) | `*superadmin*`, `*schooladmin*`, `*school*`, `sessions:*`, `courses:*`, `assignments:*`, `studentsubmissions:*` | `usr` of teacher, created_by, the school's admins and teachers, every superadmin; `sch`, `global` (G6) |
| C3 | `classrooms/signals.py:212` | `clear_course_cache` (Course) | 12 patterns incl. `*user*`, `*school*`, `*studentadmin*`, `*teacheradmin*` | `_course_scopes`: `crs`, teacher `usr`, `sch`, every enrolled student's `usr` (G5); `global` |
| C4 | `classrooms/signals.py:259` | `clear_student_course_cache` (StudentCourse) | 11 patterns incl. `*user*`, `*studentadmin*` | student `usr`, `global`, `_course_scopes` (G2, G5) |
| C5 | `classrooms/signals.py:279` | `clear_topic_cache` (Topic) | 8 patterns | `_course_scopes` + `global` (G5) |
| C6 | `students/signals.py:73` | `invalidate_submission_caches` (receiver + 3 `.update()` paths incl. the grading claim) | `SUBMISSION_CACHE_PATTERNS` (7) | `_bump_submission_scopes`: student and course-teacher `usr`, `crs`, `sch`, `global` (P1) |
| C7 | `students/signals.py:110` | `invalidate_submission_caches_bulk` (publish-all-grades) — the site the Verification Engineer flagged | `SUBMISSION_CACHE_PATTERNS` (7) | every student's `usr` + teacher `usr`, `crs`, `sch`, `global` in one pipelined call (G3) |
| C8 | `students/signals.py:120` | `clear_batch_upload_session_cache` (BatchUploadSession) | `studentsubmissions:*`, `assignments:*` | nothing needed: plan §1 table, last row — no cached payload reads BatchUploadSession (re-checked: no serializer or cached view renders it; `batch_session` is not serialised). The receiver is deleted, as the plan says. |
| C9 | `assignments/signals.py:195` | `clear_assignment_cache` (Assignment) | 8 patterns incl. `*user*`, `*studentadmin*` | `_bump_assignment_scopes`: `crs`, COURSE teacher `usr`, `sch`, `global`, every enrolled student's `usr` (G1) |
| C10 | `assignments/signals.py:219` | `clear_assignment_generation_session_cache` | `*assignmentgenerationsession*` | owner `usr` (G9) |
| C11 | `users/signals.py:275` | `clear_user_cache` (CustomUser and Settings) — the `*user*` sweep | 9 patterns incl. `*user*`, `*course*`, `*settings*` | user `usr`, `anyusr`, `global`, every superadmin `usr` (G8), viewer fan-out (G4, item 7, gap #3/#5) |

### 2.3 Constants (1)

| Where | What | Disposition |
|---|---|---|
| `students/signals.py:26` | `SUBMISSION_CACHE_PATTERNS` | removed (plan §2) |

### 2.4 Kept: the plan's one documented exception

| Where | What | Why it stays |
|---|---|---|
| `assignments/pdf_cache.py:173` | `cache.delete_pattern(f"assignmentpdf:<ver>:<assignment id>:*")` in `invalidate_assignment_pdfs` | Plan §2: "not a legacy wildcard". Exact prefix of ONE assignment's own rendered PDFs; the key is also versioned by `updated_at`; no other family shares the prefix. Design doc family 34. |

Not invalidation, so not in scope, but reported so nothing is missed:

* `ai_processor/grading_cache.py` — content-addressed (design family 35);
  no delete of any kind.
* `AutoGrader/redis_test_hygiene.py:97, 113` — `scan_iter` in the TEST
  RUNNER's per-process namespace cleanup (`gaplus-t<pid>:*`), used only by
  `AutoGrader.redis_test_runner`; never loaded by production.
* `AutoGrader/cache_generation.py:260` — `get_client` for the pipelined
  `SET NX` + `INCR` bump; no SCAN.
* `AutoGrader/settings.py:1299` — `DJANGO_REDIS_SCAN_ITERSIZE`; still used
  by the PDF exact-prefix clear, so kept (comment updated).

### 2.5 Every cache write in non-test code, checked for a remaining raw key

A family keyed without a generation and cleared only by a wildcard would go
silently stale when the wildcards go, so every `cache.set` / `cache.add` /
`get_or_set` in non-test code was listed and its key traced:

* `dashboard/views.py`: 22 `cache.set` sites. 21 build their key with
  `versioned_key`; the 22nd, `status_summary`, was raw until stage 1
  (`ffe90cc`) and is now versioned.
* `users/mixins.py` (`UserCacheMixin`, 2 sites), `users/views.py` (2),
  `students/views.py` (1), `classrooms/views.py` (1): all `versioned_key`.
* Not response caches, never matched by a legacy pattern, and not affected:
  `assignments/pdf_cache.py` (exception above), `ai_processor/grading_cache.py`
  (content-addressed), `billing/overage_pricing.py` (Stripe price, TTL),
  `billing/stripe_service.py` (portal configuration id), `users/throttling.py`
  (failure budget), `AutoGrader/health.py` (probe), the `cache.add` locks in
  `billing/` and `users/middleware.py`, and the counters themselves.

Result: after stage 1, no response-cache family depends on a wildcard.

## 3. Stage 3 — what was removed (`6ba0805`)

Everything in §2.1–§2.3, exactly as inventoried: 11 call sites, 2
definitions (with `_delete_now`, the `_pending_patterns` ContextVar,
`batched_cache_invalidation`, the warn-once flag and
`CACHE_UNAVAILABLE_ERRORS`), and `SUBMISSION_CACHE_PATTERNS`.
`AutoGrader/cache_utils.py` is deleted outright (it held nothing else).
The BatchUploadSession receiver (C8) is deleted, per plan §1. This also
closes H-4 (duplicated implementations).

Kept unchanged: every generation bump; `invalidate_submission_caches` and
`invalidate_submission_caches_bulk` (names and signatures, plan §2); the
PDF exact-prefix clear (§2.4). Comments that described the wildcards as
live were updated (`settings.py`, `cache_generation.py`, `users/mixins.py`,
`users/middleware.py`, `assignments/pdf_cache.py`, `students/views.py`,
`students/services.py`). `manage.py check`: no issues.

No family was left without invalidation: §2.5 shows every response cache
is generation-versioned, and every removed call's replacement bump was
already present in the same function (§2.2).

## 4. Stage 4 — tests rewritten (`008b5ac`)

**The matrix runs the real code.** `legacy_wildcards_disabled()` and
`LEGACY_MODULES` are gone from `AutoGrader/tests_cache_matrix_support.py`,
and so are the five per-file copies of the same patch (bespoke 11-14,
dashboard 23-29, superadmin 15-22, dashboard-wide, user fan-out) and the
two inline ones (dashboard freshness 30-33, submission `.update()` paths).
With nothing left to patch, the patch was removed rather than turned into a
no-op, and replaced — as plan §4 step 4 says — by a no-SCAN guard:
`run_matrix` records the MATCH pattern of every SCAN a mutation sends and
fails on any that is not the PDF exact-prefix clear of one assignment
(`PDF_EXACT_PREFIX_SCAN`). Every matrix row in every suite now carries it.
The "legacy really is disabled" guards became "no wildcard sweep runs on a
mutation" checks (a sentinel raw key survives a save).

| Module | Before | After |
|---|---|---|
| `tests_cache_collateral_damage` | non-cache keys survive a sweep that avoids them by naming | **structurally impossible**: every invalidating save (7 model paths) sends no DEL/UNLINK/SCAN/KEYS/FLUSH/EXPIRE and writes only `cachegen:` counters, and still bumps something; the versioned entry becomes unreachable while the old one physically survives; the latent `throttle_user_<pk>` defect is asserted closed |
| `tests_cache_invalidation_coverage` | which wildcard patterns match which key formats | **inverted**: `FAMILIES` (27 bespoke key templates, both status-summary keys included, plus the `UserCacheMixin` base serving 11 viewset prefixes) is compared both ways with every `versioned_key` call in the code by AST; every non-test cache write must use a versioned key or be a listed non-response cache (§2.5); on real Redis each family goes unreachable when any one of its scopes is bumped, and stays reachable when another entity's is |
| `tests_cache_matrix_measurement`, `_scale`, `tests_probe_wallet_invalidation` | ran ON vs OFF | run once on the real code; assert what OFF asserted, plus `DEL == 0`, no disallowed SCAN, and at least one bump per path |
| `tests_cache_matrix_selftest` | spy counts the legacy SCANs | a routed write issues no SCAN; a wildcard delete fails the matrix; the PDF clear passes the matrix guard but not the strict `assert_no_scan` |
| `tests_cache_generation` `CounterNamespaceSafetyTests`, dashboard-wide collision | counters survive the 16 live wildcards | counters survive the one pattern still issued (the PDF clear) |
| `classrooms/tests_concurrency_and_resilience` | outage forced on `delete_pattern`; warn-once on LocMem | outage forced on the bump path (`get_client`, `incr`, `add`): writes survive, ERROR logged. A TypeError is now logged with its traceback rather than raised — `bump_generation` is NEVER RAISES by design (design doc §6). The warn-once test became a proof that a LocMem backend now really revokes a withdrawn student's cached list |
| `classrooms/test_views` (3 tests) | "delete_pattern was called", then a manual `cache.clear()` | the list refreshes with no manual clear, and `delete_pattern` is never called |
| `test_cache_utils` | pinned the removed helpers | asserts the module and every removed name are gone; keeps the SCAN/prefix settings tests |
| `users/tests_activity_middleware_load` | "the `*user*` sweep still clears JSON" | the saved user's versioned payload goes unreachable; nothing deleted |

One test outside the cache suites changed for a reason worth review:
**`assignments.tests_security.ConcurrentAccessRevocationTest`** failed once
in a combined run: two requests labelled "after withdrawal" returned 200. It labelled a request by whether the revocation flag was set when the
request *completed*, while its assertion says "issued strictly after the
revocation was visible". The access check is a DB `get_object()`,
untouched by this change and never cached. Removing the SCANs that sat
between the withdrawal's commit and `withdrawn.set()` shrank that gap, so a
request whose access check ran before the commit but which finished after
the flag was miscounted. Requests are now labelled when issued. Before
relabelling it passed 5/5 run alone; after, it passed in the 492-test run
(§6). This is my diagnosis,
not a proof; the Verification Engineer should look at it.

Left as historical records, untouched: `docs/evidence/h1_stage3/probe_wallet_invalidation.py`
and `docs/evidence/refusal_handling/gate6/test_refusal_handling_scale.py`
still name the removed helpers; neither is collected by the test runner.

## 5. Stage 5 — the guard (`4691157`)

`AutoGrader/tests_no_wildcard_invalidation.py` parses every non-test module
and fails on `delete_pattern`, `delete_cache_patterns`, `iter_keys`,
`scan_iter`, `.keys(<arg>)` / `.scan(<arg>)` (redis KEYS/SCAN; a dict's
`.keys()` takes no argument) and `execute_command("SCAN"|"KEYS")`.

* **Allowlist:** exactly one `delete_pattern` in `assignments/pdf_cache.py`
  (plan §2). Its shape is pinned too: an f-string
  `{CACHE_KEY_PREFIX}:{CACHE_VERSION}:{assignment_id}:*`.
* **Excluded by path, with reasons:** `AutoGrader/redis_test_hygiene.py`
  (scans the test process's own `gaplus-t<pid>:*` prefix) and the test
  runner that loads it. The guard asserts no production module imports
  either.
* A self-test proves the scanner finds all eight forms and ignores
  `{}.keys()`, `GET` and an exact `cache.delete`.

**Proof it bites.** A throwaway `cache.delete_pattern("*user*")` was added
to `users/signals.py` `clear_user_cache`, and
`AutoGrader.tests_no_wildcard_invalidation` plus `users.tests_cache_matrix_g8`
were run:

* guard: `FAIL ... {'users/signals.py: delete_pattern': [270]} != {}`;
* matrix (independently): `teacher renames themselves (G8 fixed): the
  mutation issued a keyspace SCAN other than the PDF exact-prefix clear:
  ['gaplus-t1824233:1:*user*']`.

The file was restored from a copy, md5 verified, `git status` clean, and
the guard re-run: 5 OK.

## 6. Stage 6 — targeted gate

All runs: real PostgreSQL and real Redis, `settings_worktree`, `nice -n
10`, no `--parallel`, one invocation at a time, on `4691157`.

| Run | Labels | Result |
|---|---|---|
| Every cache module and every Stage 3 matrix/probe suite (the 36 files from the brief's `ls`, including the support module, which has no tests), plus the guard and `AutoGrader.test_cache_utils` | 38 | **256 tests OK**, 411 s, exit 0 |
| `dashboard classrooms` (whole apps) | 2 | **597 tests OK** (skipped 2), 326 s, exit 0 |
| Stage 4 check: the 36 cache modules + the 10 other rewritten modules (`classrooms.test_views`, `classrooms.tests_concurrency_and_resilience`, `classrooms.tests_security_penetration`, `students.tests_submission_update_freshness`, `users.tests_superadmin_tenancy`, `users.tests_activity_middleware`, `users.tests_activity_middleware_load`, `assignments.tests_pdf_cache`, `assignments.tests_security`, `AutoGrader.test_cache_utils`), on `008b5ac`'s content | 46 | **492 tests OK** (skipped 1), exit 0 |

Not run here (coordinator's, under the machine lock): the full suite, the
two concurrent full suites and the strict gate. `assignments`, `students`,
`users`, `billing` were run only as the modules listed above, not whole.

**Measurement, wildcards physically gone** (from the gate run;
`AutoGrader/tests_cache_matrix_measurement.py`, 4 bystander schools, 32
cached bystander reads):

| Path | SCAN | DEL | INCR | gone | coldT | coldG |
|---|---|---|---|---|---|---|
| G1 publish assignment | 1 | 0 | 7 | 0 | 0 | 4 |
| G2 withdraw student | 0 | 0 | 7 | 0 | 0 | 4 |
| G3 publish all grades | 0 | 0 | 6 | 0 | 0 | 4 |
| G4 move teacher school | 0 | 0 | 7 | 0 | 0 | 4 |
| G5 rename course | 0 | 0 | 7 | 0 | 0 | 4 |
| G6 rename school session | 0 | 0 | 5 | 0 | 0 | 4 |
| G8 teacher renames self | 0 | 0 | 6 | 0 | 0 | 4 |
| G9 delete generation session | 0 | 0 | 1 | 0 | 0 | 0 |
| P1 claim submission | 0 | 0 | 5 | 0 | 0 | 4 |
| P3 add generation message | 0 | 0 | 1 | 0 | 0 | 0 |
| P4 repair titles command | 0 | 0 | 7 | 0 | 0 | 4 |
| P5 grant credits | 0 | 0 | 3 | 0 | 0 | 0 |

The one SCAN is the PDF exact-prefix clear (asserted by pattern). Scale:
publish at 30 and 300 students, SCAN 1 / 15 queries at both. `coldG` is the
pre-existing `global`-keyed `course/my-courses` (H-15), unchanged.

**Wallet probe** (prints, asserts nothing): overage purchase FRESH for all
three viewers; licence plan change STALE for all three on
`credit_wallet.monthly_credit_total`. That STALE is **pre-existing and
unchanged by step 4**: the recorded probe log
(`docs/evidence/h1_stage3/wallet_invalidation_probe.log`) shows the same
three STALE cells with the wildcards ON and OFF — no wildcard ever reached
it. It is the probe's own finding (H-37), not a regression.

## 7. Mutation checks, wildcards gone

Each mutant applied alone, the named tests run, the file restored from a
copy and md5 verified, `git status` clean after each.

| # | Mutant | Tests run | Result |
|---|---|---|---|
| M1 | **status-summary** keys back to raw `cache.set` keys (the stage-1 change undone) | status-summary suite + coverage | **14 failures**: all 12 status-summary paths (e.g. `student status-summary (all) STALE`), plus `test_nothing_is_cached_under_a_raw_key` and `test_the_key_map_matches_every_versioned_key_call` |
| M2 | **G2, security**: `clear_student_course_cache` no longer bumps the enrolment's own student (removed from both the direct bump and the course fan-out) | G2 matrix + status-summary withdrawal and removal | **3 failures**: `student's course summary STALE, status 200 -> cached 200 / truth 404` (a withdrawn student keeps cached course data), status-summary `all` STALE on withdrawal and removal |
| M3 | G1: no enrolled-student fan-out in `_bump_assignment_scopes` | G1 matrix + status-summary publish | **2 failures**: `enrolled student's assignment list STALE`; status-summary STALE |
| M4 | **G4, security**: no school-admin fan-out in `_payload_viewers` | G4 matrix | **1 failure**: `old school admin's view of the teacher STALE, status 200 -> cached 200 / truth 404` |
| M5 | G3: publish-all bumps only one student | G3 matrix | **1 failure**: a batch student's submission list STALE |

5 of 5 caught. With the wildcards gone nothing masks a missing bump: in
M2 the withdrawn student's cached 200 survives, where under dual-running a
`*studentadmin*` sweep would have hidden it.

## 8. Exceptions kept, and why

Only plan §2's: `assignments/pdf_cache.py` `invalidate_assignment_pdfs`,
one `delete_pattern` on `assignmentpdf:<ver>:<assignment id>:*`. It clears
one assignment's own renders (keys also versioned by `updated_at`; no other
family shares the prefix). Guarded in shape by §5 and in behaviour by the
matrix's SCAN-pattern check and
`test_the_pdf_and_grading_caches_have_their_own_invalidation`.
`DJANGO_REDIS_SCAN_ITERSIZE` and `KEY_PREFIX` stay for it.

## 9. What is left

1. The coordinator's full suite, then two concurrent full suites (H-9 rule)
   and the strict committed-tree gate, under the machine lock.
2. Rebase onto beta (this branch is stacked on the gated Stage 3 tip
   `dd2dc6a`); re-run the guard and the coverage key map after the rebase,
   since any cache family beta added since then must appear in `FAMILIES`
   or the coverage test fails by design.
3. Verification Engineer review, notably the
   `ConcurrentAccessRevocationTest` labelling change (§4).
4. Owner approval, then landing; H-1 closes after that.
