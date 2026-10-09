# Merge-down b14: the targeted runs and the one strict full run, green

Recorded by the Release Engineer (0b), 2026-10-09. 0b authored the merge-down and started every run; Verifier 2 verified the merge-down part (VERIFIED-WITH-NOTES at `8a83d13b`, record under `verification_v2/`, committed verbatim in `33389cd1`).

**What it is:** the Phase 2 line `phase2/epic-a` (`554fcf11`, which carries the AI-call record's slice 0) with beta `8567a7a0` (batches 12s, 13, 13h and 14; merge-base `035e0a07`; 148 non-merge commits) merged into it: the merge `a5c4ad11`, the fix `d4ff5be7`, four one-module commits (`02e3fdb8`, `38bdba5a`, `8782ac3d`, `8a83d13b`), then Verifier 2's record `33389cd1` (docs only). Slice 2 of the AI-call record is not in this bundle (the Senior Manager's ruling: it goes in the next Phase 2 bundle once the Checker has verified it). Branch `task/epic-a-merge-down-b14`; not pushed.

**The four content conflicts and what each needed** (also in `merge_commit_message.txt`): `users/admin.py` (both sides kept: `history.record_bulk(queryset, is_active=False, token_epoch=Case(...))`); `users/views.py` imports (union, three unused names dropped); `users/views.py` `register_student` (beta's closed door, H-152; the Phase 2 line's budget, REGISTRATION_PAUSED and audit events on that door are dead and marked with a comment naming H-207); `classrooms/tests_h99_placeholder_email.py` and `classrooms/tests_roster_ready_to_use.py` (imports).

**A fault the textual merge hid (found by the targeted run, fixed in `d4ff5be7`):** beta's two new arms in `/auth/verify` (H-164, H-202) called the Phase 2 line's three-argument `refuse()` with one argument: every such refusal answered 500. 11 tests were red. The signature hunt over the 20 files beta touched (`signature_hunt_md_b14.txt`) found 3 changed signatures; only `refuse()` mattered.

**Targeted runs (17 modules of the door and sign-in code, `mdb14_modules.sh`):**

| Tip | Result | Log |
|---|---|---|
| `a5c4ad11` (the merge) | Ran 417, FAILED (failures=16): 11 from the `refuse()` fault, 5 about the closed student door | `mdb14_modules_first_run_a5c4ad11_16_red.log` |
| `d4ff5be7` (the fix) | Ran 417, FAILED (failures=5): only the closed-door tests | `mdb14_modules_after_refuse_fix_d4ff5be7_5_red.log` |
| `8a83d13b` (the door tests removed, adapted or added, one commit per module) | **Ran 414, OK (skipped=3)** | `mdb14_modules_8a83d13b_414_ok.log` |

The five closed-door tests: four removed as door-only (the three tests of `users/tests_s7d_registration_paused.py` that posted to the old door, plus its fourth, which only told the old door's pause from its rate limit; and `test_student_invitation_bad_code_survives_the_rollback`), one adapted (`test_a_student_invitation_names_the_invitee` now activates through `/auth/verify`); added: `users/tests_register_throttle_school_admin.py` (the per-network rate limit on the school-admin door) and `test_the_beta_refusals_are_audited_like_a_wrong_code`. The Senior Manager approved the classification before any commit.

**Command:** the strict full run under rules 12, 13, 16 (revised), 17 and 18, by `strict_bundle.sh 33389cd1 mdb14 Grade-Automator-Plus-epic-a-merge-down-b14 task/epic-a-merge-down-b14` (a copy is beside this file).

| Tip | Suite (WAT) | Result |
|---|---|---|
| `33389cd1` | 2026-10-09 11:48:21 to 11:58:28 (570 s of tests; 607 s wall) | **Ran 7392 tests, OK (skipped=30)**, exit 0, 0 FAIL, 0 ERROR, 0 blocked outbound, no suspend, watchdog never fired; mypy Passed and makemigrations "No changes detected" by the script's own pre-checks |

**Tests per app** (they sum to 7392): ai_processor 1117, assignments 745, audit 367, AutoGrader 754, billing 2200, classrooms 512, dashboard 270, students 629, users 798.

**Load average (1 minute):** 4.66 at the start, which is over the 4.0 line for starting a run: 0b read 4.84 just before and started anyway (the script does not gate on load); 7.38 at the end. No timing test went red.

**The log:** `strict_mdb14.log.xz` (9,045,661 bytes unpacked, sha256 `f0ab31704cde7de355cb5f3d688b64f463e4e68f8184e2554ec5ed5df862ed27`); `strict_mdb14.summary.txt` is the script's summary.

**Credential pattern check (0b, `scripts/credscan.py`'s scan function on each file added with this record, values masked):** no address with a password part in any file; the literal rows are names holding only the word KEY (cache keys: 5 in the targeted log, 35 in the packed log) and the two known warning lines from the AI module's test; the rest are code expressions, placeholder words and variables or masks.

**Not in this run:** the dead helpers of the closed student door are still in the code (row H-207); the closed door's per-address rate limit has no test on either line (row H-212; Verifier 2's mutant D4 survived by design); slice 2 and the follow-ups of the AI-call record.

**After the run tip:** the commit that adds this record changes files under `docs/` only.
