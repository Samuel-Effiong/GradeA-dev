# Upload quote: build notes

Branch `task/upload-quotes`, from PLAN_REV3 with the owner's rulings of
2026-11-20. Requirements cited: BE-B-03 (the figure), BE-B-04 (a verdict
before anything runs), BE-B-05/06/07 (confirm), BE-B-09 (the trust counter),
BE-A-01 (audit), BE-A-06 (reason codes).

## What the teacher's screen does

1. `POST /api/v1/upload-quotes/` (multipart): `kind`, `assignment_id`
   (answers) or `course_id` + optional `topic_id` (assignment files), and
   `files` (list order is the position). One request carries every file; the
   frontend does no check of its own. 201 with the quote and one entry per
   file (stored, or refused with its own reason code). One bad file never
   fails the others.
2. Poll `GET /upload-quotes/{id}` until `ready`. Add more with
   `POST /upload-quotes/{id}/files` (positions continue; sha256 de-dup; 100
   files per quote; 500 MB per request; open quotes only).
3. `POST /upload-quotes/{id}/confirm` starts the batch: what fits runs, the
   rest is parked (kept, retryable). 202 the first time, 200 with the same
   body when repeated. `would_park` in the body is accepted and ignored.
4. `DELETE /upload-quotes/{id}` cancels. `GET /upload-quotes/?open=true`
   finds a reloaded page's quote.

## For the frontend (all of it is in the replies)

* **Idempotency-Key** (header, optional, a client UUID): a repeat of the same
  key returns the same quote with 200 and stores nothing again. Without a key
  a retry makes a NEW quote (bounded by 3 open quotes and the lifetime).
* **Lifetime:** every quote reply (create, add, GET, list) carries
  `expires_at` and `lifetime_minutes`. The setting is
  `UPLOAD_QUOTE_LIFETIME_MINUTES` (default 10). `expires_at` is created + the
  lifetime, and restarts at ready + the lifetime when the last file is
  measured. A quote still measuring never expires; an unready quote older
  than `UPLOAD_QUOTE_UNREADY_CEILING_MINUTES` (60) is purged anyway. The 410
  `QUOTE_EXPIRED` carries both `expires_at` and `lifetime_minutes` in `params`.
  The purge runs every 5 minutes.
* **At 0 spendable credits** (balance less live holds, the door's own figure)
  create and add answer the existing 402 `insufficient_credits` before
  anything is stored. A teacher who reaches 0 while a quote is open still
  confirms: every file parks and the answer is 202.
* Confirm is open to a changed balance (owner Q2 = A): it recomputes against
  the live balance and the live figures, runs what fits, parks the rest.
* A file Cloudinary refuses (for example over its plan's per-file limit)
  ends `NOT_KEPT` / `QUOTE_FILE_NOT_KEPT` (503 class, `retry_after` 10) for
  that file only.
* The switch `UPLOAD_QUOTES_ENABLED` is on by default (owner, 2026-11-20); off
  answers 404 before authentication.

## The figure (BE-B-03)

A file's figure is exactly the door's: the measure stores per-page tokens
(`ai_processor.call_estimates.page_tokens_of`), the text part is recomputed
live at each GET and at confirm, and the two compose in
`estimate_from_page_tokens`, which `upload_item_estimate` (the door's own
function) now calls. Old == new on 20+ fixtures, both kinds. The headroom per
call is `CALL_HEADROOM` (8,000 for answer extraction, 12,000 for assignment
extraction, pages in chunks of 3). The upload history figure divides by the
median overestimate: `UPLOAD_DIVISOR_FALLBACK` = 1.46 (106 calls, tolerance
checked on 2026-11-19; `CALL_HEADROOM_SINCE` is 2026-11-19 12:33 UTC; at
least 30 calls before the history replaces it). Cost is rounded up and the
balance down for display.

## How it holds together

* Own tables (`UploadQuote`, `UploadQuoteFile`, migration 0046), so nothing in
  the task machinery can see an unconfirmed quote: no session, item, hold, job
  or ledger row exists before confirm (BE-B-04).
* Row-first storage: a file's row commits with its random key before the
  bytes are stored, so a dead request leaves a row the purge clears by key.
* The measure runs on its own queue `quote_measure` of the existing worker
  (`-Q celery,quote_measure`), two at a time (`UPLOAD_QUOTE_MEASURE_CONCURRENCY`),
  never charged. A stuck file is re-asked after 10 minutes, 3 attempts, then
  it is left to the task (`readable: null`).
* Confirm (BE-B-05/06/07) is one transaction under the quote's row lock: the
  session, an item per file and a `KeptUpload` that takes over the quote
  file's own storage key (no copy); the quote file's key is nulled so its
  delete signal leaves the asset. Files are walked in position order with
  `plan_parking`; what fits is held (`DoorHold`) and launched after commit
  (`launch_at_door` with holds on, the same mechanics without a hold when
  holds are off or draining); a lost hold race parks that one file (N1).
  Nothing fitting parks every file and answers 202; the discard-all 402 and
  the inline fallback are never reached.
* One audit event per answers confirm (the existing `SUBMISSION_UPLOAD`, ids
  and counts), and `UPLOAD_QUOTE_ENDED` for a cancel or an expiry. The
  assignment-files route writes no audit event at the door today, so its
  confirm writes none either.
* No file name in any log line, metric or quote reply; names live only in the
  quote file row, to become each item's `file_name` at confirm.

## Deploy

* The worker must consume the `quote_measure` queue (`-Q celery,quote_measure`,
  `scripts/start-worker.sh`). Without it a quote's files are stored but never
  measured, so no quote becomes ready. Check every worker service that runs
  the Celery app, not only the one this script starts.
* `confirm_result` holds each file's name for the replay window
  (`UPLOAD_QUOTE_CONFIRMED_KEEP_DAYS`, then the quote is deleted). Only a
  teacher owns a quote, so a student erasure does not reach it; a student
  account that somehow owns one is refused (`students.UploadQuote.teacher` is
  NOT_A_STUDENT in the data map, with a test).
* Migration 0046 (renumbered from 0045 for staging) has not shipped; its `(quote, sha256)` unique constraint
  ignores NOT_KEPT rows so "Add that file again" works.

## Open items

* None open. The gate half of BE-B-09 is wired: confirm records the quoted
  figure on each item and `execute_graded_task` calls
  `students.upload_quotes.note_gate_for_task` (never raises).
