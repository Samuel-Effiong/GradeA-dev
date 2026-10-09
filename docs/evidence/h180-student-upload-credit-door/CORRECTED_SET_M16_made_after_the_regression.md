# H-180, mutant M16: the failing set I had written was wrong (made after the regression)

M16 ("a student's upload is billed to the course's teacher", mutated to bill the student) was KILLED by three tests, not the four I had written:
- written: S1 (`test_a_wallet_below_the_estimate_is_refused_before_anything_is_queued`), S2 (`...sentence_a_student_reads_names_no_money`), S4 (the line is the estimate itself), S5 (`...asks_the_shared_estimate_method_and_nothing_else`);
- observed (`gate_files/battery_90b5c763.tar.gz`, logs/M16.log; `gate_files/expected_kills_90b5c763.txt.gz`): **S3** (`test_a_wallet_above_the_estimate_is_queued_as_before`), **S4**, **S7** (`test_a_teacher_without_a_wallet_is_left_to_the_permission`).

**Corrected set for M16 = {S3, S4, S7}.**

Why (read from the code and the tests): I wrote the set believing a student has no wallet, so the mutated door would find none and never refuse (making the refusal tests red). But `users/signals.py:325` gives EVERY new user an empty wallet, a student too. Under the mutant the door bills the student's own wallet (balance 0) and refuses EVERY upload: the tests that expect a refusal still pass (S1, S2, S5) and the tests that expect a queued upload fail (S3; S4's exact-balance half; S7, whose teacher's wallet is removed anyway). The mutant is killed for the right reason: it breaks "a funded teacher's student is queued". It is the same false premise that made my first version of S7 fail at step (a) of the chain (that test now removes the empty wallet before it uploads).

Made AFTER the regression `c_h180` (Ran 5880 OK), as the Senior Manager ruled (2026-10-09 11:47): the written `expected_kills.py` and the run's raw results are unchanged. Verifier 2 reads this reason and may ask for M16 alone to be re-run. All 18 written sets were re-read for the same premise: M16 is the only one whose written reasoning depended on it.

The other direction of the billing (a student whose own wallet is funded while the teacher's is short) is pinned by H-211's test `students/tests_upload_bills_the_teacher_only.py` and its mutant P1 (the Senior Manager's request after this result).
