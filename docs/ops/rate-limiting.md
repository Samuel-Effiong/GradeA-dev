# Rate limiting: NUM_PROXIES and client identity

**Status:** `NUM_PROXIES` is set in code (`REST_FRAMEWORK["NUM_PROXIES"]`,
default `1`, env-overridable). **The deployed value still needs verifying per
environment — see "Verify after deploy".**

## The bug this fixes

Every throttle on the unauthenticated auth surface (`users/throttling.py`)
inherits `AnonRateThrottle`, which keys its bucket on
`BaseThrottle.get_ident()`. With `NUM_PROXIES` unset, DRF falls through to:

```python
return ''.join(xff.split()) if xff else remote_addr
```

The key becomes the **entire `X-Forwarded-For` chain**. Railway's edge
*appends* to that header rather than replacing it, so a caller controls its
left-hand portion. Every fake value invents a fresh bucket, and the limit
resets at will.

This was measured, not inferred. Against the live **beta** service, before the
fix:

| Probe | Limit | Result |
| --- | --- | --- |
| 14 rapid `POST /api/v1/auth/login` | 10/min | `401` x14, never `429` |
| 7 x `POST /api/v1/auth/otp` (RESET_PASSWORD) | 5/hour | `202` x7 |

So the limits were not merely bypassable, they were not working at all.

It matters most for `otp`. `users/throttling.py` records that
`PasswordResetOTP.generate_code()` **resets the failed-attempt counter**, so
unlimited code requests re-open the brute-force path that the per-account
lockout exists to close. The same applied to the 6-digit student
`activation_token`, which `register/student` and `renew-student-token` look up
**globally** (`filter(activation_token=token, is_active=False)`) rather than
per account — a 10^6 keyspace, guessable in minutes with unlimited attempts.

## Why 1, and why it is env-overridable

`NUM_PROXIES` is the number of reverse proxies between the client and Django.
Set to an integer, DRF reads `addrs[-min(num_proxies, len(addrs))]` — counting
from the **right**, where the edge writes the address it actually observed and
which the client cannot forge.

`1` = a single Railway edge hop. The correct value is a property of the
deployment, not the code, hence `env.int("NUM_PROXIES", default=1)`.

**Too high is its own bug.** It reads an internal proxy's address, which is
identical for every caller, collapsing everyone into one shared bucket — one
user's traffic would then throttle everybody. `users/tests_throttle_client_identity.py`
covers both directions: one test proves a forged header cannot reset a bucket,
another proves two distinct clients keep separate budgets.

## X-Forwarded-For, correctly

A proxy appends the address of the peer it **received from**, not its own. So:

```
X-Forwarded-For: <anything the caller sent>, <true client>
REMOTE_ADDR:     <the edge itself>
```

The true client is the **rightmost** entry; the edge's own address never
appears in the header at all. Getting this backwards is easy — the test suite
originally modelled it the wrong way round and the "distinct clients" guard
caught it.

## Verify after deploy

The hop count was inferred, not observed — nothing in the app records or echoes
the header, so it could not be read directly from a live request. Confirm the
deployed value behaviourally, against **beta first**:

```sh
URL=https://grade-automator-beta-production.up.railway.app/api/v1/auth/login
BODY='{"email":"throttle-probe-nonexistent@example.invalid","password":"x"}'
for i in $(seq 1 14); do
  curl -s -o /dev/null -w "%{http_code} " -X POST "$URL" \
    -H "Content-Type: application/json" -d "$BODY"
done; echo
```

Expect `401` up to the limit, then `429`. Use a **nonexistent** email so no real
account's `failed_login_attempts` is incremented.

Then confirm a forged header does **not** reset it:

```sh
curl -s -o /dev/null -w "%{http_code}\n" -X POST "$URL" \
  -H "Content-Type: application/json" \
  -H "X-Forwarded-For: 203.0.113.99" -d "$BODY"
```

Expect `429`. A `401` means the deployed `NUM_PROXIES` is still wrong for that
environment — raise it by one and retry.

If instead **every** caller starts getting `429` in normal traffic, the value is
too high: it is reading a shared internal address. Lower it.

## Scope

This fixes *which client* a limit counts against. It does not change any rate,
and it does not address the separate question of whether the three `register`
endpoints and `renew-student-token` should keep sharing one bucket — that
pooling is deliberate today (see the comment at `classrooms/views.py`,
`handle_expired_token`) because the renewal endpoint is both a token-guessing
oracle and a free outbound-mail trigger.
