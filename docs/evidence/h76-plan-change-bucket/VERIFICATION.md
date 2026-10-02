# Verification: H-76, the plan change retires its old monthly bucket as processed @ c46fdbf

**Verifier:** 1a. **Author:** d5. **Date:** 2026-10-01.
**Branch:** `task/h76-plan-change-bucket-processed` @ **c46fdbf** (code `5b25650`, base-updated by 0b onto bundle 4's final tip `67a0681` as `430f4e9`; `41f3acc` and `c46fdbf` are evidence). The evidence is in `docs/evidence/h76-plan-change-bucket/`.

The run was wrapped (6G, `MemorySwapMax=0`, `nice -n 10`, `timeout -k 60 1800`) in 0b's slot, from my detached scratch checkout at c46fdbf with its own test DB (`test_vf_mcg`). Under rule 15, d5's regression (billing + the 9 beta-line guards, 2031 OK) is cited, not repeated. On d5's disclosed deviation: that run used the shared default test DB name. The log shows a fresh create and a pass, so the result stands as d5 says.

**Verdict: VERIFIED-WITH-NOTES.** The one-line fix is correct and complete for `apply_immediate_plan_change`. Nothing relied on the retired bucket staying unprocessed.

**N1 is substantive:** the **same defect exists at a sibling site**, `LicenseSubscriptionService._enroll_teacher_internal`. My probe confirms it on this tip. I recommend folding it into H-76 (one line, plus my probe as the test); **whether to fold it in or open a new row is the SM's call.**

## d5's two questions
**1. Did any caller rely on the plan change's old bucket staying unprocessed? No.**
- **The rollover fix's cleanup guard** (keep an entitled owner's newest unprocessed MONTHLY bucket): after a plan change, the newest unprocessed MONTHLY bucket is the new plan's either way.
  - Before H-76, the old bucket (expired at the change, unprocessed, and not the newest) was written off at 05:00 with a second EXPIRE: the double count.
  - After H-76 the cleanup never selects it (`is_processed=False` filter).
- **Every other selector is unaffected:**
  - the next mid-cycle grant (`services.py:730`), the renewal (`:869`) and the licence helper (`license_service.py:515`) all take the newest unprocessed MONTHLY bucket, which is the new plan's in both cases;
  - `activate_subscription` (`:271`) and the plan change itself (`:539`) filter `expires_at > now`, which the retired bucket already fails;
  - spending filters on expiry only (`models.py:976`).
- **Reporting:** `qa_console` only displays the flag. The live-QA invariant (`live_qa/scenarios_deep.py:443`, "at most one MONTHLY bucket is left un-retired") expects exactly what H-76 now does. No serializer or dashboard reads `is_processed`.

**2. The plan-change grace test's `.latest("created_at")` (`test_monthly_rollover_cleanup_race.py:771`): yes, tighten it, and fix its comment.**
- The comment above it ("The plan change retires the old one by expiring it without marking it processed: noted in EVIDENCE") is **now stale**.
- Replace the lookup with `CreditBucket.objects.get(wallet__user=self.user, bucket_type=MONTHLY, is_processed=False)`. It asserts that exactly one MONTHLY bucket is left un-retired after the change, which also pins H-76 from the grace test's side.
- Test only.

## Notes
**N1 (substantive, the same defect): `_enroll_teacher_internal` retires the previous MONTHLY bucket unprocessed after rolling it over.**
- `license_service.py:1526–1529`: when a teacher who held an individual plan joins a licence (a new allocation), enrolment:
  - rolls the old bucket's unused credits into CARRY_OVER ("Rollover from previous subscription…");
  - then retires it with `existing_monthly.expires_at = now` and `update_fields=["expires_at", "updated_at"]`, **without `is_processed = True`**.
- **Reachability:** enrolment refuses a teacher whose individual subscription is still **active**. The reachable case is a former subscriber (subscription ended or cancelled) whose last MONTHLY bucket is still live when their school adds them.
- **Probe E1** (`h76_probe_test_vf1a_h76_probe.py`) runs the real enrolment, then the real cleanup an hour later. Result:
  - `rolled_over=[1500000]`
  - `old_bucket_processed_after_enrolment=False`
  - `expired_again_by_cleanup=[6000000]`

  The EXPIRE covers the whole 6,000,000 remainder, including the 1,500,000 already granted as carry-over. It's the same ledger double count as H-76, and balances are unaffected.
- **Suggested fix (one line):** `existing_monthly.is_processed = True`, added to `update_fields`, mirroring 5b25650. Adopt E1 as the test.
- `remove_teacher_from_license` (`license_service.py:1801`) is different and correct as it is: no rollover there, so the cleanup's EXPIRE records a real forfeiture.

