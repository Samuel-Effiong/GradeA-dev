# H-256 notes (Hardening Engineer, 4d)

- **The row** (batch 29 row A; the user: "give a reasonable timeout"): the two outside calls with no timeout of
  their own get one, and Stripe's retries are set by us.
- **Base:** beta `31a86456`. Branch `task/h256-outside-call-timeouts`: tests `74cd81cc`, code and docs `c9f19d2b`.

## What happened before

- **Google** (`users/views.py` `_google_auth`):
  - `http_requests.post(token_url, data=payload)` had no `timeout`. A Google that never answered held a gunicorn
    thread until the operating system gave up.
  - A failed exchange was logged as `logger.error("Google OAuth token exchange failed", exc_info=e)`: the full
    traceback and the error's text. requests writes the host, the port and the timeout into its messages.
  - The answer was already neutral: `describe_user_error` passes no requests error through, so the user saw
    "Google sign-in failed. Please try again." (400).
- **The AI provider** (`ai_processor/services.py` `AIProcessor.__init__`): `OpenAI(base_url=..., api_key=...)`, so
  the SDK's defaults (openai 1.107.1 `_constants.py`) applied: `httpx.Timeout(600, connect=5.0)` and
  `DEFAULT_MAX_RETRIES = 2`.
  - One request that never answered cost up to 3 x 600 s = 30 minutes.
  - That is past the grading task's own kill point: `GRADING_TASK_TIME_LIMIT_SECONDS = 25 * 60`, with the soft
    limit 60 s before it.
- **Stripe** (`billing/imports.py`): only `stripe.api_key` was set.
  - stripe 14.4.1's module default is `max_network_retries: int = 2` (`stripe/__init__.py:38`).
  - Its default client is `RequestsClient(timeout=80)` (`stripe/_http_client.py:584`).

## What changes

- **Google:** `timeout=GOOGLE_TOKEN_EXCHANGE_TIMEOUT`, which is `(5, 10)` (connect, read).
  - Every `RequestException` of the exchange is logged as one ERROR line, "Google OAuth token exchange failed:
    error=<Class> frames=<...>" (`describe_error_for_log`, H-218's helper). There is no traceback or text.
  - The SM named Timeout and ConnectionError. The HTTPError from `raise_for_status()` goes through the same line,
    because its message carries the URL too.
  - The answer, its status and the audit reason (GOOGLE_EXCHANGE_FAILED) are unchanged.
- **AI provider:** `timeout=httpx.Timeout(settings.AI_PROVIDER_TIMEOUT_SECONDS, connect=10.0)` and `max_retries=2`.
  - New setting `AI_PROVIDER_TIMEOUT_SECONDS = env.float(..., default=300.0)`, read when a process starts. The
    client is built in `__init__`, and the module's singleton is built at import.
  - Worst case for one request that never answers: 3 x 300 s = 15 minutes, inside the grading task's 25.
- **Stripe:** `stripe.max_network_retries = 2` beside the key. The timeout is left as the SDK's 80 s.
- **Docs:** the backlog row and `docs/backend/ai-processor.md` ("How long a request may wait").

## The measurement (the number 300)

- **Source:** the live benchmark records in the repo, `ai_processor/benchmark/answers/reports/*.json`, last
  changed 2026-09-14.
  - Each call carries `seconds`. `measure_benchmark_durations.py` reads them; its output is
    `measure_benchmark_durations.out.txt`.

  | Kind | Calls | p50 | p95 | p99 | max |
  |---|---|---|---|---|---|
  | answer reading (grok-4.3) | 140 | 14.6 s | 36.8 s | 94.2 s | 126.8 s |
  | grading (deepseek-v4-pro) | 12 | 26.8 s | 94.1 s | 113.3 s | 118.1 s |

- **One number covers both.** 300 s is about 2.4 times the slowest call of either kind. The SM's guess of 120 s
  for grading would have cut off the slowest grading call (118 s) with 2 s to spare. Grading has 12 calls, so its
  p99 is in effect its maximum.
