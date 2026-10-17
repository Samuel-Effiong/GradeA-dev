# Score-print stack: mutant M10, the set written short (made after the regression)

Chain on aea4854e: all mutants matched the written failing sets except M10.
M10 (the guard also placed around the save) failed 2 tests; I had written 1.
The extra failing test is
`TheGuardKeepsTheSaveSafeFromTheNextBuilderFault.test_the_grade_is_saved_and_the_old_document_is_left_as_it_was`.
Why (read from the test and the mutant, not a re-run): M10 puts the save
INSIDE the guarded block. The test makes the builder raise, then asserts the
response is 200 and the row holds the new score (9) and was_regraded. With the
save inside the guard, the raise skips the save, so the grade is not stored
and the test fails, as it should. I wrote the set before adding that test and
did not re-read it against every mutant (the rule on file).
Corrected set for M10 = the written one + this test. The written file and the
run's raw results stay unchanged. Senior Manager ruled (chain stands with
M10's extra kill). Verifier 2 may ask for M10 alone to be re-run.
