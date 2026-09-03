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

## Measuring the proxy hop count (instead of guessing it)

`NUM_PROXIES` cannot be derived by probing. If it is wrong, the throttle
key varies per request, no limit is ever reached, and from outside that is
indistinguishable from "the limit is high", "the code isn't deployed", or
"CI blocked the deploy". All three of those actually happened while
chasing this, and each guess costs a full CI + deploy cycle.

So measure it. On the service in question:

1. Set `EXPOSE_CLIENT_DIAGNOSTICS=true` and let it redeploy.
2. Read the value the throttles actually bucket on:

   ```sh
   curl -s https://<host>/api/v1/health/client | jq .data
   ```

   ```json
   {
     "resolved_ident": "…",
     "num_proxies": 1,
     "x_forwarded_for": "<client>, <hop1>",
     "remote_addr": "<last hop>",
     "version": "…"
   }
   ```

3. **`NUM_PROXIES` is the position of the real client counting from the
   RIGHT of `x_forwarded_for`, starting at 1.** DRF reads
   `addrs[-min(num_proxies, len(addrs))]`. So if your address is the
   rightmost entry, it is 1; second from the right, 2; and so on.
   Compare against a known-good source (`curl -s ifconfig.me`) run from
   the same machine.
4. Confirm `resolved_ident` equals your real address, and that it is
   **the same across two consecutive calls**. A value that changes
   between requests is the actual failure mode - no limit can ever be
   reached.
5. Set `NUM_PROXIES`, set `EXPOSE_CLIENT_DIAGNOSTICS=false` again, and
   re-run the probes above.

If `x_forwarded_for` is absent entirely, `NUM_PROXIES` cannot help:
DRF falls back to `REMOTE_ADDR`, and if the platform's internal mesh
varies that per request, throttling needs a custom throttle class
reading a trusted header (`x_real_ip`, `cf_connecting_ip`, or
`x_envoy_external_address` - all four are echoed by the endpoint for
exactly this reason).


## MEASURED: what Railway actually sends (2026-09-03, beta)

The section above was written from an assumed proxy model that turned
out to be WRONG. Corrected here from a real reading taken via
`/api/v1/health/client`:

```
x_forwarded_for: "129.222.206.195, 152.233.29.4"
                  ^ true client    ^ edge instance
x_real_ip:       "129.222.206.195"
remote_addr:     "100.64.0.3"        (internal mesh, never in the chain)
```

**1. The edge address rotates per request.** Five consecutive calls
reported `152.233.29.4`, `46.151.193.242`, `46.151.193.242`,
`46.151.193.241`, `46.151.193.241`.

**2. The true client is SECOND FROM THE RIGHT, so `NUM_PROXIES = 2`.**
With `1`, `get_ident()` returns the rotating edge address, every request
lands in a fresh bucket, and no limit is ever reached. That was the live
bug - measured on beta as 14 rapid logins against a 10/min cap returning
401 fourteen times, and 8 OTP requests against a 5/hour cap returning
202 eight times.

**3. A client-supplied `X-Forwarded-For` is STRIPPED, not appended to.**
Sending `X-Forwarded-For: 203.0.113.99` produced a chain with no trace
of it. So header forgery was never the threat on this platform; the
earlier "the caller owns the left-hand portion and can invent buckets"
reasoning was mistaken. The rotating edge was the whole problem.

**4. Over-setting degrades gracefully, for now.** DRF clamps with
`addrs[-min(num_proxies, len(addrs))]`, so against a two-entry chain any
value >= 2 resolves to the same client entry. If a CDN is ever put in
front of Railway the chain lengthens and this stops being true - re-take
the reading if the topology changes.

`x_real_ip` carries the true client as a single unambiguous value and is
the more robust key if `NUM_PROXIES` ever proves brittle; it would need a
custom throttle subclass overriding `get_ident()`.
