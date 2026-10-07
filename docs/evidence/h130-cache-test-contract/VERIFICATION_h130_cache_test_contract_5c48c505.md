# Verification: H-130's follow-up, the submission-detail cache tests say what H-130 made true (d5)

- **Branch:** task/h130-cache-test-contract at **5c48c505**, on task/beta-batch-11 079ae209. The test module last changed at 79850d19; d87a56f6 adds the mutation runner. 5c48c505 adds only docs/evidence/h130-cache-test-contract/ (18 files). v2's run was at d87a56f6.
- **The change:** tests only. In `AutoGrader/tests_cache_bespoke_1114.py` three tests replace one, with a helper, the table row for this cache key, and a KNOWN LIMIT in the module's notes. No application code, no migration, no settings change (SM's ruling, 2026-10-07).
- **Why:** batch 11's one full run at 079ae209 was red on one test (Ran 5913, failures=1). The old test said a student's submission detail does not change when the teacher retitles the assignment. Since H-130 a student's answer document before release is rebuilt at each read from the assignment's current title and due date, so it does change. The test was out of date; the cache was not serving old data.
- **Verifier:** v2 (independent), 2026-10-07. One slot from 0b, 11:50:09 to 11:52:39 WAT, 1-minute load 7.05 to 8.03 (nothing timed), beside the Next-stage Builder's battery.
- **Verdict:** **VERIFIED-WITH-NOTES.** The three tests say what the code does since H-130, each can fail, and the known limit is real as written. The notes are limits of the tests and one older fact (H-150); none asks for a change here.

## This was a miss of mine in H-130
In H-130's verification v2 did not search the whole test tree for tests that pin the behaviour the change replaces. The regression ran classrooms, students and assignments; this module is in the AutoGrader app and not on the guard list, so the batch's full run was the first to meet it. Rule 20 (2026-10-07) now puts this module into the gates of every change to a cached answer.

## The three tests, as read
All three read `GET /api/v1/submissions/<id>` on the real Redis cache, with an enrolled student, a published assignment, and the course's own teacher. The first read fills the stored document and the cache; the retitle is a real save.

| Test | What it holds |
|---|---|
| a retitle reaches the student's unreleased document | the next read has the new title and not the old one, with no cache clear; nothing else in the payload changes |
| a retitle does NOT change the teacher's payload | staff read the stored document, built once |
| a retitle does NOT change a released student's document | after release the student reads the stored document too |

