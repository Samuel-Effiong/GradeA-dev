# H-233: the overage sentence at 0 blocks; at_risk reads like a boolean; reason-code docs

Found by the Verifier by reading the user's commits fafed57f..913f248f (L2, L4); the SM made it one row for the small batch after batch 18 and approved each choice word for word (2026-10-14). Branch task/h233-overage-wording-at-risk, stacked on H-231's tip 1800ca80: both rows rewrite my_students' plain-list guard.

## The faults (read on 1800ca80)
- OVERAGE_CAP_EXCEEDED (AutoGrader/reason_codes.py) said "... Reduce your quantity to {remaining} or fewer to proceed.", which reads "to 0 or fewer" when no block is left. The docs entry (docs/GRADE_A_PLUS_REASON_CODES.md) carried an older sentence, and RATE_LIMITED had no docs entry.
- my-students and all-students read `at_risk` as `value.lower() in ("1", "true")`: `?at_risk=yes` silently meant false, `?at_risk=maybe` filtered to the students NOT at risk, and all-students cached that answer.

## Change
- f886e643 tests only. billing.tests.test_overage_cap pins the whole new sentence at remaining=1 (service and view, and the view's remediation) and at remaining=0. dashboard.tests_h233_at_risk_values, on both routes: V1/V2 yes is true; V3/V4 an unknown word is a 400 `{"at_risk": ["'maybe' is not true or false."]}`; V5 empty is not given; V6 a refused value leaves no all-students cache entry. Controls: V7 (true, false, 1, empty), V8 (a good value is cached: one key).
- 2c4ba4fc fix. The sentence: "This purchase would go over your plan's limit of {max_blocks} block(s) per billing cycle. You have {remaining} block(s) left this cycle." (the one raise site, billing/stripe_service.py, the checkout's cap check, passes both params; the grant-time re-check only logs). The docs entry carries the same sentence; a RATE_LIMITED entry and its index row are added. AutoGrader/query_params.py `boolean_query_param`: DRF's BooleanField parsing (true/false, 1/0, yes/no, on/off, t/f, y/n, any letter case); empty is not given; anything else is a 400 naming the parameter. All-students reads it before the cache key is built; my-students is not cached (a custom action, not the mixin's list).

## Mutants (GAP-58-runs/h233/config.py, copied as `chain_2c4ba4fc/config.py.txt`)
K1 empty is no longer "not given" {V5, V7}; K2 an unknown word means false {V3, V4, V6}; K3 all-students back on the old reading {V2, V4, V5, V6}; K4 my-students' guard by truthiness {V7}; O1 the old sentence {the three overage tests}. Every written reason was checked against the operand types (set vs set, list vs list, int vs int, str vs str, where the old sentence shows in the "- " diff line).

## The chain on 2c4ba4fc (a6's GRANT 15:51 WAT; logs gzipped in chain_2c4ba4fc/)

Runner row_chain.py sha256 f3fe0bdde92d9065aeaf99aecfc3a2a9c69dc67a9598e561da2faa108d805b8e; config `config.py.txt` sha256 d3b3f3fd7ef0d62d0986c1f37ce9c05eeaf7b2b5f2bd1536730b12f9f0692eb8 (Verifier pre-read: clear). One systemd-inhibit around the whole chain. Copied from chain.status and the logs:

| Stage | Result |
|---|---|
| (r) the two test files from the tip, on 1800ca80 | "Ran 14 tests in 1.400s", FAILED (failures=9); "9 failing, as expected"; "the red set is the expected one, each for its written reason" |
| (a) 27 labels | "Ran 442 tests in 153.622s", OK |
| (b) baseline (2 modules) | "Ran 14 tests in 1.973s", OK |
| K1 | FAILED (failures=2); "2 failing, as expected" |
| K2 | FAILED (failures=3); "3 failing, as expected" |
| K3 | FAILED (failures=4); "4 failing, as expected" |
| K4 | FAILED (failures=1); "1 failing, as expected" |
| O1 | FAILED (failures=3); "3 failing, as expected" |

"(b) every mutant killed, each failing set and reason as written"; "h233 CHAIN END: green as written" at 15:57:44. Rule 24: the row's regression is its batch's full run.
