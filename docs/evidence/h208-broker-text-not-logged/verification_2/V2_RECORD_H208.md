# Verifier 2: H-208 at 994febe1 (d5), record

Slot: 18:18:15 to 18:19:00 (clock read), GRANT by 0b 18:18, load 1.29 before, 1.39 after. Worktree vf2-s1 at 994febe1, clean before and after. Runner vf_h208_mutants.py 17725c4ccae85514, script vf_h208_run.sh 59fd488c881f5a68, written 17:45 BEFORE d5's account of the differences was read (expected sets file h208_v2_expected_sets.txt 4ee1eb4086b92397). Judged by students.tests_broker_text_not_logged, students.tests_task_tracking, billing.tests.test_refusal_handling.D8TaskFailureLoggingTest.

## Results (raw: mutant_logs/*.txt, h208_994febe1_mutants.log)
Baseline: Ran 40 tests, OK (expected Ran 40, OK).
All 7 mutants KILLED_AS_WRITTEN: failing set EQUAL to the set I wrote, and every written failure fragment stood inside that test's own FAIL block (rule 22), restore sha256 ok for each.
- M1 (broker branch off): L2, L4, F1, H1, H4 + the billing D8 timeout test (d5's list had the first five; the sixth is the billing class d5's runner does not judge).
- M2 (cause not followed): L4, F1, H4.
- M12 (context followed again): L5, H3 (both "'error=RuntimeError' not found in ... error=ConnectionError").
- V1 (follow-up line without the helper): F1 only.
- V2/V3/V4 (str(exc) at safe_delay, cancel, state read): D1, C1, S1, one each.

## Agreement with d5's corrected account
M1 {L2,L4,H1,H4,F1}, M2 {L4,H4,F1}, M12 {L5,H3}: equal to mine (M1 plus the billing test). F1 under M1/M2 is the services_text assertion, as d5 says; the M12 "WRONG REASON" was his checker reading the condensed log (a checker fault, not a test fault); my own reasons for L5/H3 were seen in their blocks.

## Whole-test-set read for empty or missing values (994febe1)
No assertion found satisfiable by an empty/missing value. H1 now raises the error and asserts it has a traceback; F1 asserts on the follow-up line's own text; L5/H3/H4 assert the deciding values (cause/context, traceback) before their absences; every absence in L1, L2, L4, C1, S1, D1 follows a non-empty-text assertion. Frames-absence for a broker class is asserted in L2, L4, H1, H4 and the billing timeout test; C1/S1/D1/F1 rely on the shared helper (M1 is seen by L2, L4, H1, H4, F1 and billing).

## Design fact and other roads to error reporting (by reading)
BROKER_UNAVAILABLE_ERRORS = redis ConnectionError/TimeoutError, kombu OperationalError, builtin ConnectionError, builtin TimeoutError. requests' ConnectionError is not the builtin. Since e7dd3925 the helper follows __cause__ only. mark_processing_task_failure: every task-body caller re-raises, so a real fault still reaches Celery/Sentry. launch_processing_task: non-broker re-raised; broker becomes the 503, reported only by the log event. safe_delay, cancel, state read swallow broker errors only (log event is the report). Follow-up dispatch swallows ALL exceptions: the log event (class plus frames) is the only report for a non-broker fault.

## Not done by me
The author's c_h208 regression (rule 15). d5's tip is now f4d443df (docs only over 994febe1; not checked by me beyond 0b's note).
