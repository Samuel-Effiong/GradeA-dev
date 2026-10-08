# subscription-me-none-slim: Verification

Branch `task/subscription-me-none-slim` @ 9888ceb (a6132d9 code, 9888ceb samples + evidence), off beta 4b902fc.

## (1) ACTIVE/EXPIRED unaffected, proven on the rendered payloads
- `billing/views.py` diff: only `_none_subscription_response()` (single caller, the NONE fall-through) and the NONE `extend_schema` example. No ACTIVE/EXPIRED code is in the diff.
- Stronger than a diff read: I copied the new sample test onto plain beta 4b902fc and generated all 9 payloads there, generated them again on 9888ceb, and compared them after masking ids, UUIDs, timestamps, emails and the fixtures' random `_<8 hex>` suffixes. **8 of 9 are identical** (individual active/expired, the 3 license-teacher shapes, license admin active/expired, student 403). Only `individual_none` differs, as intended.

## (2) The tests pin the right things
- The key-set test is exact: `set(keys) == {"status", "message"}`, plus both values.
- My mutants (each restore sha-verified):
  - M1: a field (`"id": None`) creeps back into NONE. KILLED by the key-set test and `test_individual_none`.
  - M2: an individual's lapsed subscription falls through to NONE. KILLED by 6 tests, including `test_lapsed_individual_subscription_reports_status_expired`.
  - M3: a license admin's lapsed license falls through to NONE. KILLED by 7 tests.
  - M4: the parent-license-lapsed allocation shape narrowed back to `is_active=False`, so that teacher falls to NONE. KILLED by 3 tests.
  So lapsed history of every kind gives EXPIRED, not NONE.

## (3) The samples are real
I regenerated all 9 via `SUBSCRIPTION_ME_SAMPLES_DIR` in my own detached checkout of 9888ceb. After the same masking, **all 9 match the committed `payloads/`**.

## Tests
`billing.tests.test_subscription_me_status` + `test_subscription_me_payload_samples`: 31/31 OK.

## Full suite?
Not needed for correctness, in my view. The change swaps the dict on one branch of one view, with a single call site. The other 8 payloads are proven identical on rendered bytes, and no other test module in the repo references `/subscription/me` (only these two files do), so nothing else can be reading the removed NONE fields. Running it anyway would be cheap insurance, and it's the SM's call. The real consumer risk is the frontend reading the removed NONE fields, which is the founder's decision and outside this verdict.

Verdict: VERIFIED.
Verified by Verification Engineer (grade-automator-plus-1a), 2026-09-28.
