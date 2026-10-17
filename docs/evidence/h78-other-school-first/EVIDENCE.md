# H-78: another school's teacher's billing status stays hidden; no address in the log

Branch `task/h78-other-school-first-b4`, off bundle 4's final tip 67a0681
(now beta). Author: d5. Test 757e724, fix 8a31d19, runner 679ee09, test
fix 45c36f2 (gated tip). Supersedes task/h78-other-school-first (bfcf355,
off abeda10; archived as `archive/h78-other-school-first-abeda10-bfcf355`,
never gated). ed copied 8a31d19 byte-identical into Epic A S7d (0dde18e).

## The defects

1. `_get_or_invite_teacher` checked "has an active individual subscription"
   before "belongs to another school". A school admin adding another
   tenant's teacher who pays for their own plan was told that teacher's
   billing status ("... active individual subscription ... Please cancel
   the individual subscription first").
2. The address reached the log. The individual-subscription refusal logged
   its message (address included), the not-business refusal did the same on
   its non-raising path, and `_invite_and_enroll_one_teacher` logged every
   refusal's text in its "Skipped enrolling" line.

## The fix (8a31d19, billing/license_service.py only)

- The school check moves above the subscription check. Every refusal's
  wording is unchanged (bundle 4's generic other-school text included).
- The individual-subscription refusal logs `Teacher <id> has an individual
  subscription: not enrolled.`; the not-business refusal logs `Not a
  business email: teacher not enrolled.`. Both log before the raise, on
  either path, like bundle 4's other two refusals.
- "Skipped enrolling a teacher in license <id> (school <id>): <class>",
  never the exception's text.

## Behaviour changes (4-point records)

**B1. The refusal for another school's teacher with an individual plan.**
1. Previous: the subscription refusal, naming their billing status.
2. New: "This teacher already belongs to another school."
3. Why it's correct: another tenant's teacher's billing status is not this
   admin's to learn. The admin's own (or an unattached) teacher still gets
   the subscription refusal: it's actionable, and theirs to see.
4. Tests: `test_another_schools_paying_teacher_gets_only_the_other_school_refusal`,
   `test_the_service_raises_the_school_refusal_first`,
   `test_the_non_raising_path_logs_the_school_refusal_first`, and the three
   controls.

**B2. What the enrolment refusals log.**
1. Previous: the addresses, via each refusal's message and the "Skipped
   enrolling" line's exception text; the raising subscription refusal
   logged nothing of its own.
2. New: ids and the exception class only; each refusal logs on both paths.
3. Why it's correct: logs carry no emails (standing rule); the ids still
   trace the case.
4. Tests: `test_the_subscription_refusal_logs_the_teacher_id_not_the_address`,
   `test_adding_teachers_logs_no_address_for_either_refusal` (every logger).

## Runs

| Step | Log | Result |
|---|---|---|
| Reproduce-first at 757e724 (disposable worktree, `test_h78_repro`) | `repro_757e724.log` | 8 tests: 6 failures, 1 error (every fix test, each for the intended reason); the 3 controls pass |
| (a) at 679ee09 | `a_modules_679ee09_red.log` | 1 failure: a test bug (below); chain stopped |
| (a) at 45c36f2: this module, bundle 4's `test_add_teachers_other_school_not_disclosed`, `test_license_teacher_changes_400` | `a_modules_45c36f2.log` | 17 OK |
| (b) battery, own DB `test_h78_mut` (dropped) | `b_mutation_battery_45c36f2.log`, `results.tsv`, `logs/` | **5/5 killed**, verified restore |
| (c) ONE regression: billing + all 9 beta-line guards, `-v 2`, UTC timestamps | `c_app_billing_guards_45c36f2.log.gz` | 2037 tests **OK**, 312.4 s; wall 340 s; no suspend |

Every run used `--settings=settings_worktree`, `EXEMPT_EMAIL_DOMAINS=`
empty, the 6G scope, timeout 1800 and (from 45c36f2) rule 16's
`systemd-inhibit`.

Mutants: O1 the pre-fix order; O2 the school check disabled; L1 the
"Skipped enrolling" line logging the exception text; L2 the subscription
refusal logging its message; L3 the not-business refusal logging its
message.

## Deviations

- **The test bug at 679ee09.** `test_the_non_raising_path_logs_the_school_refusal_first`
  searched `assertLogs` output for "billing", and that output carries the
  logger's name `billing.license_service`. Fixed test-only in 45c36f2
  (the records' messages). At 757e724 the same test failed earlier, on
  "belongs to school", so the reproduce-first stands. ed hit the same
  failure in S7d and re-copies the module from 45c36f2.
- A permission denial in d5's session held the gates until the founder
  approved them.
- Out of scope, logged as **H-80**: about 30 other logger calls in
  `license_service.py` still carry addresses.

## N1a fold (after 1a's VERIFIED-WITH-NOTES at 2d94c43)
On the SM's ruling, 1a's N1(a) is folded in: `_enroll_teacher_internal`
logged the individual-subscription refusal's text (the address) when the
teacher subscribed between the invite check and enrolment. It now logs
the same ids-only line as `_get_or_invite_teacher`; the raised message is
unchanged.

| Commit | What |
|---|---|
| `1b056b3` | 1a's record, verbatim |
| `8c248e8` | 1a's probe R1 adopted as `test_a_teacher_who_subscribes_before_enrolment_is_not_logged_by_address` |
| `9675d17` | the fix (one log call) |
| `0c6af7d` | mutant L4 |

| Gate | Result | Log |
|---|---|---|
| Repro at 8c248e8 (h78-repro worktree) | 9 tests, exactly 1 FAIL: the new test | `repro_n1a_8c248e8.log` |
| (a) module at 0c6af7d | 9 OK | `a_modules_0c6af7d.log` |
| (b) battery at 0c6af7d (`test_h78_mut`) | 6/6 killed, sha-verified restores; L4 kills the fold | `b_mutation_battery_0c6af7d.log` |
| (c) billing + 9 guards at 0c6af7d | 2038 OK, wall 329 s | `c_app_billing_guards_0c6af7d.log.gz` |

All under 0b's grant, 6G, `timeout -k 60 1800`, the rule-16 prefix,
`--settings=settings_worktree`. The seat-limit ids-only line (1a's R3) is
not folded: backlog H-86. `create_license_subscription`'s failed_results
log goes to H-80.
