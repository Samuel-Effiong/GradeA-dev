# H-58 / H-59: a licence counts each teacher once, and refuses no seats before payment

Branch `task/license-seat-counting` off beta `463e222` (the licence-seat hotfix is on beta). Beta-bound, batch-3; the verifier is 1a. No migration.

Backlog: H-58 = the hotfix verification's N4, H-59 = N5 (`docs/evidence/hotfix-license-seat-400/VERIFICATION.md`). The SM widened H-58 to the school admin's Add teachers door and to the case-insensitive account lookup.

## Defects
**H-58: one teacher counted as two seats.** `check_seat_capacity` lower-cased the listed emails but:
- did not de-duplicate them, so `["dup@", "DUP@"]` counted as 2 teachers;
- compared them case-sensitively with the carried-forward teachers' **stored** emails. `normalize_email` lower-cases only the domain, so a stored `Kept@` and a listed `kept@` counted twice.

The same defect sat on the school admin's `POST license-subscriptions/<id>/add_teachers`:
- `add_teachers_batch` took the raw request list;
- it did no normalization and no de-duplication;
- it checked for an already-active teacher with an exact-case lookup.

So a school with enough seats was refused ("no seats left"), or had to buy one more.

**Account lookup (same path).** `_get_or_invite_teacher` lower-cased the email, then matched it **exactly**. So an existing account stored as `Mixed.Case@` was not found for `mixed.case@`, and a second account was created and invited. The `email` column's uniqueness is case-sensitive.

**H-59: no seats reached checkout.** `max_seats` is optional on create, and the view fell back to 0. `check_seat_capacity` read 0 as "unlimited", so Stripe checkout went ahead with `quantity: 0`. The webhook's `create_license_subscription` then refused "max_seats must be a positive integer" **after the school had paid**.

## Fix (`billing/license_service.py`)
- `check_seat_capacity`:
  - refuses `max_seats <= 0` first, with `LicenseRequestError("max_seats must be a positive integer")`. The Stripe checkout and the webhook's create both run it, so the refusal now happens before payment. The same guard is removed from `create_license_subscription`, where it is redundant.
  - lower-cases the carried-forward emails;
  - counts the listed emails through `normalize_teacher_emails`.
- `normalize_teacher_emails`: strip, lower-case, drop blanks, keep each address once, in the order given.
- `teacher_account_for_email`:
  - an exact match first;
  - otherwise a case-insensitive match (`order_by("email")` for determinism if legacy rows differ only by case).
  - Used by `_get_or_invite_teacher` and by `add_teachers_batch`'s already-active check.
- `add_teachers_batch` iterates `normalize_teacher_emails(teacher_emails)`.

**Unchanged:**
- The Stripe view branch still maps any `ValueError` to 400 (N2, separate backlog).
- A seat refusal is still a `LicenseRequestError`, so its message still reaches the user (hotfix).

## Tests (`billing/tests/test_license_seat_counting.py`, 11)
Own fixtures, no borrowed TestCase methods. Every mocked value that can reach a response is a real scalar (team rule 14).

**Create, H-58:**
- The same teacher listed twice ("dup@", "DUP@ ") takes one seat, OFFLINE and STRIPE.
- A carried-over teacher stored as "Kept@" and listed as "kept@" is one seat, OFFLINE and STRIPE.
- The refusal message counts distinct teachers.

**Create, H-59:**
- A STRIPE create without `max_seats` is a 400 with the message, and `Session.create` is not called.
- The shared check refuses 0 and -1.

**Add teachers:**
- The same teacher listed twice takes one seat.
- Re-adding an active teacher in capitals takes no seat.
- An active teacher **stored** with capitals takes no seat on a full licence (kills S8).
- An account stored as "Mixed.Case@" is enrolled, not duplicated.

## The 2026-09-30 OOMs (found and fixed here)
The first version of the H-59 Stripe test left `Session.create().url` a bare MagicMock. Beta's code does not refuse the request, so it reaches checkout (the reproduce-first run), and DRF's `JSONRenderer` expanded the mock without bound. That caused both machine-wide OOMs (11:42, 12:03) and the 12:22 rule-13 cap kill.

Measured, one test at a time under a 2G cap:
- the other tests peaked at about 195 MB;
- that test hit the cap in about 9 s;
- with a real URL string it peaked at 194 MB on beta code;
- `JSONRenderer().render({"x": MagicMock()})` alone blew past 1G.

Product code is bounded; it was a test artifact. It is fixed in 51f2aa5 and became team rule 14.

## Gates
Every run was wrapped in `systemd-run MemoryMax=6G`, `nice -n 10`, a timeout, `EXEMPT_EMAIL_DOMAINS=` and `--noinput`, one at a time.

| Gate | Result |
|---|---|
| Reproduce-first | beta `463e222`'s service against the new tests (`prefix_beta_463e222_failing.txt`): **11 of 11 fail** |
| On the fix | **11 OK** |
| 2 Mutation | `mutate.py`, 8 mutants, anchors asserted unique, over the new file + `test_license_seat_400` + `test_license_teacher_changes_400`: **8/8 killed** (`mutation_log.txt`) |
| 1 Regression | `billing`: **1676 OK** |
| mypy | whole-repo `pre-commit run mypy --all-files`: **Passed** |
