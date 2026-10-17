# Verification by Verifier 2 (v2): the score-printing stack (bottom aea4854e, H-144, H-145, H-146)

Tip verified: `67390ac8` (code `c8ec622d`; the tip is docs only over it, diff read). Author: d5. Four rows stacked on beta `035e0a07`:
bottom `aea4854e` (the stored answer document prints its score: H-139/H-140/H-142), H-144 `81cd0e08` (a manual grade clears the formatted
grade), H-145 `76b69102` (a formatting task that was overtaken writes nothing), H-146 `c8ec622d` (a regrade clears the formatted grade).
Production change: three files (`students/services.py`, `students/views.py`, `assignments/tasks.py`).
Runs: Release Engineer's slots, 2026-10-09. First slot 10:41:14 to 10:41:24 (red baseline, my probe's fault); second slot 11:01:21 to 11:06:05
(load 3.67 at start, 3.82 at the end). Probes, runner and script written before any run (runner `cc3f25616b047d87`, script `6e99c19b32a40c40`; probes:
score `f35c19e6a2a2714b`, H-144 `408d2f6668d1feee`, H-145 as run first `82ec70e9e35a54a5` and second `92a3604f298e88d7`, H-146 `4c3b2383e2e3efbe`, guard `afca2361fc60c931`).
The author's gates (722/734/759/818 module tests OK, 10+9+12+3 mutants killed, regression Ran 1858 OK) were NOT repeated (rule 15).

## Word: VERIFIED-WITH-NOTES (all four rows)

## The first slot's red baseline, and its cause
Baseline Ran 15, 12 passed, 3 failed (H-145 probe P1, P2, P4), all from one cause: **my probe's fake queue gave every queued message the same fake celery id,
and the database holds that id as a unique key** (the same slip d5 made in `76b69102`). A probe fault; no mutant ran; the shipped code was not judged.
Fix: a uuid per message. The red probe and the first slot's logs are kept (`first_slot_red/`). The Release Engineer approved one re-run under the Senior
Manager's delegation. The 12 other tests were green on the tip in the first slot too.

## What I checked
1. **Reading, line by line** (the three production files; the older probes' anchors for the route's rebuild were rewritten because the code now has the guard).
   Stamp: `grading_result_stamp` = graded_at | regraded_at, taken after the save, sent with the message; the task writes only if the row still has that stamp,
   inside the row lock; a message without a stamp writes as before. Clearing: grading and the manual grade set `formatted_grade = None` in the same UPDATE as
   the new score; the manual grade's launch failure is caught and logged (ids and type only) after the grade is saved. Score printing: a graded row prints
   `format(Decimal(str(score)), ".2f")` on every path; an ungraded row is as before.
2. **Real roads, nothing but the provider call replaced:** the manual-grade route with the student's own reads on both routes; the formatting task run with the
   exact message the route / the grading pipeline queued, its tracked record included; `grade_engine` for the regrade (H-146 R1 released, R2 unreleased, H-145 P3).
3. **15 tests, baseline Ran 15 OK on the tip** (the second slot).
4. **17 deliberate faults, each judged by my probes alone, failing set written beforehand: 16 KILLED with the failing set EQUAL to the written one, restores 17 of 17:**
   Y3b rebuild only when published {s4}; Y9b the route never rebuilds {s1,s2,s4,g1}; Y10 one decimal not two {s1,s2,s3,s4}; Y11 student serializer returns the
   stored document {s4,t3}; Y12 the route does not clear {t4,p1,p2,p4,g1}; Y14 manual route sends no stamp {p1,p2}; Y15 teacher-feedback route no stamp {p4};
   Y16 grading sends no stamp {p3,r1}; Y17 the task compares the copy it loaded {p2}; Y18 the stamp leaves out the manual-grade time {p1,p2,p4};
   Y8 grading clears only a released paper {r2}; Y13 grading never clears {r1,r2}; G1 the guard catches nothing / G3 its log line carries the error text / G4 it logs a
   traceback {g1 each}. Y4 (the ungraded form printing two decimals) cannot be seen by my probes; d5's two modules judged it: the three tests I named failed (among 8).
5. **ONE DIFFERENCE, my written set:** Y5 (the refused-queue log line swallowed) failed {t1}; I had written {t1, t2}. My T2 reads the whole "students" log tree with
   `assertLogs`, and the launcher's own older line (`students.task_tracking`) also logs at ERROR there, so T2 still passes. My T1 header said so and I did not apply it
   to T2. The kill by T1 (`0 != 1 : []`, no line from `students.views`) is the one wanted. T2 states a fact and was never meant to lean on the route's own line.
6. **Rule 22:** for H-145 and H-146 the failure fragments exist, were written before the red runs (file times 19:45 and 20:02 on 8 Oct against runs at 19:55 and 20:26)
   and are looked for inside each test's own block. The H-144 and bottom chains ran before rule 22 existed (18:29 against 19:18), so they have no fragments; I read
   H-144's nine red blocks myself: they fail for the old stored text ("... is not None") or "no logs of level ERROR or higher triggered on students.views", their purposes.
7. **Merge onto the current beta:** a dry run (`git merge-tree`, nothing written) of the stack with origin/beta `5e37ac2b` shows no conflicts, and beta has not touched
   the three files since the stack's base. The author's regression ran on the stack's base, not on current beta: the batch gate on the merged tree is the Release Engineer's.

## Notes
- N1 (new, pre-existing, not of this stack): on a refused queue the route's own log line is clean (ids and error type), as claimed, but the launcher's older line
  `students/task_tracking.py` `mark_processing_task_failure` (`logger.error(..., exc_info=error)`) logs the same broker error WITH its text and a traceback.
  My T1 printed: "other ERROR lines under 'students': 1; carrying the broker's text: 1 ['students.task_tracking']". The route's comment says a broker error's text can
  carry a connection address; that line defeats the care. Proposed as a separate row (LOW); not part of this verdict.
- N2: a genuine zero now prints "0.00" in the stored document where it printed nothing before (intended by the row; held by my S2 and d5's tests).
- N3: `format_grade` (partial save, H-145) is queued nowhere in the application (dead path); tested by d5 only.
- N4: the lock's behaviour between two database connections (the task's `select_for_update` against a grade being saved) is by reading only; no test of anyone uses two connections.
- N5: an unreleased paper's manual grade changes nothing a student reads on four routes (my T3, green on the tip, red under Y11): the stored document is rebuilt but not served before release.
- N6: my S3 checked every hundredth from 0.00 to 10.00: the header printed from a float equals the header printed from the database decimal, 1001 of 1001, none differing.

## Files
Probes, runner, script, the second-slot driver and mutant logs (gzipped) in `logs/`; the first slot's red probe, status and logs in `first_slot_red/`.