- **Not read: the per-call records** (`AICallRecord.duration_ms`, H-201).
  - The local database has no `ai_processor_aicallrecord` table.
  - The beta database login in `.env` (`DATABASE_URI`) is refused ("password authentication failed", 2026-10-27).
  - The read-only query is `call_record_durations.sql`, for whoever holds a working login. **SM ruling
    2026-10-27:** do not chase the login; the benchmark records are the measurement of record.
- **Caveats:**
  - The records predate H-204's price sort, which tries the cheapest supplier first even when it is slower. Real
    calls since then may be slower, which is one more reason for the margin.
  - httpx's read timeout is per read, not per call: a reply that keeps sending bytes is not cut off. Whether
    OpenRouter pads non-streaming replies was not checked.

## Not changed (named for the SM)

- **The certificate fetch inside `id_token.verify_oauth2_token`** (`google_requests.Request()`).
  - google-auth 2.53.0's own default is `_DEFAULT_TIMEOUT = 120` (`google/auth/transport/requests.py:47`).
    `fetch_certs` calls `request(certs_url, method="GET")` without a timeout, so it gets the 120 s.
  - It is not a `requests.*` call of ours, and it has a limit. **SM ruling 2026-10-27:** leave it; it is a known
    limit in the row, with no follow-up row.
- **No other `requests.*` call in `users/views.py`.**
  - `users/mailerlite_service.py` already passes `timeout=REQUEST_TIMEOUT_SECONDS`.
- **Stripe's text and traceback in billing logs** are H-255's subject (Security Engineer), not this row's.

## Tests (three modules, 10 tests)

- `users.tests_h256_google_exchange_timeout` (G1-G3):
  - **G1** the post's `timeout` kwarg is `(5, 10)`.
  - **G2** (control) a ReadTimeout, ConnectTimeout or ConnectionError answers 400 with the neutral refusal and no
    provider text.
  - **G3** a ReadTimeout or ConnectionError gives one ERROR record with no `exc_info`, "error=<Class>" in it and
    no provider text.
- `ai_processor.tests_h256_provider_timeout` (P1-P3, R1, the shape control):
  - **How the stand-in works:** it replaces only `client._client.send` (the httpx layer). The SDK's own retry,
    timeout and error mapping run for real, and its retry sleep is patched to nothing.
  - **P1** every try's `request.extensions["timeout"]` is `{connect 10, read/write/pool 300}`.
  - **P2** `override_settings(AI_PROVIDER_TIMEOUT_SECONDS=42)` reaches the wire.
  - **P3** (control) `client.max_retries == 2`, and three requests are sent.
  - **R1** (control) a real `grade_student_submission`: the first batch is answered and charged 1000 credits, and
    every request after it raises httpx.ReadTimeout.
    - The raised chain holds `openai.APITimeoutError`. There is exactly one charge (the timed-out calls are never
      charged), it is refunded, and the balance is back to the start.
  - **Shape control:** the stand-in's reply parses as a chat completion.
- `billing.tests.test_h256_stripe_network_retries` (S1, S2):
  - **S1** with the SDK's module value patched to 0, reloading `billing.imports` gives 2.
  - **S2** (control) `RequestsClient()._timeout == 80`.

## Expected reds against 31a86456

The red run takes the four production files back to the base and runs the three modules as at the tip.

- **How the fragments were set:** they come from a first run on the base (tests only, before any code;
  `GAP-4d-runs/b29/h256_first_red.txt`, outside the repo), checked block by block with `reason_check.py`. The
  gate's red run checks them again.

```
6 FAIL blocks, no ERROR, "Ran 10 tests"
  G1 test_the_exchange_waits_5_s_to_connect_and_10_s_for_the_answer  "None != (5, 10)"
  G3 test_a_timeout_is_logged_by_its_class_only (2 blocks)          ") is not None"
  P1 test_every_try_carries_the_timeout      "{'connect': 5.0, 'read': 600, 'write': 600, 'pool': 600} != {'connect': 10.0, 'read': 300.0"
  P2 test_the_read_timeout_follows_the_setting "... != {'connect': 10.0, 'read': 42.0"
  S1 test_the_retries_are_ours_not_the_sdks_default                 "0 != 2"
  GREEN (controls): G2, P3, R1, the shape control, S2
```

## Mutants (14; expected sets and fragments written before any mutant ran)

