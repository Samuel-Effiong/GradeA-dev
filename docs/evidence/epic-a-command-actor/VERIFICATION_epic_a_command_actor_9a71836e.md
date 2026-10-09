# Verification: command_actor, H-69 plumbing on Epic A (ed)

- **Branch:** task/epic-a-command-actor at **9a71836e**, on phase2/epic-a 3fff1382. The code tip is 5e4e1c5d (0b's base update); 5e4e1c5d..9a71836e is evidence only.
- **Commits:** 09141be9 (code), a8b2cbdd (tests), 0124cc68 (round 1's fix: audit/history.py back to its base; one isolating test; the docstring).
- **Verifier:** v2 (independent), 2026-10-02.
- **Verdict:** **VERIFIED-WITH-NOTES**

## Static checks
| Check | Result |
|---|---|
| Base update 5e4e1c5d: `git show --remerge-diff` | Empty |
| Added-line survival (`vf_merge_survival.py`) | base cc22bc0, sides 8c7f1ed / 3fff138, **0 lines lost** |
| Production diff against the epic | audit/context.py, audit/emitter.py, audit/metadata.py and audit/README.md only |
| audit/history.py | The same blob as 3fff1382's (4204766c…) |
| Where the rule lives | One place, `emitter._build`: `if actor is None: actor = current_command_actor()`. The history signals and `record_bulk` pass their actor to `emit`, so they are covered without their own edit |
| Who may be named | A saved, active user with `is_superuser` and user type SUPER_ADMIN. Checked before the block runs; anything else raises ValueError |
| What may be named | A plain module name (`[a-z][a-z0-9_]{0,63}`) that Django lists as a management command |
| `metadata["command"]` | Added by the emitter after the allow-list, from the checked name. The key is in `ALLOWED_KEYS` but in no action's allow-list, so a call site's own "command" is dropped |
| `source` (SM ruling) | Untouched: still create / save / bulk / delete |
| The two ruled cases | An actor a call site passes is kept; a request's signed-in user is kept. Both in the docstring, each with a test |
| Context handling | A ContextVar set with a token and reset in `finally`. It does not cross into a Celery worker or a new thread; events written there stay SYSTEM |
| Rule 14 | No MagicMock. One `patch(..., return_value={...})` of `get_commands` with a real dict |

## ed's gates (read, not repeated: rule 15)
| Gate | Tip | Result |
|---|---|---|
| Reproduce-first on 3fff1382's production files | 5e4e1c5d | FAILED at import, as expected (the weak form, disclosed) |
| The new module, caller modules, Epic A's guards and the merge-down's | 5e4e1c5d | **584 OK** |
| Mutants C3–C15 | 5e4e1c5d | **13 of 13 killed** |
| ONE regression: audit, ai_processor, students, billing | a75c4cf7 | **3603 OK** (skipped=9) |

- **Full logs, hashes recomputed by v2:** the 584-test gate `2140b9cbbd9b8508`; the regression `10025fbf33c9715f`; round 1 `4ce358d8a08e85c9`. No FAIL or ERROR header in any of them.
- **Rule 17:** mutate.py sets `PYTHONDONTWRITEBYTECODE=1` and deletes `__pycache__` before each mutant and after each restore; EVIDENCE.md states both.
- **Round 1 is disclosed:** two survivors (C1, C14). C1's edit to audit/history.py was redundant and was removed; C14 got an isolating test.
- **Not in ed's regression (SM ruling):** users, classrooms, assignments, dashboard and AutoGrader. The next Gate 10 (staging refresh 8) covers them and stays a hard gate before staging.

## v2 run (0b's grant, one 6G slot, rules 16, 13 and 12, scratch worktree at 9a71836e, DB test_vf2_s1)
**Rule 17:** every test subprocess ran with `PYTHONDONTWRITEBYTECODE=1`. The `__pycache__` of audit/ was deleted before each baseline, before each mutant run and after each restore. Each restore was sha-checked against 9a71836e.

**Baseline, second run: 28 tests OK** (ed's 21 + the probe's 7). Log: runs/command_actor_9a71836e_baseline2.log.

**Disclosed: the first baseline failed on a defect in v2's own probe** (28 run, 1 failure; runs/command_actor_9a71836e_baseline.log). Q4 expected a history event for a new TEACHER account; the epic records a user's create only for a privileged account (`create_filter`). The probe now creates a school admin: one probe line changed, no product file. 0b extended the grant for the one repeat.

**Probe (tests_vf2_command_actor_probe.py), 7 OK:**
- **Q1 nested blocks:** the inner block's operator and name apply inside it; the outer block's are restored when it ends, also when it raises.
- **Q3:** a call site that supplies `"command": "someone@example.com"` inside a block cannot change the name. The stored value is the block's, and the emitter logs the dropped key.
- **Q4:** a privileged account's create and its delete inside a block are the operator's, with `source` create and delete.
- **Q5:** `record_bulk(actor=None)`, an explicit SYSTEM, is the operator's inside a block and SYSTEM outside one.
- **Q7:** a refused block (a teacher; a free-text name) inside a good block leaves the good block's context in place.

**Mutants (vf_command_actor_mutants.py), each run against ed's module and the probe separately** (runs/command_actor_9a71836e_mutants.log):
| Mutant | ed's tests | v2's probe |
|---|---|---|
| Y1 a nested block's exit clears the outer block's context | **survived** (21 OK) | killed (Q1, both tests) |
| Y2 inside a block, a call site's own "command" value wins | **survived** (21 OK) | killed (Q3) |
| Y3 the command is named only on the operator's own events | killed (2 tests) | survived (the probe has no explicit-actor case) |

## Notes
1. **Two gaps in ed's tests (Y1, Y2). The production code is right on both; nothing pins it.**
   - **Y2 matters more.** ed's `test_a_call_site_cannot_supply_the_command_key` runs outside a block, where the emitter's branch isn't taken. Inside a block a regression could let a call site write free text, even an address, into `metadata["command"]`, past the allow-list.
   - **Y1:** no test nests two blocks.
   - **For the SM:** I recommend ed adopt Q1 and Q3 (two short tests, test-only, a touched-module re-run under rule 15.4) before the merge, or with the next slice, which is the first to use the block.
2. **No command uses `command_actor` yet,** so there is no end-to-end command run. The resolve-intent audit emit is the first user; verify the wiring there (`--by` resolved, the block around every write).
3. **A Celery task or thread started inside a block is not covered:** its events stay SYSTEM, with no command key. Worth one line in audit/README.md when a command first dispatches work.
4. **Apps not in ed's regression** (users, classrooms, assignments, dashboard, AutoGrader): left to the next Gate 10, by the SM's ruling.
5. **The first v2 baseline's failure** was the probe's, disclosed above.
