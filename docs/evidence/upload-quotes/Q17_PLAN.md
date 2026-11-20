# Q17 A plan: don't try to keep files over the cloud's limit

Status: plan only, waiting for the owner's yes. Code read at beta a96b381c0
and task/upload-quotes 9635309a1. Evidence: R1_SPIKE.md. The cloud refuses a
raw file over 10,485,760 B with HTTP 400, after the whole body is sent.

## Setting

`KEPT_UPLOAD_MAX_MB` (env int, default 10, at least 1, else
ImproperlyConfigured). It derives `KEPT_UPLOAD_MAX_BYTES`. The helper
`kept_files.kept_upload_max_bytes()` reads it on every call, as
`max_upload_size_bytes()` does. A file of exactly 10,485,760 B is still kept;
only a larger file is skipped. The check runs for every backend, memory
included, so tests see it. There is no startup warning: with the default 50 MB
per-file limit it would fire on every boot.

## Store call sites (there are only two)

1. `students/kept_uploads.py` `DoorFile.__init__`. Its callers are three
   routes, `assignments/views.py` upload_assignment_async and
   `students/views.py` batch_upload and upload_answers_async, plus
   `students/task_tracking.py` record_parked_item, which keeps a parked file.
2. `students/upload_quote_service.py` `add_files`, the quote's
   `kept_files.store(content, key=key)`.

Item retry (`students/item_retry.py`) never stores; it only loads. An item
that went inline already has no kept file, so `upload_retryable` is False, as
today. No change there.

## What the teacher sees

- **Door:** today's inline behavior, minus the wasted upload (up to 225 s
  measured at 50 MB). The item runs from the inline payload. If it fails, it
  is not retried in place, which is the existing not-kept display. No new
  wording.
- **Quote:** `QUOTE_FILE_NOT_KEPT` is wrong here. It is SYSTEM 503,
  retryable, retry_after 10, and says "Add that file again.", which fails the
  same way forever. A quote cannot carry an inline file across requests, so
  the file can't run at all. **Recommendation: refuse it up front.**
  `_refusal_of` passes `max_size_bytes=min(max_upload_size_bytes(),
  kept_upload_max_bytes())`, so the file becomes a REFUSED row with
  FILE_TOO_LARGE. That uses the existing message "{file_name} is {actual} and
  the limit is {limit}." and the existing remediation, with limit = 10 MB.
  There is no store attempt and no fault. The rest of the batch carries on
  (the H-223 rule).

**Wording flag (owner):** no new text is needed, but the two paths now give
different limits for the same file. The door accepts 10-50 MB (inline, not
kept). The quote refuses anything over 10 MB, saying "the limit is 10 MB".
`MAX_UPLOAD_FILE_SIZE_MB=10` would make them agree and cost nothing. If the
owner wants the quote to say something other than FILE_TOO_LARGE, that is new
wording.

## Metric and log, replacing kept_upload_fallback for this case

- Door: `audit_metrics.count("kept_upload_over_limit")` and an INFO log, "Kept
  upload: file over KEPT_UPLOAD_MAX_MB; sending inline (%d bytes)." No
  warning, and no file name or key (N2). `kept_upload_fallback` stays for real
  storage faults only.
- Quote: no metric. It is a normal FILE_TOO_LARGE refusal, counted like any
  other refusal. `upload_quote_not_kept` stays for real faults only.

## Tests

1. Default is 10 MB, and the helper reads `override_settings` on each call.
2. Door, file 1 B over the limit: `store` is not called, no KeptUpload row,
   the payload is inline, `kept_upload_over_limit` is counted,
   `kept_upload_fallback` is not, and there is no WARNING.
3. Door, file exactly at the limit: stored and kept (boundary).
4. Door, real fault under the limit: still WARNING plus
   `kept_upload_fallback` (unchanged).
5. Quote, file over the kept limit and under the per-file limit: REFUSED
   FILE_TOO_LARGE with limit = kept limit and no store call. The other files
   in the same add are stored. Confirm records it as a refused item.
6. Quote, at the limit: stored.
7. The three door routes each send an over-limit file inline, and a parked
   over-limit file is recorded with no kept file (parametrized).

Plants (3):
- (a) drop the door's size check, which test 2 catches;
- (b) quote passes only the per-file limit, which test 5 catches;
- (c) `>=` instead of `>`, which tests 3 and 6 catch.

## Estimate

About half a day: 1-2 h of code (setting, helper, two call sites), 1-2 h of
tests and plants, then the guard and near gates (about 15 min of runs). It
touches the quote branch's `add_files`, so it builds on task/upload-quotes,
or on beta for the door half if the quote has not landed yet.
