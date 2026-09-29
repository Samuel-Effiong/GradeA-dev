# Batch-2 deployed check (Gate 8) on staging

Batch-2 goes **staging first**: after its two strict overnight runs pass, the
founder deploys batch-2 (plus phase2/epic-a) to staging, and these checks run
there before the beta package goes. The founder may run them via Railway.

- **Generic part** (deploy SHA check, migrations applied, health, Sentry
  quiet): the Integration & Release Engineer.
- **Sections 1–4** (H-1 stage 3, H-1 step 4, H-25): the Hardening Engineer
  (grade-automator-plus-d5).

Every result is recorded as **counts, statuses, ids and timestamps only**.
Never paste response bodies, email addresses or tokens into this file.

---

## G. Deployment sanity, before anything else (all read-only)

Run this first. If any row fails, stop: the rest of the check would be
testing the wrong build.

**G1. The right commit is running.**
- `GET https://<staging-host>/health` returns `{"status", "checks", "version"}`.
- `version` is the commit Railway deployed (`RAILWAY_GIT_COMMIT_SHA`).
- **PASS:** `version` equals the pushed origin/staging sha in the package, on
  every web replica. With several replicas, call it about 10 times and
  record every distinct `version` seen: there must be exactly one.
- `"unknown"` means the variable isn't set. Record the deploy id from the
  Railway dashboard instead.

**G2. Migrations are applied.**
- `railway run --service <web> -- python manage.py migrate --plan`
- **PASS:** "No planned migration operations."
- Also record `python manage.py showmigrations users audit` and confirm
  `users 0039_customuser_token_epoch` and `audit 0001_initial` are `[X]`.
- Do **not** run `migrate` from here. If anything is pending, the deploy's
  release step didn't run. Stop and tell the SM.

**G3. Services are healthy.**
- `/health` returns HTTP 200 with every entry in `checks` ok.
- A 503 names the failing service. Stop.
- `GET /health/beat` returns 200. It checks Celery Beat separately from the
  web deploy gate.
- For the worker: `railway run --service <worker> -- celery -A AutoGrader inspect ping`
  gets a `pong` from each worker. Record the count and compare it with the
  topology in §0.

**G4. Sentry is quiet across the deploy.**
- In Sentry, filter to the staging `environment`. Events carry the
  environment but no release tag, so use a time window: 30 minutes before
  the push to 30 minutes after the checks finish.
- **PASS:** no new issue in that window, and no spike in an existing one.
  Pay particular attention to `audit`, cache/Redis, `users` auth and the
  status-summary view.
- Record issue ids and counts only, never event bodies.

**Rollback target:** the previous origin/staging sha, given in the push
package. Rolling back code does not reverse migrations (all of them are
additive), and the generation counters in Redis stay in place (§4).

## 0. Setup (writes test data; see §4)

**URLs have no trailing slash.** The routers use `trailing_slash=False` and
`APPEND_SLASH = False`, so a trailing `/` returns 404. In R3 and §3 that would
look exactly like a PASS, so copy the paths exactly.

- **Accounts.** Use staging test accounts on an address the team controls
  (for example plus-addressing on the team inbox). Enrolment and removal send
  real emails (`send_bulk_enrollment_email`,
  `send_removed_from_course_email`), so never use a real person's address.
  - **T:** a teacher.
  - **S:** a student.
- **Data, created by T:**
  - **C:** a course, with S enrolled (`POST /api/v1/course/{C}/students`,
    body `{"email": ...}`);
  - **A:** a DRAFT assignment in C, with at least one question.
- **Tokens.** Log in as T and as S. Keep the JWTs in shell variables
  (`TOK_T`, `TOK_S`). Never echo them or write them to a file.
- **Redis access.** Run through Railway so the URL is never printed, for
  example `railway run --service <redis-or-web> -- sh -c 'redis-cli -u "$REDIS_URL" …'`.
  Use whichever variable the staging service actually sets
  (`REDIS_DEV_URL` / `REDIS_PROD_URL` by `ENVIRONMENT`).
  - The cache and the Celery broker and results share this one Redis.
  - Cache keys are namespaced `gaplus:1:…` (`KEY_PREFIX` + version 1).
