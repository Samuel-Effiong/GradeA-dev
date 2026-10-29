# Smoke test of batch commit a7d667c (running app)

Date: 2026-09-21. Run by worker-0a for the Senior Manager. This is a smoke test
of the running app, not a gate: it does not replace the full-suite run on the
same commit, and it is LOCAL-REAL only (nothing deployed was touched).

**Verdict: PASS on all four areas asked for** (login, my-students,
upload/extract an assignment, the AI-refusal path). 24 checks were made. One of
my own checks was worded too strictly on the first run and is corrected below,
with the original result kept. Nothing in the app failed.

## What was tested

| Item | Commit / tree |
|---|---|
| Code under test | `a7d667ca637a96e181d95255a632c207d6aad65e` ("Reconcile H-19 and H-24 tests: an empty wallet raises EmptyWalletError") |
| Where | own detached worktree `GAP-smoke-0a-a7d667c` (a copy, not the batch worktree); tracked files unmodified |
| App | `gunicorn AutoGrader.wsgi` (same server as the Dockerfile, 2 workers x 2 threads, gthread) on `127.0.0.1:18765`, plus a real Celery worker (concurrency 2) |
| Database | new database `gap_smoke_0a_a7d667c` (created by me, migrated from zero: all migrations applied, 14.6 s, no errors) |
| Redis | DB 5 only (was empty; broker, cache and result store all on 5) |
| Not touched | Redis DB 0, DB 15 (the gate worker's), DB 7 (another session had keys there), the `AutoGrader` database, any deployed database |

## Guardrails: nothing leaves the machine, nothing costs money

The project `.env` holds real keys, so I ran with my own copy of it and a
settings shim (`settings_smoke.py.txt`, kept beside this file):

- **AI provider**: the OpenRouter key in my copy was replaced with a dummy, and
  the shim re-routes every OpenAI-SDK client to a local fake provider
  (`fake_provider.py.txt`, port 18766) that returns a canned assignment and logs
  every request. So the real extraction pipeline ran, but the "AI" answer was
  canned. Real calls are opt-in and none were made.
- **File storage**: local disk instead of Cloudinary.
- **Email**: MailerSend key replaced with a dummy, MailerLite key blanked; the
  seed script also patched the two enrolment emails. Stripe was not used (no
  billing flow was exercised).
- **tiktoken**: it cannot download `cl100k_base` in this sandbox (SSL EOF, the
  same failure the gate run saw), so the shim swaps in an approximate token
  counter. This only changes the size of the credit estimate. It is the reason
  the cost shown per extraction is 1,500 credits (what the fake provider
  reported), not a real figure.

## Seed data (`seed.py.txt`)

Three teachers: **A** (funded: free-trial plan, 5,000,000 raw credits), **B**
(funded), **C** (no plan, no credits). A student shared by A, B and C, and a
student only in B's course; A, B and C each have one course; C has one
published assignment. Everything created through the app's own services
(`activate_free_trial`, `enroll_student_by_email`).

## Results (run 2; `drive.py.txt`, raw results in `drive_results_run2.json`)

| # | Check | Result | Detail |
|---|---|---|---|
| L0 | health endpoint | PASS | `/api/v1/health` 200, database ok, cache ok |
| L1 | teacher login | PASS | 200, `user_type=TEACHER`, access and refresh tokens |
| L2 | teacher C login | PASS | 200 |
| L3 | student login | PASS | 200, `user_type=STUDENT` |
| L4 | wrong password | PASS | 401 with a plain message, not a 500 |
| L5 | no token on a protected route | PASS | 401 |
| L6 | token works | PASS | `users/me` returns the caller |
| M1 | my-students, teacher A | PASS | sees only the shared student; B's private student absent |
| M2 | nothing of B's course leaks through the shared student | PASS | whole payload searched for B's course name, B's notes and B's surname (the H-22 leak) |
| M3 | filter oracle | PASS | filtering by B's course id gives the identical response to a made-up id |
| M4 | my-students, teacher B | PASS | sees both own students, nothing of A or C |
| M5 | student calls the teacher-only endpoint | PASS | 403 |
| U1 | upload accepted | PASS | 202 with a task id |
| U2 | extraction completes in the worker | PASS | task `COMPLETED`, "Assignment uploaded successfully" |
| U3 | assignment saved | PASS | "Smoke Quiz: Fractions" appears in teacher A's course list |
| U4 | AI call went only to the fake | PASS | 1 request recorded by the fake provider |
| U5 | same file uploaded again | PASS | 202, but no second AI call (dedupe) |
| R1 | no-credit teacher uploads | PASS | 402 with `insufficient_credits`, not a 400 or 500 |
| R2 | refusal body | PASS | plain text, no HTML markup |
| R3 | refusal cost | PASS | no AI call made |
| R4 | feature not on the plan (generate-assignment) | PASS | 403 "AI access denied...", no AI call |
| R5 | student on an out-of-credit teacher's assignment | PASS | 402, not a 500 |
| R6 | student-facing message hides billing state | **FAIL as first written** | see the note below; replaced by R6b |
| R7 | still no AI call after the student refusal | PASS | provider request count unchanged |
| R6b | corrected R6 (`r6b_recheck_output.txt`) | PASS | see below |

**R6 note.** My check searched for the words "credit" and "wallet" and found
them. Those words are part of the message the H-24 design intends for everyone:
"There aren't enough AI credits available for this. The credit wallet needs to
be topped up before it can run." (`docs/evidence/REFUSAL_HANDLING_EVIDENCE.md`,
"one generic, role-neutral message"). D12 is about not telling a student their
teacher's specific billing state (trial expired, balance, refund, chargeback).
R6b checks that instead: the student's message is **identical** to the
teacher's, and contains none of trial / expired / subscription / balance /
refill / chargeback / refund / dispute / plan / any amount. Both passed. The
original R6 failure was a wrong assertion by me, not an app defect. The real
reason ("Credit wallet is empty") is logged server-side at WARNING, as the
design says (`web.log.txt`).

