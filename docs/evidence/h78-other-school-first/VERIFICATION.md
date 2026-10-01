# Verification: H-78, "another school" before "individual subscription", and no address in the log @ 2d94c43

**Verifier:** 1a. **Author:** d5. **Date:** 2026-10-01.
**Branch:** `task/h78-other-school-first-b4` @ **2d94c43**, off bundle 4's final tip `67a0681`:
- `757e724`: the tests
- `8a31d19`: the fix
- `679ee09`: the mutation runner
- `45c36f2`: a test-only fix
- `2d94c43`: the evidence

The evidence is in `docs/evidence/h78-other-school-first/`.

I ran in 0b's slot from my detached scratch checkout at 2d94c43, with its own test DB (`test_vf_h78`). The wrapper was rule 16's `systemd-inhibit`, the 6G scope with `MemorySwapMax=0`, `nice -n 10` and `timeout -k 60 1800`.

Under rule 15 I cite these of d5's results and don't repeat them:
- the regression: billing plus the 9 beta-line guards, 2037 OK;
- the battery: 5/5 killed;
- the reproduce-first: 6 failures and 1 error at 757e724, with the 3 controls passing.

**Verdict: VERIFIED-WITH-NOTES.**
- The fix in `_get_or_invite_teacher` is correct. Another school's teacher now gets only the generic school refusal, whatever their plan, on both the raising and the non-raising path.
- Each of `_get_or_invite_teacher`'s four refusals logs ids only. The "Skipped enrolling" line logs the class.
- **N1:** three log sites outside the fix still print the address when a teacher is refused for an individual subscription. Two of them (`:1372`, `:1100`) are **not** on H-80's line list. The exact paths are below.

## d5's three questions
**1. Did any caller rely on the subscription refusal reaching another school's admin? No.**
- `IndividualSubscriptionConflictError` is caught only together with `ValueError` (`license_service.py:768` and `:856`). Both handlers treat the two the same way: log, then return a failed result carrying `str(exc)`.
- `AutoGrader/error_messages.py` lists it only for message mapping.
- No caller branches on which refusal came first. The new order changes only what another school's admin is told, which is the point of the fix.

**2. Does the class-only "Skipped enrolling" line lose operator information? Only for refusals that have no reason line of their own.**
- Each of `_get_or_invite_teacher`'s refusals (not-business, user type, other school, subscription) logs an ids-only reason line before the "Skipped" line, so nothing is lost for them.
- Refusals raised inside `_enroll_teacher_internal` have no such line. Probe R3, the seat limit, shows what the operator sees:
  - `Skipped enrolling a teacher in license <id> (school <id>): ValueError`
  - That line has no teacher id and no reason. The admin's response does give the reason ("has reached its seat limit of 1").
- **Suggestion (LOW, not blocking):**
  - Give the seat-limit and "does not belong to school" refusals in `_enroll_teacher_internal` an ids-only line of their own, as `_get_or_invite_teacher`'s refusals now have. Use the teacher id, which is known there.
  - Alternatively, add the teacher id to the "Skipped" line when `_get_or_invite_teacher` returned a user.

**3. Is a refusal path still logging the address? Yes, three sites, all on the individual-subscription refusal.**

| Site @ 2d94c43 | What it logs | Reached by | On H-80's list? |
|---|---|---|---|
| `license_service.py:1372` `_enroll_teacher_internal`, `logger.warning(error_msg)` | "Teacher <email> has an active individual subscription…", the same text that `8a31d19` stopped logging at `:1196` | (a) the race: the check at `:1192` passes, then the teacher subscribes before enrolment; (b) the licence carry-forward (below) | **No.** The list has 1385, a different line. A heuristic grep misses this one because the address is inside a pre-formatted variable. |
| `license_service.py:857–862` `_carry_forward_teacher_allocations`, except | "Failed to carry forward teacher <email> … : <exc text>", which contains the email twice | replacing a school's licence (`create_license_subscription`, `carry_forward_teachers=True` by default) when a carried teacher now has an individual subscription | Yes (859) |
| `license_service.py:1099–1105` `create_license_subscription`, `logger.error(... FAILED: %s, failed_results)` | the result dicts: `'email': <email>` and `'error': <refusal text with the email>`, for **any** failed invitation or carry-forward, at ERROR level | every licence creation with at least one refused teacher (the serializer at `serializers.py:2150`, the Stripe webhook at `stripe_service.py:3546`) | **No** |

