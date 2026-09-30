# Verification: H-58/H-59 licence seat counting @ fdfe423

**Verifier:** Verification Engineer (1a, grade-automator-plus-c2). **Author:** Security (ed).
**Base:** beta 463e222 (beta-bound, batch-3). **Date:** 2026-09-30.

**Verdict: VERIFIED-WITH-NOTES.** The code is correct and closes my N4 and N5 from the hotfix. **One item, R1, is required before merge:** a one-line, test-only change for team rule 14.

## What I checked
My own detached checkout with its own test DB. Every run used `systemd-run --user --scope -p MemoryMax=6G -p MemorySwapMax=0`, `nice -n 10` and `timeout -k 60 1800`.

| Check | Result |
|---|---|
| Scope | `billing/license_service.py` (+50/−20), one new test file and evidence. No migration. 1bd709e and 51f2aa5 are WIP, superseded by fdfe423. |
| H-59: the refusal moved into `check_seat_capacity` | Both callers run it unconditionally, before any write: `create_license_subscription` (so the webhook too, through `_handle_license_create`) and `StripeCheckoutService.create_license_session`, before `Session.create`. The serializer also refuses a `max_seats` of 0, so there are two layers. The webhook refused 0 before this change as well (the create guard), so in-flight sessions behave the same. |
| "0 = unlimited" | This still applies to licences **already stored** with `max_seats=0`: `seats_remaining` returns `None`, and enrolment grants the full allocation. Neither is touched. Only creation and checkout refuse 0. See N3 for the docs. |
| H-58: counting | `normalize_teacher_emails` strips, lower-cases, drops blanks and de-duplicates in order. Carry-forward emails are lower-cased. `create_license_subscription` enrols `genuinely_new_emails` (already de-duplicated). Carry-forward re-enrols from **allocations** (user objects), not emails, so lower-casing can't create a duplicate account. |
| H-58: `add_teachers_batch` | Normalised and de-duplicated. The "already active" check and the seat check both use the normalised list. |
| H-58: account lookup | `teacher_account_for_email`: exact match first, then `iexact` ordered by email. It's used by `_get_or_invite_teacher` and `add_teachers_batch`. |
| ed's seat tests plus my 6 probes | **35 OK**, 206 MB peak |
| Whole `billing` app | **Ran 1682, OK** (ed's 1676 plus my 6 probes), 392 MB peak, 263 s |

## My probes (`billing/tests/test_vf_seats_probe.py`, uncommitted; real `SimpleNamespace`/dict Stripe stubs, no bare mocks)
| Probe | Result |
|---|---|
| **Stripe metadata round trip.** Checkout with `["Round@SeatCap.edu", "round@seatcap.edu "]` on 1 seat → 200, with the real `checkout_url`. The captured metadata `teacher_emails='Round@SeatCap.edu,round@seatcap.edu'` is fed to the **real** `StripeWebhookHandler._handle_license_create` | the licence gets **1 teacher and 1 account** |
| An existing account `Mixed.Case@…`, listed as `mixed.case@SEATCAP.edu` | reused: one allocation, for the existing user, and no second account |
| Legacy case-twins `PAIR@` and `pair@` | a lower-case request finds `pair@`; an upper-case request finds `PAIR@`. The exact match wins in both directions. |
| **Legacy twins carried forward:** `leg@` and `LEG@`, both active on the old licence, then a new licence with 1 seat and carry-forward on | 201. The new licence holds **1** teacher, so it is not over its seats. The second twin is refused by the enrolment layer's own seat check and listed as a failed carry-forward in the response. That's safe; see N2. |
| H-59 on STRIPE: 0 seats, and `max_seats` omitted, with teachers listed; and 0 seats with no teachers | all 400, and `Session.create` is **never called** |

## My mutants (2, beyond ed's 8)
| Mutant | ed's tests | My probes |
|---|---|---|
| Q1: the exact match no longer wins (`iexact` only) | **SURVIVED** | killed by the upper-case twin request. (With a lower-case request, the collation happens to put the lower-case twin first, so only the upper-case direction discriminates.) See N1. |
| Q2: 0 seats refused only when teachers are listed | killed: `test_the_shared_seat_check_refuses_no_seats` | survived at HTTP level (the serializer refuses first) |

All restores were sha-checked.

## Notes
**R1 (REQUIRED; tests only; rule 14).**
- `post_stripe` and `test_stripe_checkout_without_max_seats_is_refused_before_payment` patch the whole `billing.stripe_service.stripe` module with a bare `MagicMock` and set only `Session.create.return_value.url`.
- `create_license_session` also logs `session.id` (`logger.info(…, session.id)`), so a bare MagicMock reaches a **log line**, which rule 14 names.
- It's bounded today, since `str()` of a mock is short, but the rule exists because the same pattern OOM'd the machine twice.
- Fix: give the stub a real `id` too, or return `SimpleNamespace(id="cs_test_…", url="https://…")` from a patched `Session.create`, as my probe does.

**N1 (non-blocking).** The docstring's "an exact match wins" isn't pinned: Q1 survives your tests. Callers pass lower-cased addresses today, so it matters only for a mixed-case lookup. Adopting my twin probe would pin it.

**N2 (informational).** With legacy case-twin accounts both active on the old licence, the carried-forward count is 1 (by lower-cased email) while 2 allocations are carried. The enrolment layer's seat check refuses the second, and it shows as a failed carry-forward in `teacher_invitations`. That's safe. Counting carried teachers by user id would make the pre-check match exactly.

**N3 (docs drift, backlog).** Several places still say "0 = unlimited" for the input: `stripe_view_schemas.py` (the `max_seats` help) and the model/migration `help_text`. `create_license_subscription`'s own log line still formats 0 as "unlimited", which is now unreachable on create. Creation and checkout now refuse 0. That is still true for stored legacy licences only.