- **Topology.** Note how many web replicas and Celery workers staging runs
  (Railway service settings), and record it here.
  - **Why it matters:** invalidation is a generation counter in that shared
    Redis, so every web replica and every worker sees the same counters.
  - **The only process-local cache in production code** is
    `assignments/prosemirror_converter.py`'s `lru_cache`. It is a pure
    content conversion, keyed by the input, and holds no per-user or tenancy
    data.
  - With one replica, §1 still proves the deployed wiring. With several,
    repeat each read enough times (10×) to cross replicas.

## 1. Cache invalidation on the deployment (H-1 stage 3 + step 4)

**Method.** For each row:
1. Warm the viewer's read: call it twice and record the status plus a
   sha256 of the body, not the body itself.
2. Perform the write and wait for its response.
3. Immediately read as the viewer 10 times.

**PASS** = every post-write read reflects the write, starting with the first.
**FAIL** = any read shows pre-write data. Record the row, the read number and
the timestamp, then stop.

| Row | Family (what changed in batch-2) | Write | Viewer read | Expected after the write |
|---|---|---|---|---|
| R1 | course (user- and course-scoped generations; stage 3 targeted bumps, wildcards gone in step 4) | T: `PATCH /api/v1/course/{C}` `{"name": "<new>"}` | S: `GET /api/v1/course` and `GET /api/v1/course/{C}`; T: `GET /api/v1/course` | the new name in all three |
| R2 | student status-summary (**newly versioned on `usr(student)` in step 4**) and the assignment families | T: `PATCH /api/v1/assignments/{A}` `{"status": "PUBLISHED"}` | S: `GET /api/v1/student-admin/dashboard/status-summary` and `…?course={C}` | A counted as open/pending for S in both |
| R3 | enrolment revocation (course detail and list for the removed student) | T: `DELETE /api/v1/course/{C}/student/{S_id}` | S: `GET /api/v1/course/{C}`, then `GET /api/v1/course` | detail **404** (not 200), and C absent from the list. Re-enrol S afterwards for §3 |
| R4 *(optional: costs AI credits, founder's call)* | **worker-originated** write: a Celery task bumps and a web-cached read refreshes | S submits answers to A. T: `POST /api/v1/submissions/{id}/grade-async`, then waits for the task to finish | T: the assignment's submission list; S: status-summary | graded state visible on the first read after the task finishes |

R4 is the only row whose write happens in a worker. Rows R1–R3 are web
writes. Because the counters live in the shared Redis, a web read after a
worker write goes through the same path, and R4 is the direct proof of that.

## 2. No wildcard or whole-cache operation on the deployment (step 4)

Run both, bracketing the §1 writes. The static guard
(`AutoGrader/tests_no_wildcard_invalidation.py`) already proves the deployed
SHA has no call site; this checks the running system.

**2a. Server command counters** (read-only):

```sh
redis-cli -u "$REDIS_URL" INFO commandstats | grep -E '^cmdstat_(scan|keys|flushdb|flushall):'   # before §1
# ... run §1 R1–R3 ...
redis-cli -u "$REDIS_URL" INFO commandstats | grep -E '^cmdstat_(scan|keys|flushdb|flushall):'   # after
```

Record the `calls=` deltas.

**PASS:**
- `keys`, `flushdb` and `flushall` rise by 0 (a missing line means 0 calls);
- `scan` rises by **no more than** the number of assignment saves or deletes
  in the window (R2 = 1). The one allowed SCAN is the PDF cache's
  exact-prefix clear. With `SCAN_ITERSIZE` 100,000 it is normally one call
  per clear on a staging-sized keyspace.

These counters are **server-wide**, so they also count other clients of the
same Redis (Celery, any admin tool). A rise above that bound is not yet a
failure: attribute it with 2b.

**2b. Attribution with MONITOR, filtered at the source** (read-only, a few
minutes at most):

```sh
redis-cli -u "$REDIS_URL" MONITOR | grep --line-buffered -iE '"(scan|keys|flushdb|flushall)"'
```

Run it in a second terminal while §1 R1–R3 run, then Ctrl-C.

- **Never run or save MONITOR unfiltered.** It prints every command's
  arguments, including cached response payloads with names and emails.
- **PASS:** the only lines are `"SCAN" … "MATCH" "gaplus:1:assignmentpdf:<v>:<A's uuid>:*"`.
  That is one assignment's exact prefix, with the only glob the trailing `*`.
- **FAIL:** any other SCAN pattern (for example `*user*`, `*course*`), or any
  KEYS, FLUSHDB or FLUSHALL.

If the managed Redis plan disables MONITOR or INFO, record that and rely on
whichever of 2a/2b works, plus the static guard.

## 3. H-25 commit race on the deployment (probabilistic replay)

**What this can and cannot show.** The race window is the few milliseconds
between the in-transaction generation bump and the COMMIT. Nothing reachable
over HTTPS can force a read into it, which is why Gate 8 is PARTIAL (the
founder signed that off on 2026-09-27). This replay is supporting evidence:
a stale 200 is a real FAIL, but zero stale reads only means the window was
not observed to leak.

**Round** (run 20 rounds from any machine with network access to staging):
1. Make sure S is enrolled in C (`POST /api/v1/course/{C}/students`).
2. S: `GET /api/v1/course/{C}` twice, to warm the cache (expect 200).
3. Start 16 threads, each looping S `GET /api/v1/course/{C}` as fast as
   possible.
4. While they run, T: `DELETE /api/v1/course/{C}/student/{S_id}`. Record
   the time its response arrives (`t_del`).
5. Stop the threads. Then S: `GET /api/v1/course/{C}` 5 times, 200 ms
   apart, all **after** `t_del`.

**PASS:** across all 20 rounds, **0** post-`t_del` reads return 200 (each is
404).
**FAIL:** any post-`t_del` 200. Record the round and time, stop, and tell the
SM and the Hardening Engineer.

Record only status codes and times. Reads during step 3 (before the DELETE
returns) may legitimately be 200 and are not counted.

**Control (optional, founder's decision).** The same script against the
current, unfixed deployment shows whether the replay can hit the window on
real infrastructure at all.
- If the control shows at least one stale 200 and staging shows 0, the
  result is discriminating.
- If both show 0, record it as non-discriminating.
- The control **writes test data on a live environment** (enrolment rows,
  emails, audit events), so it needs the founder's approval. The local
  replay (Security Lead, `task/cache-race-gate4`: 4/5 stale unfixed vs 0/5
  fixed) is the discriminating evidence already on record.

Each round sends S one enrolment email and one removal email. Twenty rounds
means 40 emails to the team-controlled test address.

## 4. What reads and what writes, and cleanup

| Step | Effect |
|---|---|
| §2a `INFO commandstats`, §2b filtered `MONITOR` | read-only (MONITOR adds some Redis load while open; keep it short) |
| All `GET`s | read-only (they populate cache entries, which expire by TTL: 5 min for API responses) |
| §0 setup: accounts, course C, assignment A, enrolment | **writes**: DB rows, audit events, an enrolment email |
| R1 rename, R2 publish, R3 remove/re-enrol | **writes**: row updates, generation-counter increments, audit events, removal and enrolment emails |
| R4 grading | **writes, and consumes AI credits** (optional) |
| §3, 20 rounds | **writes**: 20 enrolment deletes and re-creates, about 40 emails, audit events |

**Cleanup:**
- T deletes A, then C, through the API.
- Deactivate or keep the test accounts, per the team's staging convention.

**Do not clean up:**
- **Generation counters.** They are left in place **by design**. They have
  no TTL (see `docs/HARDENING_BACKLOG.md` H-1, "Generation-counter
  accumulation: do NOT add a TTL"). Orphaned cache entries expire on their
  own.
- **Audit events.** They are append-only and stay.
- **Redis.** Never run `FLUSHDB` or `FLUSHALL`: the same Redis holds the
  Celery broker queue and results.

## Results (fill in on the run)

| Check | Result | Notes (counts, ids, times only) |
|---|---|---|
| G1 running `version` = pushed sha (distinct values seen) | | |
| G2 `migrate --plan` empty; 0039 + audit 0001 applied | | |
| G3 `/health` 200, `/health/beat` 200, worker pongs | | |
| G4 Sentry new issues / spikes in window | | |
| Topology (web replicas / workers) | | |
| §1 R1 | | |
| §1 R2 | | |
| §1 R3 | | |
| §1 R4 (optional) | | |
| §2a commandstats deltas (scan / keys / flushdb / flushall) | | |
| §2b MONITOR SCAN patterns | | |
| §3 rounds with a post-DELETE 200 (of 20) | | |
| §3 control (optional) | | |
