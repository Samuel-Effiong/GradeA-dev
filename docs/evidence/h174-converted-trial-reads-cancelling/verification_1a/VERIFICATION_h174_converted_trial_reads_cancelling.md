# Verification: H-174: a plan bought during the free trial is not "scheduled to cancel" @ e94dc5c3

**Verifier:** 1a. **Author:** ed. **Date:** 2026-10-07. **Severity:** HIGH (Senior Manager).
**Branch:** `task/h174-converted-trial-reads-cancelling` @ **e94dc5c3**, the final tip, from beta `d7143538` (batch 11). Merges clean onto the pushed beta tip `7976a571` (`git merge-tree --write-tree`, no conflict). No model, no migration. Headed for batch 12b and, as a patch, for main.

Commits: `eb5719c3` tests first; `b60007c2` tests only (three older tests reversed); `f29bc45f` the change; `e92c7ea1`, `425d4e5d` docs and the first gate; `cc97616e` the delta's tests; `98c75afa` the delta's change; `b960b7cd`, `013774da`, `e94dc5c3` docs only. **No file outside `docs/` differs between `98c75afa` and `e94dc5c3`.** Outside `docs/`, the row changes six files against its base: `billing/services.py`, `billing/stripe_service.py`, `billing/views.py`, the new `billing/tests/test_converted_trial_is_not_cancelling.py`, and two older test modules (`test_subscription_cancel.py`, `test_subscription_reactivation.py`).

**What this record is.** I told the author at `425d4e5d` (by reading, before a final tip existed) that the first version of the "keep" correction looked at the flag `cancel_at_period_end` alone and so would undo, locally, a cancellation Stripe had scheduled for a DATE. The author built the answer (`cc97616e` tests, `98c75afa` change). This record verifies that delta and the row at its final tip: by reading the whole production change, the author's raw logs, and by my own probes and mutants.

**I ran at e94dc5c3** (2026-10-07; baseline 20:04:55 to 20:05:23, mutants 20:05:28 to 20:07:28; hooks 20:07:41 to 20:08:54 WAT) on 0b's GRANT, from my own detached scratch checkout, serial, each run once, under rule 16's `systemd-inhibit` (idle, sleep and lid switch), the 6G scope with `MemorySwapMax=0`, `nice -n 10 timeout -k 60 1800`, `PYTHONDONTWRITEBYTECODE=1`, `python -B`, `--settings=settings_worktree` (mutants: `settings_worktree_mut`), every run's output straight to its own file with stdin from `/dev/null`. Load at each start 3.62 to 4.00 (the grant's condition was 4.0 or lower; Q2 was held 20 seconds until it was). No timeout, no kill, no baseline fault of mine this time.

Under rule 15 I cite the author's gate (step 0, modules and guards, 25 mutants, at `b960b7cd`) and the author's billing regression (at `013774da`), and repeat neither. Under rule 20 I cite the author's gate, which ran `AutoGrader.tests_cache_bespoke_1114` (17 lines in its log); no serializer, cached route or request shape changes in this row.

**Verdict: VERIFIED-WITH-NOTES.** What the evidence lists under "What changes" is true on everything I read and drove. My `cancel_at` finding is closed: a cancellation Stripe has scheduled by date or recorded is no longer corrected away, and I drove the new branch in three shapes the author's tests do not use. I found no defect. The notes say what the row leaves; none asks for a change before the merge.

## What changes (checked by reading the code at the tip)
1. **Both conversions of a trial** (`finalize_trial_to_paid_conversion`, `finalize_trial_conversion_via_stripe`) set `auto_renew=True` and `cancelled_at=None`, and both names are in `update_fields`. A trial is born `auto_renew=False`; the same row becomes the paid plan, which is why the page read "scheduled to cancel".
2. **"Keep" (`resume`) and the shared core** (`SubscriptionReactivationService.reactivate_if_cancelling`). Order of the branches at the tip: (a) Stripe's flag True: undone at Stripe, as before; (b) flag False AND Stripe's answer carries `cancel_at` or `canceled_at` (any truthy value): nothing is changed, the result carries the new field `scheduled_at_provider=True`; (c) flag False (`is False`, so an answer without the flag is not "not cancelling") and none of the two dates: the local record is corrected under a row lock (active, not a trial, period not ended; sets `auto_renew` True and clears `cancelled_at`), no call to Stripe, `local_changed=True` only when something was written.
3. **The answer of `resume`.** For a trial the trial sentences come first, as before. Then: scheduled at provider: a sentence that a cancellation is scheduled with the payment provider and could not be undone, nothing changed; status `cancellation_scheduled` (not for a trial, whose status stays `already_active`). Corrected locally with nothing changed at Stripe: a "never scheduled ... has been corrected" sentence, status `resumed`. Otherwise as before.
4. **`select_plan`** counts a cancellation as undone only when Stripe was changed (`reactivation.stripe_changed`, was `.changed`), so a record corrected locally is not announced as "undone".
5. **The cancel route** treats a paid record with `auto_renew` False, no date and not a trial as never cancelled: Stripe is told, the date is stamped, and the answer is "will not renew" and not "already". A trial never gets a date.