## Credits: the ledger agrees with what happened (`ledger_check_output.txt`)

Teacher A ends with **4,998,500** raw credits = 5,000,000 granted - 1,500 for
the one successful extraction. Teacher C has 0 and no usage rows: refusals cost
nothing.

## Run 1 (kept for honesty)

Run 1 did not finish cleanly, for two reasons that were mine:

1. My fake provider first returned an assignment in the wrong shape (a type
   `QUIZ`, a question type `SHORT_ANSWER`, a text rubric, a fractional
   confidence). The app **rejected it at validation, failed the upload and
   refunded the charge**: the ledger shows `CONSUME -1500` then `REFUND +1500`
   twice (two failed attempts), and the file's claim was released so the retry
   worked. That is a useful extra: the failure-and-refund path works on the
   running app. I then fixed the fake.
2. The driver logged in too often and hit the app's own login throttle (429
   "Expected available in 3 seconds"). I made the driver wait and reuse tokens,
   and cleared throttle counters by flushing **only Redis DB 5**.

## Log check

`web.log.txt`: 0 responses with status 500. Seven "API Exception" tracebacks,
all expected (2 wrong-password 401s, 2 no-token 401s, 2 student-on-teacher-route
403s, 1 login throttle 429). `worker.log.txt`: the only ERROR lines are the two
failures from run 1 above.

## Not covered (be careful what you claim from this)

- The AI answer was canned, so extraction *quality* and the real provider
  response shape were not tested; the image path and the long-document chunked
  path were not exercised. Grading was not run.
- Stripe, Google sign-in, real email, Cloudinary and Celery Beat were not used.
- One gunicorn config (2 x 2), not production's (9 x 4); local Postgres is 18.6
  while CI and production use 16.
- One pass by one tester. This is not Gate 8 DEPLOYED-REAL.

## Observations (not defects in this batch)

- Routine 401, 403 and 429 answers are logged with a full traceback under
  "API Exception". That is noisy, not wrong.
- The health check is at `/api/v1/health`; `/health` on its own is a 404.
- tiktoken's download failing in this sandbox is worth vendoring the encoding
  file for (already on the worker's step 0 list).

## Reproduce

```bash
git worktree add --detach ../GAP-smoke-0a-a7d667c a7d667c
cp .env <worktree>/.env   # then: DB name -> gap_smoke_0a_a7d667c, Redis -> /5,
                          # OpenRouter key -> dummy, MailerSend dummy, MailerLite blank
export PYTHONPATH=<dir with settings_smoke.py> DJANGO_SETTINGS_MODULE=settings_smoke \
       SMOKE_MEDIA_ROOT=<dir> SMOKE_FAKE_PROVIDER_URL=http://127.0.0.1:18766/v1
python manage.py migrate && python manage.py shell < seed.py
python fake_provider.py 18766 requests.jsonl &
gunicorn AutoGrader.wsgi:application --bind 127.0.0.1:18765 --workers 2 --threads 2 --worker-class gthread &
celery -A AutoGrader worker -l info --concurrency 2 &
python drive.py && python recheck_r6.py
```

The shim refuses to start unless the database is `gap_smoke_0a_a7d667c` and the
Redis DB is 5.

## Files in this folder

`drive.py.txt`, `recheck_r6.py.txt`, `seed.py.txt`, `ledger_check.py.txt`,
`fake_provider.py.txt`, `settings_smoke.py.txt` (the scripts, `.txt` so they are
not picked up as code), `drive_results_run2.json`, `r6b_recheck_output.txt`,
`ledger_check_output.txt`, `fake_provider_requests.jsonl`, `web.log.txt`,
`worker.log.txt`. The logs were scanned for key and token patterns before being
saved: none found.