- **Probe R1** (the race, through `_invite_and_enroll_one_teacher`) fails on `:1372` alone.
- **Probe R2** (a real `create_license_subscription` replacing the licence, with a carried teacher who now subscribes) fails on all three: 3 address-carrying lines, one of them at ERROR.

## Notes
**N1 (privacy, LOW-MEDIUM, the same class as H-78 and H-80): the three sites above.**
- **Recommendation: `:1372`.** It is the same refusal text H-78 de-addressed, and the fix is the same one line as `8a31d19`'s `:1196`: `logger.warning("Teacher %s has an individual subscription: not enrolled.", teacher.id)`. Fold it into H-78, with R1 as its test (R1 passes once only `:1372` is fixed).
- **Recommendation: `:1100`.** Add it to H-80 explicitly. It logs whole result dicts, so the fix is to log ids, or counts plus `teacher_id`, rather than to edit one string. It also catches every other refusal's text.
- **Recommendation: `:859`.** It is already on H-80.
- **R2** is ready to be H-80's end-to-end test for the carry-forward path.
- **Whether `:1372` is folded in now or goes to H-80 is the SM's call.**

**N2 (for the SM, pre-existing, outside H-78's scope): an *unattached* paying teacher's status is still disclosed.**
- Probe R4: an address whose account has no school and an active individual plan.
- Any school admin's add-teachers call gets `200 … "Teacher <email> has an active individual subscription…"`.
- H-78 fixed this for another school's teacher. For a school-less account the refusal still tells an arbitrary school admin that the address has a paid plan. A school-less teacher is a plausible recruit for any school, so this may be acceptable. It's a product decision, so I'm not filing a row myself.

**N3 (cosmetic).** In `8a31d19` the not-business refusal now logs its ids-free line on the raising path as well as the non-raising one. Before, it logged only on the non-raising path. This is harmless and consistent.

## Evidence
| Check | Result |
|---|---|
| **Baseline** @ 2d94c43: my probe + `billing.tests.test_other_school_before_subscription` + `billing.tests.test_add_teachers_other_school_not_disclosed` | **15 tests, 2 failures, exactly N1** (R1: `:1372`; R2: `:1372`, `:859`, `:1100`). **Every d5 test passes.** R3 and R4 are observation probes and pass. Run time 19 s. |
| d5's battery (cited) | 5/5 killed at 45c36f2: O1 the pre-fix order; O2 the school check disabled; L1, L2, L3 each refusal's log line reverted. Together they cover every line `8a31d19` changes, so I added no mutant of my own. |
| d5's reproduce-first (cited) | 6 failures and 1 error at 757e724, each for the intended reason; the 3 controls pass. |
| d5's regression (cited) | billing + 9 beta-line guards, 2037 OK, `-v 2` with UTC timestamps, no suspend. |
| Hooks | `pre-commit run --from-ref 67a0681 --to-ref 2d94c43` passes, and each of the 5 commits passes. |
| Merges | `git merge-tree --write-tree` against 67a0681 (bundle 4's final tip) is **clean**. It is also clean against `task/h65-beat-locks` @ c5790bb and `task/h76-plan-change-bucket-processed` @ 477eeed, the other beta-line items on this base. |

Log: `runs/h78_baseline_2d94c43.log`. Probe: `h78_probe_test_vf1a_h78_probe.py`.
