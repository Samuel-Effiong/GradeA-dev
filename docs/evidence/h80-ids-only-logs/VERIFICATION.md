# Verification: H-80 / H-86, licence and signal logs carry ids, never an address @ 84c17542

**Verifier:** 1a. **Author:** d5. **Date:** 2026-10-02.
**Branch:** `task/h80-ids-only-logs` @ **84c17542** (code tip `af5cee04`), on `task/beta-batch-5` `83fe58ca`:
- `21670117`: the tests
- `5565b246`: the fix in `billing/license_service.py` and `users/signals.py`
- `446b07a9`: the success-path test runs its on-commit callback; `run_mutants.py`
- `7cbd88be`: the renewal test expects the teacher's id
- `f811d9bc`: tests for my pre-review findings P1 and P2
- `af5cee04`: P1's fix (`billing/services.py`, the two renewal-failure lines)
- `84c17542`: the evidence (docs only)

The evidence is in `docs/evidence/h80-ids-only-logs/`.

I ran in 0b's slot from my detached scratch checkout at 84c17542, with its own test DBs (`test_vf_h80`, mutant `test_vf_h80_mut`). The wrapper was rule 16's `systemd-inhibit`, the 6G scope with `MemorySwapMax=0`, `nice -n 10` and `timeout -k 60 1800`. Under rule 15 I cite d5's regression (billing + users + 9 guards, 2772 OK at af5cee04) and don't repeat it.

**Verdict: VERIFIED.**
- The SM's four points hold: (a) every logger call in the two modules is ids-only and the success-path lines really fire; (b) the merges with H-60/H-57 and H-82 lost nothing; (c) the known limit is disclosed; (d) each refusal logs an ids-only reason line.
- Both findings of my pre-review were ruled into the branch by the SM and are closed: P1 by `af5cee04`, P2 by `f811d9bc`, the latter proven by my mutant Y3.

## Static review
**(a) Ids-only.** I walked every logger call in the two files myself (62 in `license_service.py`, 15 in `signals.py`). None passes an address, a name holding one, an exception's text or a user object. Exceptions are logged by class name.

**(b) The merges.** At the pre-review tip `932acc22`, H-80's own change to each file was byte-for-byte the same before and after the base update onto H-60/H-57. At the final tip, the difference from `83fe58ca` in production code is H-80's change plus the P1 fold only (`license_service.py`, `signals.py`, 9 added lines in `services.py`). The `services.py` lines sit in `activate_automatic_free_trial`, away from H-82's change.

**(c) The known limit (row H-89).** EVIDENCE.md has a "Known limit" section: 13 logger calls attach a traceback, whose exception text can carry an address; it names both leak shapes and row H-89. My own count gives the same 13 sites (9 + 4). The evidence's line numbers are those of the `exc_info=True` argument; mine below are the calls' first lines.

**(d) Reason lines.**
- H-86: the three refusals inside `_enroll_teacher_internal` (another school, an individual subscription, the seat limit) each log an ids-only WARNING before raising.
- P1: `activate_automatic_free_trial` logs a WARNING for a trial already used and an ERROR for a missing trial plan, each with the user's id, before raising.

**Rule 14:** there is no MagicMock in the new or changed tests.

**The evidence:** the committed logs match the stated gates. The addresses in them are test fixtures.

## My two pre-review findings (ruled in by the SM)
- **P1: a lost reason.** At `932acc22` the signal logged only "ValueError" for both free-trial refusals, so a missing trial plan (which denies every new teacher a trial) would not have been named in the log. Closed: the two reason lines above. The two renewal-failure lines, which logged only a class name, now carry a traceback and joined the known limit.
- **P2: a guard blind spot.** The source guard inspected only the arguments after the message, so `logger.info("… %s" % teacher.email)` passed it. Closed: the guard now walks the message too and requires a plain literal.