The battery is the three H-256 modules. A mutant is KILLED only if its failing set is EXACTLY the written one and
every block of each expected test holds that test's fragment.

| Id | Break | Expected to fail | Fragment |
|---|---|---|---|
| M1 | the Google post's timeout removed | G1 | `None != (5, 10)` |
| M2 | `GOOGLE_TOKEN_EXCHANGE_TIMEOUT = None` | G1 | `None != (5, 10)` |
| M3 | the failure logged with `exc_info=e` again | G3 (2 blocks) | `) is not None` |
| M4 | the failure logged by its text (`%s`, e) | G3 (2 blocks) | `not found in 'Google OAuth token exchange failed: HTTPSConnectionPool` |
| M5 | the AI client's timeout removed (the SDK's 600 s) | P1, P2 | `{'connect': 5.0, 'read': 600` |
| M6 | `timeout=None` (no limit) | P1, P2 | `{'connect': None, 'read': None` |
| M7 | `timeout=0` | P1, P2 | `{'connect': 0, 'read': 0` |
| M8 | the 10 s connect wait dropped | P1, P2 | `{'connect': 300.0, 'read': 300.0` / `{'connect': 42.0, 'read': 42.0` |
| M9 | the setting ignored (a fixed 300 s) | P2 | `{'connect': 10.0, 'read': 300.0` |
| M10 | the setting's default 30 s | P1 | `{'connect': 10.0, 'read': 30.0` |
| M11 | `max_retries=0` | P3 | `0 != 2` |
| M12 | the refund skipped when the chain holds a Timeout (`billing/refunds.py`) | R1 | `False is not true` |
| M13 | `stripe.max_network_retries = 0` | S1 | `0 != 2` |
| M14 | the Stripe line removed | S1 | `0 != 2` |

- **Equivalent, not a mutant:** deleting `max_retries=2` (the SDK's default is 2) and, in stripe 14.4.1, deleting
  the Stripe line *without* the patch. S1 patches the SDK value to 0, so M14 is not equivalent under it.
- **M11:** P1 and P2 still pass (one request, the right timeout). R1 still refunds: the pipeline's own retries
  send more than one request.
- **M12:** R1's charge count and amount pass first, then `is_refunded` fails.

## The seam

`seam_check.py` parses the base and tip of the four production files. It takes the H-256 changes out of the tip's
tree, each found exactly once and with exactly its written value, and requires the rest to equal the base.

Checked before the pack by parsing only:
- **The tip** passes.
- **The base against itself** stops: the additions are not found.
- **A copy with `max_retries=3`** stops: "the client's max_retries is 3".
- **A copy with the refusal text changed** stops: "something else changed".

## Gate

- **Script:** `run_h256_gate.sh.txt`, the H-221 gate's form.
- **Order:**
  1. the red run (6 blocks);
  2. makemigrations;
  3. the changed-file list (exactly the four production files beside the tests and `docs/`), then the seam;
  4. own, near and guards;
  5. the 14 mutants.
- **Own + near + guards:**
  - own: the three modules;
  - users: `tests_google_auth`, `tests_admin_power_principle`, `tests_auth_audit_doors`, `tests_last_login_stamp`,
    `tests_open_signup`, `tests_rate_limited_envelope`, `tests_remaining_branches`,
    `tests_student_cannot_name_themselves`;
  - ai_processor: `tests_provider_block`, `tests_h204_provider_price_sort`, `tests_grading_pipeline`,
    `tests_grading_completeness`, `tests_reading_pipeline`, `tests_answer_extraction_gate`,
    `tests_dashboard_custom_ai_prompt`, `tests_answer_chunk_merge`, `tests_call_record`,
    `tests_call_record_static`, `tests_second_opinion_error_code`, `tests_second_opinion_pipeline`;
  - `students.tests_s6d_provider_failure`;
  - billing: `test_execute_graded_task`, `test_fake_stripe_harness`, `test_h28_stripe_budget`,
    `test_refusal_handling_gates`, `test_overage_lock_across_network`;
  - the 21 guards.
- **A development run of own + near (not the gate)**, at `c9f19d2b`'s code before commit: "Ran 562 tests in
  48.447s", "OK (skipped=3)". No provider host appeared in its log.
