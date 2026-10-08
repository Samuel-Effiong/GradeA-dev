# H-48 — thin-webhook signature tests: no live Stripe calls, exact success status

Branch `task/h48-thin-webhook-mock`, off beta `197aa46`. Author: Integration & Release Engineer (grade-automator-plus-0b). Test-only change.

## Finding

First real CI run of the H-39 network guard (beta `be78221`, [Tests #120](https://github.com/Enhanced-Electronics/Grade-Automator-Plus/actions/runs/36444904862)) logged two `BlockedNetworkCallError` to `api.stripe.com:443` from `billing/webhooks.py` `thin_webhook` → `stripe.Event.retrieve("evt_signature_check_1")`. The suite stayed green.

Cause: `ThinWebhookRealSignatureTests` inherits the base class's accepted-path tests. Only `test_a_correctly_signed_payload_passes_verification` was overridden to patch `retrieve`. The two others, `test_a_rolled_secret_still_verifies_while_both_are_live` and `test_a_signature_just_inside_the_tolerance_is_accepted`, reached the real API, got a 500, and asserted only `!= 400`.

## Change (`billing/tests/test_webhook_signature_verification.py` only)

1. `ThinWebhookRealSignatureTests.setUp` patches `stripe.Event.retrieve` for the whole class, so every inherited accepted-path test is covered. The explicit override now just asserts `retrieve` was called once with the event id. `test_retrieve_is_never_reached_when_the_signature_is_bad` keeps its own inner patch and still proves verification gates the call.
2. All three accepted-path assertions (fat and thin) are now `assertEqual(status, 200)`, the dispatcher's success status, instead of `assertNotEqual(status, 400)`. A 500 can no longer pass as "accepted".

## Results

| Run | Result |
|---|---|
| Fixed file | 27/27 OK, no blocked-network log lines |
| M1: class patch never started (retrieve live) | **3 FAIL** `500 != 200` (correctly-signed, rolled-secret, inside-tolerance on the thin endpoint) |
| M2: original file from `197aa46` | 27/27 OK **while** the guard logs blocked Stripe calls, reproducing the silent pass |

No production code changed.
