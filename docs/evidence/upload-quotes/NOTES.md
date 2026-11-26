# Upload quote: build notes

Branch `task/upload-quotes`, from PLAN_REV3 with the owner's rulings of
2026-11-20. Requirements cited: BE-B-03 (the figure), BE-B-04 (a verdict
before anything runs), BE-B-05/06/07 (confirm), BE-B-09 (the trust counter),
BE-A-01 (audit), BE-A-06 (reason codes).

## What the teacher's screen does

1. `POST /api/v1/upload-quotes/` (multipart): `kind`, `assignment_id`
   (answers) or `course_id` + optional `topic_id` (assignment files), and
   `files` (list order is the position). One request carries every file; the
   frontend does no check of its own. 201 at once with the quote and one
   entry per file: `STORING` (kept; the bytes are stored after the reply), or
   `REFUSED` with its own reason code. One bad file never fails the others.
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

### Frontend note: the reply comes before the storage (2026-11-26)

* **STORING.** The create and add-files 201 no longer wait for Cloudinary. A
  kept file starts `STORING`, then goes `STORED` -> `MEASURING` ->
  `MEASURED` on later polls. `ready` is false until every file is settled
  (`MEASURED`, `REFUSED` or `NOT_KEPT`), and `measured`/`total` count the
  settled files. STORING is a state code, not wording: no new sentence.
* **NOT_KEPT can arrive on a poll.** A file the storage could not keep (after
  one retry, and the store task's own retries) turns `NOT_KEPT` with
  `QUOTE_FILE_NOT_KEPT` after the 201, not in it. Show it as today's
  `NOT_KEPT` entry.
* **duplicate_of.** An entry with `duplicate_of` (the same bytes as an earlier
  file in this quote) has `id: null` and is in the create/add reply only.
  GET lists one entry per stored file, so read a duplicate's state from the
  file its `duplicate_of` names.
* **Polling.** Poll GET every 2 s at first, rising to 5 s (for example 2, 3,
  4, 5, 5, ...), until `ready`. The read throttle is 120 a minute per teacher.
* **A replay** with the same `Idempotency-Key` while files are storing
  answers 200 with every row (all written in one transaction), never a part.

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
* Reply first (owner, 2026-11-26): the request writes every row and, for a
  kept file, its bytes (`UploadQuoteStagedFile`, migration 0048) in ONE
  transaction and answers 201; on commit it asks the Celery task
  `students.tasks.store_quote_files` (queue `quote_measure`, consumed by every
  worker) for the STORING rows. The task loads `UPLOAD_QUOTE_STORE_CONCURRENCY`
  files' bytes at a time, recomputes sha256 and size against the row and the
  staged row (a mismatch: `NOT_KEPT`, metric `upload_quote_bytes_changed`,
  nothing stored), stores them, and checks Cloudinary's reply (`bytes` = the
  size, `etag` = the md5; `UPLOAD_QUOTE_VERIFY_ETAG` can switch the etag
  check off; a mismatch deletes the asset: `NOT_KEPT`, metric
  `upload_quote_reply_mismatch`). It settles each batch under the quote's
  lock: STORING -> STORED (with `stored_at`) only while the quote is open and
  the row still holds the key; otherwise it deletes what it stored, unless a
  duplicate run already settled the row. Staged bytes go once a row settles;
  then the task asks for the measure and restarts the clock. A storage fault
  keeps the bytes staged and retries the task (5 times, a minute apart); the
  last try ends `NOT_KEPT`.
* The store's net (with `requeue_stuck_quote_measures`, every 5 minutes): a
  STORING file not settled after 10 minutes is asked for again while its
  bytes are staged; with none staged, or after 30 minutes, it ends
  `NOT_KEPT` and its key is deleted (metric `upload_quote_store_gave_up`).
  Cancel, the purge and a row's delete (erasure, a cascade) remove staged
  bytes. Cancel and the purge delete a STORING file's key but leave it on the
  row until 30 minutes have passed, so bytes that land late are deleted on
  the next purge; the task also deletes what it stored for an ended or
  erased quote. The measure's stale and alarm clocks run from `stored_at`.
* Row-first storage: a file's row commits with its random key before the
  bytes are stored, so a dead store leaves a row the purge clears by key.
* Stores run `UPLOAD_QUOTE_STORE_CONCURRENCY` (default 5, owner 2026-11-26)
  at a time (`students.quote_store_pool`, used by the store task). A store the storage did not answer
  (an error or a timeout) is tried once more (metric
  `upload_quote_store_retried`) before the file is `NOT_KEPT`; a Cloudinary
  400 and changed bytes are never retried. Only the storage call runs in the
  pool; every row write stays on the task's thread. Django writes every upload
  over `FILE_UPLOAD_MAX_MEMORY_SIZE` (256 KB, set for this) to a temporary
  file as it arrives, and the request reads one file at a time to stage it,
  so it holds in RAM only the files under 256 KB and the file being staged.
  Staged bytes take database space until stored (up to the 1 GB per-teacher
  room); the store usually empties them within seconds.
* A quote with no file is not ready (confirm answers `QUOTE_NOT_READY`).
  `open_bytes` counts only rows that hold or will hold bytes (STORING, STORED,
  MEASURING, MEASURED): a refused or not-kept file fills no room.
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
* Migration 0048 (`UploadQuoteStagedFile`, `UploadQuoteFile.stored_at`)
  depends on 0046; staging, which already has 0047_merge_upload_quotes, needs
  a merge migration (prepared on `task/quote-reply-first-staging`). The store
  task runs on `quote_measure`, which every worker consumes (declared in
  `CELERY_TASK_QUEUES`); no start command changes.

## Open items

* None open. The gate half of BE-B-09 is wired: confirm records the quoted
  figure on each item and `execute_graded_task` calls
  `students.upload_quotes.note_gate_for_task` (never raises).
