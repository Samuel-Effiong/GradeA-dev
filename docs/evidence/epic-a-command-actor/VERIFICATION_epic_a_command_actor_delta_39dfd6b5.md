# Verification addendum: command_actor, the delta to 39dfd6b5 (ed)

- **Branch:** task/epic-a-command-actor at **39dfd6b5**. The earlier verdict is VERIFIED-WITH-NOTES at 9a71836e (record committed verbatim at d40416fe).
- **Delta:** 94064627 (test-only), ada31929 (the harness), 39dfd6b5 (evidence).
- **Verifier:** v2 (independent), 2026-10-02. No v2 test run (rule 15.4: a test-only commit; ed ran the touched module and the mutants).
- **Verdict:** **VERIFIED.** Note 1 of the earlier record (the two test gaps, Y1 and Y2) is closed, as the SM ruled, before the merge.

## Checks
| Check | Result |
|---|---|
| 9a71836e..39dfd6b5 outside docs/ | audit/tests_command_actor.py only, +85 lines. audit/context.py, emitter.py, metadata.py and history.py are unchanged |
| Y2's test | `test_a_call_sites_command_value_never_reaches_the_event`: inside a block, four supplied values (an address, a real command name, free text, an empty string) each leave the block's checked name in the event, with the operator as actor |
| Y1's tests | `NestedBlocksTests`: the outer block is restored after the inner one ends and when it raises; a refused inner block leaves the outer one in place |
| The two mutants in ed's harness | C16 and C17 are the same edits as v2's Y2 and Y1 (compared line by line) |
| Rule 14 | No mock in the new tests |

## ed's round 3 (read, not repeated)
- The module at ada31929: **25 tests OK** (r3_module.txt).
- Mutants C3–C17: **15 of 15 killed**, no survivors (mutation_log.txt). C16 is killed by the Y2 test; C17 by the two nested-block tests.
- EVIDENCE.md has the round 3 section, states the rule 17 steps, and lists under "Not verified here" that a task or thread started inside a block stays SYSTEM.

## Still open from the earlier record (none blocks)
- No command uses `command_actor` yet; the resolve-intent audit emit is the first user.
- users, classrooms, assignments, dashboard and AutoGrader were not in ed's regression: the next Gate 10 (staging refresh 8) covers them and stays a hard gate.