## What I checked by reading
- **The whole production change** against `d7143538` (three files, all read): the lines above are what the code does. The only way into branch (b) is the flag being exactly False together with a truthy `cancel_at` or `canceled_at`; a date of `0` or `""` counts as no date (Stripe sends neither; nothing in a test says it).
- **The local correction cannot touch a trial or an ended period** (the lock re-reads the row and checks `is_active`, `is_trial` and `now < billing_cycle_end`), and it writes only the fields it changed plus `updated_at`.
- **Who calls the core.** Two callers only: `resume` and `select_plan` (searched in production code: `billing/views.py:1140`, `billing/stripe_service.py:2505`). `select_plan` ignores `scheduled_at_provider`; see N3.
- **The three reversed older tests** (`b60007c2`), against the author's section "one by one": the two in `test_subscription_reactivation.py` change an assertion that held only because of the fault (the record is now corrected, nothing changes at Stripe); the one in `test_subscription_cancel.py` changes the fixture (a record that really was cancelled now carries its date) and keeps every assertion. What the old "no-op when not cancelling" test also protected, "nothing is written when the record and Stripe agree", moved to `test_noop_when_stripe_and_the_local_record_agree`, which the author says nobody had seen red. **I saw it red** (Q4 below).
- **The author's raw logs, read by me from the commit** (each has exactly one Ran line, and 0 `FAIL:` or `ERROR:` lines except step 0): `prefix_base_production_failing_b960b7cd.txt.gz`: `Ran 28 tests`, `FAILED (failures=17, errors=1)`, 18 distinct red as written. `modules_and_guards_b960b7cd.txt.gz`: `Ran 585 tests`, `OK`, 17 lines of the cache module. `regression_013774da.txt.gz`: `Ran 2130 tests in 301.846s`, `OK`. `mutation_results_b960b7cd.json`: 25 entries, 25 KILLED. The first regression (Ran 2126; raw log lost in the machine stop) is not counted, as the author says.
- **Ancestry and merge.** `d7143538` is an ancestor of `e94dc5c3`; the merge onto `7976a571` is clean.

## What I checked by running (at e94dc5c3)

The author's 20 tests and 25 mutants cover the three conversions, the correction, the cancel answers and the two Stripe dates. My three probes attack what the author named and did not test. The failing method set of each mutant was written beforehand (`h174_expected_kills.txt`, written 20:00 WAT before any run). Rule 19: each probe was seen green on the tip and red under its own mutant; each mutant failed exactly the set written. Answers are read from `response.data`; my fixture class is the author's, subclassed through its module so its tests are not collected twice, and no helper is named `client`.

- **pa** a cancel date that has already passed, flag false: left alone, told "scheduled", record and `updated_at` untouched, Stripe not changed.
- **pb** a record with a plan change waiting (`pending_plan`, a schedule id) and a dated cancellation at Stripe: pending plan and schedule id kept, nothing written, no schedule call.
- **pc** a trial that has a Stripe id and a dated cancellation: still a trial, mark still false, status `already_active`, the trial sentence and not the provider sentence.

| Run | What it shows | Result |
|---|---|---|
| Baseline: my probe (3), the author's module (20), `test_subscription_reactivation` (25) | green at the tip | `Ran 48 tests in 3.691s`, `OK` |
| Q1: a cancel date counts only while in the future | **pa**: a date that has passed still counts | `Ran 48`, `FAILED (failures=1)`: pa |
| Q2: neither Stripe date counts | the author's three (dated, recorded, the core's report) and my **pa** and **pb** | `Ran 48`, `FAILED (failures=5)`: those five |
| Q3: the status says "cancellation_scheduled" for a trial too | **pc**: the clause "not for a trial", which the author says no test isolates | `Ran 48`, `FAILED (failures=1)`: pc |
| Q4: the correction is saved and reported even when nothing needed correcting | the author's `test_noop_when_stripe_and_the_local_record_agree`, `test_already_active_noop` and `test_right_after_the_purchase_the_answer_does_not_contradict_itself`: so the older "no-op" test the author could not show red **is able to fail** | `Ran 48`, `FAILED (failures=3)`: those three |