- **Why the student's key needs no wider scope:** the key hangs on the reader's own counter, and an assignment's save moves the counter of every student who holds an enrolment row in the course (`assignments/signals.py`, `_bump_assignment_scopes`; any status).
- **A limit of the two "does NOT change" tests (v2's note, taken by d5 into the evidence):** an equal payload is also what a stale cache gives. v2's P1 reads the student's counter before and after and shows the equal payload is a fresh one.
- **A limit of the whole module (v2's note):** no test in it can fail because a key is invalidated too often. v2's Z3 added the global scope to this key and all 17 of d5's tests passed, as predicted.

## The KNOWN LIMIT, shown as written
The bump goes to students with an enrolment row; a student reads their own submission with no enrolment check. v2's P3 deletes the row, retitles, and reads: the counter does not move and the student still reads the old title; with the cache emptied the new title is there. So the limit is real, it is the cache and nothing else, and it lasts at most the key's five minutes. No production code deletes an enrolment row (d5's and v2's text searches; not proof). Nothing about a grade is involved. SM: accepted, row H-149 (LOW).

## Found while reading, older than this change: three keys a student never receives (H-150)
`StudentSubmissionDetailStudentVersionSerializer` declares `assignment_title`, `assignment_due_date` and `course_title` with sources written with two underscores (`assignment__title`). That names no attribute; the fields are read-only, so the framework leaves the keys out (`rest_framework/fields.py`, `get_attribute`: not required, so the field is skipped).

- v2's P4 states the absence, and passed. Z4 repaired the title's source: P4 failed, and so did d5's student test on its last line ("nothing else in the payload changes") and d5's released-student test.
- So d5's "nothing else follows the assignment" is true today for this reason, and a repair of those sources will turn two of the three tests red. That is right: it would be a visible change with its own cache question.
- SM's ruling: row H-150 (LOW), a product question for the frontend; no repair in batch 11 or 12.

## d5's gates, read by v2 from the raw logs (not repeated, rule 15)
| Gate | The raw log |
|---|---|
| (a) the module and the repo-wide guard list, at d87a56f6 | Ran 487 tests in 205.967s, OK |
| (b) 3 mutants, at d87a56f6 | 3 of 3 killed; each failing set equals the one written at 11:42:29, before any run: X1 the student test; X2 the teacher and released-student tests; X3 the released-student test |
| (c) AutoGrader + students + assignments, `--parallel 2`, at d87a56f6, 12:01:42 to 12:06:58 (rule 20) | Ran 1639 tests in 289.356s, OK (skipped=16), exit 0; no stall; 1-minute load 2.97 at the start, 3.34 at the end. The three new tests are in the log, passing. 0b's disclosure: about 23 seconds of one core at full load beside it (a cost check of another tool), ending 12:02:37; nothing in this module is judged by the clock |

- "Each seen red first" (SM): the three tests are new and no code changes, so there is no red commit. Each test is seen red under a mutant: the student test under X1 (and v2's Z1, Z4), the teacher test under X2, the released-student test under X2 and X3 (and v2's Z4).
- **Nothing but evidence after d87a56f6** (v2 read the delta: no file outside the evidence folder). The committed raw logs end with their own Ran and OK lines, and the regression's committed raw log is the file v2 read (same sha256). d5's evidence carries the known limit (H-149), v2's three notes in v2's sense, and 0b's disclosure.

## v2's run (11:50:09 to 11:52:39, at d87a56f6)
Expectations were written in the runner before any run; 0b read the script and checked the checksums before the grant. Every inner run wrote its own file. Baseline: **Ran 26 tests in 19.213s, OK** (d5's module, 17; v2's probe class, 9: it subclasses d5's class, so that class's five tests run a second time beside P1 to P4).

| Mutant | Judged by | Result | Failing |
|---|---|---|---|
| Z1 the student always reads the stored document (H-130 part B undone) | d5's tests | KILLED, Ran 17 | the student test, exactly |
| Z1 | v2's probe | KILLED, Ran 9 | P2 and the inherited student test, as named; and P3 |
| Z2 an assignment's save bumps no student | v2's probe | KILLED, Ran 9 | P1 and P2, as named; and the inherited student test |
| Z3 the key gains the global scope | d5's tests | SURVIVED, as predicted, Ran 17, OK | none |
| Z3 | v2's probe | KILLED, Ran 9 | P3, exactly |
| Z4 the student's title source repaired | d5's tests | KILLED, Ran 17 | the student test, as named; and the released-student test |
| Z4 | v2's probe | KILLED, Ran 9 | P4 and the inherited student test, as named; and P1 and the inherited released-student test |

- **Three failing sets are larger than the names v2 wrote.** Every named test failed; the extra ones were not predicted by v2 and each follows from the mutant: under Z1 the stored document keeps the old title, so P3's last read fails; under Z4 a released student's payload carries the live title too, so the released-student test and P1 fail.
- **Rule 19, each probe seen red:** P1 under Z2 (and Z4); P2 under Z1 and Z2; P3 under Z3 (and Z1); P4 under Z4.
- **What the probes hold:** P1, P3 and P4 are described above. P2: a due date the teacher saves reaches the student's unreleased document on the next read, and nothing else in the payload changes (the module's note says "title and due date"; d5's test covers the title).
- **Rules 16, 13, 12** wrap each step. **Rule 17:** `PYTHONDONTWRITEBYTECODE=1` and `python -B`; `__pycache__` of the mutated module's folder deleted before each mutant and after each restore; every restore equals the commit's blob by sha256. **Rule 18:** every run wrote straight to its own file with stdin from the null device.
- **Redis:** the module runs on the real Redis cache through `AutoGrader.test_cache.real_redis_caches`: a key prefix per process and a `clear()` that removes that process's keys only. So this run and the battery beside it could not clear each other's entries (read by v2 for 0b before the grant).
- **Load:** 7.05 at the start of the baseline, 8.03 at its end, 7.72 at the end of the mutants. Nothing is judged by the clock.

## Notes
- **N1.** The two "does NOT change" tests cannot tell a fresh equal payload from a stale one. Stated in d5's evidence; v2's P1 covers the released-student case. A one-line counter check in the module is the SM's to ask for.
- **N2.** Nothing in the module fails on over-invalidation (Z3). The reason this key has no global scope is cost, and no test holds it.
- **N3.** H-150: the three absent keys. A repair turns two of these tests red.
- **N4.** The known limit (a deleted enrolment row, at most five minutes) is real as written (P3).
- **N5.** v2's search of the whole test tree for other tests that pin H-130's old behaviour was not repeated here: batch 11's full run did that, and its one failure is this test.

Files: `~/Documents/Projects/GAP-v2-handover/` `tests_vf2_h130c_probe.py`, `vf_h130c_mutants.py`, `vf_h130c_run.sh`; the run: `runs/h130c_d87a56f6_baseline.log`, `runs/h130c_d87a56f6_mutants.log`, `runs/h130c_d87a56f6.status`, `runs/h130c_d87a56f6_mutant_logs.tar.gz` (each inner run's whole output, eight files).