## Evidence
| Check | Result |
|---|---|
| **Run** @ 84c17542: my probes W1–W3 + `billing.tests.test_logs_carry_no_email` + `billing.tests.test_license_renewal_partial_failure` | **18 tests OK.** |
| **W1:** a recorder on both loggers while the licence paths run: creation with six kinds of address, batch add, filling the seats and one more, removal, re-adding, both renewals with one teacher failing, a replacement licence | **93 lines, 0 with an "@" in the message.** All ten steps ran. 24 of `license_service.py`'s 62 call sites fired. The lines I required to fire all did: queued invitation, enrolled, removed, reactivated, seat limit, individual subscription, not a business email, skipped enrolling, and both renewal-failure lines. |
| **W2:** users created through the signal (a teacher, a school admin, a teacher under the beta switch) | **0 with an "@".** With W1, 9 of `signals.py`'s 15 call sites fired. |
| **W3:** the two free-trial reason lines | Both fire once, at the ruled levels (missing plan: ERROR; trial already used: WARNING), each with the user's id and no "@". The signal's own line still ends "ValueError". The used-trial refusal's text does carry the address, so logging its class only is right. |
| **The known limit, seen live (observation):** | In W1 the two renewal-failure lines (`license_service.py:1952`, `:3848`) each logged a traceback containing an address, because I made the failure an `IntegrityError`-style text with one. The messages themselves were ids-only. This is the accepted limit, row H-89. |
| **My mutant Y3** (`"Created CreditWallet for teacher %s" % teacher.email`; on `test_vf_h80_mut`) | **KILLED by exactly the source guard** (`test_no_logger_call_formats_an_address`: "a message that is not a literal" and `teacher.email`). The other 17 tests pass, because that line never fires in practice (the signal creates the wallet first). So only the guard protects it, and before `f811d9bc` the guard would have passed it. This agrees with d5's G6. |
| d5's gates (cited) | at af5cee04: repro 15 tests, 10 failures + 1 error; (a) 16 modules 300 OK; (b) 23/23 mutants killed with sha-verified restores; (c) billing + users + 9 guards 2772 OK (skipped=6). |
| Hooks | `pre-commit run --from-ref 83fe58ca --to-ref 84c17542` passes, and each of the 7 non-merge commits passes. |
| Merges | `git merge-tree --write-tree` against `task/beta-batch-5` is **clean**, at its current tip `f4e6e5d3` (H1 merged since the branch's base). |

**What the dynamic probe does not cover.** 33 of the 77 call sites fired in my run. The other 44 (overage, plan and seat changes, the superadmin paths, most failure branches) rest on the source guard and my own walk of the calls, not on a fired line.

**Rule 17.** Every run had `PYTHONDONTWRITEBYTECODE=1`, and the mutant was applied with `python -B`. `__pycache__` under `billing/` and `users/` was deleted before the baseline, before the mutant and after the restore (the logs show 0 directories each time). The restored `billing/license_service.py` matched the commit blob's sha256, and no tracked file was changed afterwards.

**Disclosure: two baseline runs were discarded, both for faults in my own probe.**
1. `runs/h80_84c17542_PROBE_FAULT_1.log`: W1's first step asked for six teachers on a four-seat licence, so licence creation refused the request and W1 stopped. The Y3 run chained after it (`runs/h80_mutant_Y3_84c17542_PROBE_FAULT_1.log`) is discarded with it.
2. `runs/h80_84c17542_PROBE_FAULT_2.log`: W1 required a "Created CreditWallet" line that never fires in practice, and my renewal failure was injected into the wrong function, so the renewal-failure lines did not fire. That run already showed 0 messages with an "@".

I fixed the probe each time and re-ran; the results above are from the third baseline and the Y3 run after it. Neither fault was in d5's code. Each run took about 20 seconds, inside the one slot.

Logs: `runs/h80_84c17542.log`, `runs/h80_mutant_Y3_84c17542.log`, and the three discarded logs named above. Probe: `h80_probe_test_vf1a_h80_probe.py`. Mutant: `h80_mutant_Y3.py`.
