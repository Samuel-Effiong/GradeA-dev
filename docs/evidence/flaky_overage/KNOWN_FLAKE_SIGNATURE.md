# Pre-registered signature: the known overage concurrency flake

This is written **before** tonight's batch gate so that a red run is compared against a
written definition, not recognised by eye. It covers only beta's **unfixed** copy of the
test, which is what the gate runs until `task/fix-overage-concurrency-flake` lands.

**The rule:** a failure gets the known-flake path only if it matches **all** of S1–S4. If any
one does not match, it is **not** the known flake and needs a full investigation.

## Root cause the signature is derived from

A worker thread in `ConcurrentOverageDeliveryTests._run` is still inside a **live**
`stripe.PaymentIntent.retrieve` (`resolve_stripe_receipt_url`, called inside the grant's
transaction) when `join(timeout=60)` gives up. `_run` never checks `is_alive()`, so the
assertions read a wallet whose grant has been written but **not committed**. That wallet
reads as **zero** credits.

Reproduced deterministically on `b744c9f`:
`docs/evidence/flaky_overage/g1_repro_prefix_b744c9f.log` (lines 303–312).

## S1 — The test ID, exactly

```
billing.tests.test_overage_purchase_integrity.ConcurrentOverageDeliveryTests.test_concurrent_purchases_by_different_teachers_stay_separate
```

It must be this test and no other in the class or file. The other three tests in the class
share `_run` and could in principle fail in a similar way, but that has never been observed.
A failure in any of them does not match.

## S2 — The failure, exactly

- An **`AssertionError`** (a FAIL, not an ERROR) raised from the **grants** assertion. On
  beta `fba1294` that is `billing/tests/test_overage_purchase_integrity.py`, traceback
  frame **line 795**, whose message argument is at line 798.
- The message must match this regular expression, anchored at both ends:

  ```
  ^AssertionError: 0 != 500 : a teacher did not receive exactly their own block$
  ```

What this rules out, on purpose:

| Observed | Why it does NOT match |
|---|---|
| `1000 != 500`, or any value other than `0` | Credits were granted, just the wrong amount. That is a double grant or a cross-assignment, i.e. a real billing bug, not an uncommitted grant |
| `0 != 500` from the `errors` assertion (`threads raised: …`, line 793) | A worker raised. That is a real exception, not a slow one |
| An ERROR (any exception other than `AssertionError`) | Not this mechanism |
| The same message from a test other than S1 | Fails S1 |

## S3 — The timing marker (ties it to a thread outliving the 60 s join)

The mechanism needs a worker to outlive `join(timeout=60)`. So **the test's own runtime is
at least 60 s**. The reproduction shows `Ran 1 test in 61.660s` (log line 312).

- The gate should record per-test durations (`manage.py test --durations 0` is supported on
  this project). S3 matches only if this test's recorded duration is **≥ 60.0 s**.
- **A matching message in a test that ran under 60 s does NOT match.** No thread could have
  outlived the join, so the zero came from something else.
- If durations were not captured for the failing run, S3 is **unmet**, not assumed. Treat
  the failure as unmatched.

## S4 — The discriminator (added by the Senior Manager)

**One clean isolated rerun of the S1 test, on a fresh database, must PASS.** The known flake
depends on live Stripe latency, so it passes in isolation almost always. If the isolated rerun
also fails, it is **not** the known flake. The gate still records the original run as FAIL
either way; the rerun never flips the verdict.

## What matching the signature does and does not mean

- It **does** mean the failure has the fingerprint of an uncommitted grant read after a timed-out
  join, caused by live Stripe latency.
- It **does not** mean the run passed. The gate scores it FAIL.
- It **does not** prove the absence of a real defect in the same run. A real
  cross-assignment bug that happened to zero a wallet *and* take over 60 s *and* pass in
  isolation would match. That combination is why S2 pins the value `0` and S3 pins the time,
  but the residual risk is real and is removed only when the fix lands. The fixed version of the test fails with a named
  thread (`… still running after 60s: ['overage-worker-N'] …`) instead of reading partial state,
  so on the fixed branch this signature can no longer occur.
