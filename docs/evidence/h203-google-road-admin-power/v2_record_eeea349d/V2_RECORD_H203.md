# Verifier 2: H-203 record (tip eeea349d; docs-only successor 2a16913c)

Verdict: VERIFIED. Run 18:42:40 to 18:44:23 WAT 2026-10-09, one slot (0b GRANT 18:43), load 3.0 at start.

## Result
- check: all 19 edits apply (exit 0).
- baseline: Ran 32 tests, OK (my probe 11, ed's principle module 16, the pin 2, 1a's probe 3). My licence finding l1 is green.
- mutants: 19 of 19 KILLED, every named must-test failing; extra failures are only ed's own module tests (module-judged mutants). Restores 19 of 19 sha256 ok, no mismatch; worktree clean at eeea349d; probes removed.
- Written reasons (rule 22) seen inside the test's own FAIL block: R4/a3 (`not greater than or equal to 400`), S8 and S20 (ed's order tests), A1 and A2 (the SUPER_ADMIN Google test), S9, S10 and R1b (1a's v1 test). P3 (a password-by-mail road) is killed by the pin's SET_PASSWORD kind.
- Raw logs in this folder: h203_eeea349d_{check,baseline,mutants}.log, .status, h203_eeea349d_mutant_logs/.

## Things the reader must know
- My test a3 (direct-add route) does NOT pin the status to 400: that route answers 500 for a refused add (known row H-214). It checks any 4xx/5xx, the row unchanged, no enrolment, no mail. The first baseline stopped on this (my probe's fault); the stopped logs are kept apart in runs/stop2_baseline_a3 and the stopped probe in runs/tests_vf2_h203_probe_7cb6d302_red_a3.py.
- Accepted costs of the principle (SM rulings): (1) Google road gives a one-bit signal: the mailbox holder of a never-verified admin-power account reads a plain failure where others get in; the refusal bytes equal an ordinary failed Google sign-in (my g2). (2) Licence road: the school admin reads the same words as for a non-teacher account. (3) Add-by-email widening: a verified student-typed row with staff/superuser flags answers NOT_A_STUDENT_MESSAGE. (4) A dormant never-verified SCHOOL_ADMIN with no flags still activating through Google is ruled (platform power only).
- Carried: H-214; batch 16's merge-down needs an audit event on the new Google arm.

Files here: my probe, 1a's probe, mutants runner, run script, expected sets (shas: probe ee530f2dca64a5c6, 1a's 557a4e725e1bbced, mutants ea379a9cb5e43f9a, script 68a7e9be7c13957c, expected 465a739604721c71).