Every mutant log has "applied", "mutated sha differs: True", "restored_sha256_matches_commit_blob: True", 0 tracked changes after the restore, and exactly one Ran line. The pycache directories of the two apps were cleared before each run and after each restore (counted in each log).

**Commit hooks** over `d7143538..e94dc5c3`: exit 0, 19 passed, 0 failed, 0 tracked changes after.

**What I did not run:** the author's modules, mutants and regression (cited); nothing at Stripe, on a service, in a database other than the test one, or on the web page; no whole-tree scan.

## Notes
- **N1. Unknown: what Stripe's real answer carries after a cancellation is undone.** The new refusal reads `canceled_at` as a cancellation. If Stripe leaves `canceled_at` set on a subscription whose cancellation a customer undid (flag False again), "keep" would refuse to correct a record it could correct, and say a cancellation is scheduled. The author names the same unknown and took the cautious side. Nobody has seen a real Stripe answer; every test uses a stand-in. Worth one look at a real subscription that was cancelled and resumed.
- **N2. A race, read in the code, not run.** The correction (branch c) does nothing, silently, if the row is no longer active or its period has ended by the time the lock is taken (a renewal or expiry during the Stripe call). The caller then answers "already active and set to renew — nothing to resume", which may be untrue for that moment. The old flag-True branch raises `SubscriptionExpiredDuringRequest` for the same case. It writes nothing wrong; it is wording and a missed correction on a rare edge.
- **N3. `select_plan` ignores `scheduled_at_provider`.** A customer with a dated cancellation who changes plan is told nothing about it, as before this row. The author says so.
- **N4. Nothing else in the code reads a cancellation scheduled by date.** The webhook's sync does not copy it to our record and the page shows it only when our record happens to say "not renewing". "Keep" now stops undoing it wrongly; the wider gap is the author's new MEDIUM row.
- **N5. Records already converted are not reached** unless the customer presses "keep", changes plan, renews, or cancels. No correction command is built (the author says so).
- **N6. A side effect the Senior Manager accepted:** a record cancelled before the date column existed (mark false, no date) gets today's date on a repeat cancel.
- **N7. Stripe is a stand-in in every test**, the author's and mine. Not shown: behaviour at Stripe, on a service, in a database, on the web page, the order of Stripe's messages, or the patch working on main (the author applied it to copies of main's files only).
- **N8. A trial that carries a Stripe id** and a dated cancellation gets the trial sentence and `already_active`; by my pc and Q3 that is now a tested rule.
- **N9. The author's slips** (told in the hand-over: two commits inside a quiet window, `cc97616e`'s message saying all four tests were red before the change when one was green) are theirs to carry; I read the commits and they change nothing I verified.

## Credential check
My probe, mutant file, expected note and five logs, searched for a URL with anything in the password position and for assignment forms whose name contains PASS, PWD, SECRET, TOKEN or KEY, masked output only: 0 lines in each. No line over 4000 characters; no trailing whitespace. No archive among them. (The author's test file makes a test user with a made test password and an allow-listing marker; I did not touch it and it is not part of this delta.)

## Files (in `~/Documents/Projects/GAP-1a-records/`, sha256 prefixes)
- `h174_probe_test_vf1a_h174_probe.py` 2472f7d4b3fe4cd1
- `h174_mutants_Q.py` 82a3f9c623db1521
- `h174_expected_kills.txt` f1b91d5f75fc549a
- `runs/h174_e94dc5c3.log` 975e5d1b3d6ff8b9
- `runs/h174_mutant_Q1_e94dc5c3.log` b72accd19fc7cfdd
- `runs/h174_mutant_Q2_e94dc5c3.log` 6be860e191c8d17c
- `runs/h174_mutant_Q3_e94dc5c3.log` b0d0d48e05469941
- `runs/h174_mutant_Q4_e94dc5c3.log` 6ce50ce4517d380f

The runner (`~/Documents/Projects/GAP-1a-scratch/h174_run.sh`, d43ba2d591772111) and the comparison helper (`h174_expect.sh`, 9c419a6e52f7a2ff) were read by 0b before the grant.
