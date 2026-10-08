# H-48: Verification (a6a2088, test-only): VERIFIED

- The thin subclass patches `stripe.Event.retrieve` in setUp for the whole class (started, addCleanup stop). The parent `RealSignatureVerificationTests` has no setUp, so not calling super() loses nothing. The inner re-patch in `test_retrieve_is_never_reached_when_the_signature_is_bad` shadows the class mock, and its `assert_not_called()` still proves verification gates the outbound call.
- Exact 200 is right for both endpoints. Both views go through `_record_and_dispatch`, whose every success branch (already-succeeded, unhandled type, queued) returns `HttpResponse(status=200)`; the non-success statuses are 409 in-flight and 500 on enqueue failure. `invoice.payment_succeeded` takes the unhandled-type 200, so these tests prove verification, not dispatch, which is their purpose.
- Re-ran in a throwaway checkout of a6a2088 with the H-39 guard active (the runner wraps `block_real_network_calls`): **27/27, zero "Blocked real outbound network call" lines**, so no inherited thin test reaches live Stripe.
- My mutants (each restore sha-verified):
  - M1, the class patch never started: exactly 3 accepted-path tests fail, and 18 blocked-network lines are logged. It's the patch that keeps them offline, and without it they now fail instead of passing on a 500.
  - M2, every dispatcher `return HttpResponse(status=200)` changed to 202: 6 failures (3 tests × fat and thin). **The same mutant against the ORIGINAL `!= 400` file passes 27/27**, so the strictening is what catches it.

Verdict: VERIFIED. Verified by Verification Engineer (grade-automator-plus-1a), 2026-09-29.
