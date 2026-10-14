# H-231: a filter number that is not a number is a 400, not a 500

Found by the Verifier by reading the user's commits fafed57f..913f248f (M3); the SM made it a row for the small batch after batch 18 and approved the scope (2026-10-14): empty means not given; a non-number, NaN or a value outside 0-100 is a 400 naming the parameter; `months`, the one sibling with the same pattern, gets the same treatment. Branch task/h231-grade-filter-400 from beta 8a64c35b.

## The fault (read on 8a64c35b)
my-students (classrooms/views.py, `my_students`) cast `min_grade` and `max_grade` with a bare `float()`; monthly-token-usage cast `months` with a bare `int()`. `?min_grade=abc`, `?months=abc`, or an empty value beside another filter raised ValueError, which `custom_exception_handler` (users/exceptions.py) answers with a bare 500. Values outside 0-100 were accepted and filtered.

## Change
- c4bfb05c tests only: classrooms.tests_h231_bad_filter_numbers. G1 min_grade=abc, G2 max_grade=abc, G3 nan, G4 150, G5 -5: each a 400 with `{param: ["<repr> is not a number between 0 and 100."]}`; G6 an empty min_grade beside at_risk is not given (200); M1 months=abc a 400 `{"months": ["'abc' is not a whole number."]}`; M2 empty months is the default 12. Controls: G7 (0, 100, 70 and 69.99 are grades; equal to min_grade is kept), M3 (months=99 still falls back to 12).
- 2463eed0 fix: `_grade_query_param` (absent or empty is None; otherwise float in 0-100, or DRF's ValidationError naming the parameter, as the file's `_validate_uuid_query_param` answers); the plain-list guard asks `is not None`, so min_grade=0 still filters out the ungraded; `months`: absent or empty is 12, a non-whole number is a 400, out of range still 12.
- 1800ca80: the separate `grade != grade` clause removed. NaN fails `0 <= grade <= 100` too, so it was a second check no test could tell apart.

## Mutants (GAP-58-runs/h231/config.py, copied as `chain_1800ca80/config.py.txt`)
P1 float() unguarded {G1, G2}; P2 no upper bound {G4}; P3 no lower bound {G5}; P4 empty is not "not given" {G6}; P5 the plain-list guard by truthiness {grade_filter's ungraded-student test}; P6 months int() unguarded {M1}; P7 empty months not the default {M2}; P8 0 refused {G7, grade_filter :140, at_risk_filter :172}; P9 100 refused {G7}. P8 and P9 were added before any run, on the SM's word that both ends must be shown accepted.

## Trace fault: G3's written reason (the stopped run of 14:51)
The first grant (a6, 14:49) ran the chain on 1800ca80 with config sha256 5f94e6fc. It STOPPED at (r) at 14:51:40: 8 failing, the expected set, but "WRONG REASON: test_g3_nan_is_not_a_number_here". G3 was written "200 != 400" ("nan matches nothing"); its own block read "AssertionError: 500 != 400". Cause: on the base the grade is a Decimal, and `Decimal('70.00') >= float('nan')` raises InvalidOperation (an ordering comparison with NaN signals in Decimal); the log shows `decimal.InvalidOperation` at line 50. So NaN was a third way into the same bare 500 as G1 and G2. The Builder and the Verifier both missed it by reading. (a) and (b) did not run.

**On the SM's ruling, G3's reason was corrected to "500 != 400" AFTER the stopped run had shown it**, in the config only (sha256 979ba6cf, re-read by the Verifier); the tip stayed 1800ca80 and the whole chain was re-granted and re-run. The stopped run's log and status are kept in `stopped_1451/` (r_8a64c35b.log.gz, chain.status.gz). Lesson recorded: read the operand TYPES of a comparison (Decimal vs float) before predicting it.

## The chain on 1800ca80 (a6's re-GRANT 14:57 WAT; logs gzipped in chain_1800ca80/)

Runner row_chain.py sha256 f3fe0bdde92d9065aeaf99aecfc3a2a9c69dc67a9598e561da2faa108d805b8e; config `config.py.txt` sha256 979ba6cfc6d63b2e7debaaecb19289171e55eb95b6a6220d245f014319062d47 (Verifier re-read: clear). One systemd-inhibit around the whole chain. Copied from chain.status and the logs:

| Stage | Result |
|---|---|
| (r) the h231 module from the tip, on 8a64c35b | "Ran 10 tests in 0.675s", FAILED (failures=8); "8 failing, as expected"; "the red set is the expected one, each for its written reason" |
| (a) 26 labels | "Ran 438 tests in 117.731s", OK |
| (b) baseline (3 modules) | "Ran 28 tests in 2.459s", OK |
| P1 | FAILED (failures=2); "2 failing, as expected" |
| P2, P3, P4, P5, P6, P7 | FAILED (failures=1) each; "1 failing, as expected" each |
| P8 | FAILED (failures=1, errors=2); "3 failing, as expected" |
| P9 | FAILED (failures=1); "1 failing, as expected" |

"(b) every mutant killed, each failing set and reason as written"; "h231 CHAIN END: green as written" at 15:04:06. Rule 24: the row's regression is its batch's full run.