**N2 (the F6 query, for the founder's run):**
- Pre-H-76 (and pre-N1-fix) data contains EXPIRE rows "Automatic expiration of MONTHLY bucket." for buckets already rolled over by a plan change or an enrolment.
- `detect_monthly_rollovers_lost_to_cleanup.sql` reports such a row only when a new MONTHLY grant follows within 3 days (the plan change's or enrolment's own grant comes **before** the EXPIRE), so false positives are incidental.
- When reviewing its rows, treat a written-off bucket retired at the moment of an "Immediate upgrade…" or "Rollover from previous subscription…" grant as a ledger double count, not a lost rollover.
- The historical double counts could get a read-only query of their own if the founder wants the ledger corrected. Balances are right either way.

## Evidence
| Check | Result |
|---|---|
| **Baseline** @ c46fdbf: my probe + `billing.tests.test_plan_change_retires_old_bucket` + `billing.tests.test_monthly_rollover_cleanup_race` | **42 tests, 1 failure, exactly N1** (E1: `[6000000] != []`). Every d5 test passes, including both H-76 tests. |
| d5's battery (cited) | 2/2 killed at 5b25650: P1 (the assignment removed) and P2 (not saved). Between them they cover every way to undo the one-line fix, so I added no mutant of my own. |
| d5's reproduce-first (cited) | 2 of 2 failed on e190f06. |
| Hooks | `pre-commit run --from-ref 67a0681 --to-ref c46fdbf` passes, and each of the 4 commits passes. |
| Merges | `git merge-tree --write-tree 67a0681 c46fdbf` (bundle 4's final tip) is **clean**, and so is the merge with H-65 (`51fbb0e`, the other beta-line item on this base). |

Log: `runs/h76_baseline_c46fdbf.log`. Probe: `h76_probe_test_vf1a_h76_probe.py`.

---

# Delta re-check: the N1 fold @ aa174a33

**Date:** 2026-10-01. By the SM's ruling, this covers **only the N1 delta**.
- **Commits over c46fdbf:**
  - `951fe38`: this record
  - `e5a75d8`: my E1 as `LicenceEnrolmentRetiresOldBucketTests`, plus the grace test's `.get(..., is_processed=False)` with its comment fixed (d5's question 2)
  - `477eeed`: the fix, with mutants P3 and P4
  - `91eadbd`, `aa174a33`: evidence
- **Setup:** my scratch checkout at aa174a33, `test_vf_mcg`, in 0b's slot, with rule 16's `systemd-inhibit`, 6G and `MemorySwapMax=0`, `nice -n 10`, `timeout -k 60 1800`.

**Verdict: VERIFIED.** N1 is closed.

## The fix
- **`477eeed`** (`license_service.py:1527–1534`, `_enroll_teacher_internal`): the old MONTHLY bucket is now retired with `is_processed = True`, and `update_fields` is `["expires_at", "is_processed", "updated_at"]`. This is exactly the suggested one line, mirroring `5b25650`.
- **Behaviour note (informational, no change asked).** The flag is set whether or not anything was rolled over: when nothing was unused, when the rollover was 0, or when max_bank suppressed it. So the cleanup no longer writes an EXPIRE for the part that isn't carried over.
  - That is already the deliberate policy at every sibling retire site: the plan change (`services.py:593`), the mid-cycle grant (`:789`, whose comment documents it), the renewal (`:925`), `activate_subscription` (`:337`) and the licence rollover (`license_service.py:555`).
  - Enrolment now matches them. Before the fix it was the only site that recorded the forfeited remainder, but it also recorded the carried slice a second time.

## Evidence
| Check | Result |
|---|---|
| **Delta run** @ aa174a33: probe E1 + `billing.tests.test_plan_change_retires_old_bucket` + `billing.tests.test_monthly_rollover_cleanup_race` | **44 tests OK** (3.6 s). E1: `rolled_over=[1500000] old_bucket_processed_after_enrolment=True expired_again_by_cleanup=[]`. At c46fdbf the result was `False` and `[6000000]`. |
| **My mutant Z6** (on `test_vf_mcg_mut`, dropped): the flag is set and saved, but `expires_at` is dropped from `update_fields`, so the old bucket stays live. The production file's sha matched the commit blob after the restore. | **KILLED** by `test_the_old_bucket_is_retired_as_processed` (1 failure in 4). This is a different undo from d5's P3 (the flag not set) and P4 (the flag not saved). |
| d5's gates (cited) | reproduce-first at e5a75d8: exactly the 2 new tests fail; at 477eeed: 43 OK, 4/4 killed (P1–P4); the regression at 91eadbd: billing + 9 guards, 2033 OK. |
| Hooks | `pre-commit run --from-ref c46fdbf --to-ref aa174a33` passes, and each of the 5 commits passes. |
| Merges | The branch is based on 67a0681. `git merge-tree --write-tree` against 67a0681 is **clean**. It is also clean against H-65 @ e3d7751 and H-78 @ b9e4ccb, the other two beta-line items, which also touch `license_service.py`. |

Logs: `runs/h76_n1_aa174a33.log`, `runs/h76_n1_mutant_Z6.log`. Probe: `h76_probe_test_vf1a_h76_probe.py` (unchanged).
