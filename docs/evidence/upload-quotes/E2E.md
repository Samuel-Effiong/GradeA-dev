# Upload quote, local end-to-end run (2026-11-23)

Setup: staging 02e1a26e2 plus the Worker default queues change (97ecf993f, Fixer) merged. Real worker (`celery -A AutoGrader worker -l info --pool=gevent --concurrency=200 --prefetch-multiplier=1`, no -Q), beat, Redis db 13, real Cloudinary, real AI provider, app on :8001, own DB.

Baseline (02e1a26e2 alone): the worker listens to `celery` only; the measure job sat in the `quote_measure` list, file stayed STORED, ready=false. With the Fixer's change the same worker lists both queues and the job ran.

1. Happy path (3 PDFs): PASS. 201, ready with p50/p90, 10-minute deadline, open list, ended quotes audited as "Upload quote".
2. Confirm with enough credits (2 files): PASS. 202, both SUCCESS.
3. Credits for part (3 files, 1 fits): PASS. 202, 1 ACCEPTED, 2 PARKED (INSUFFICIENT_CREDITS); after a top-up both retried and finished.
4. Zero spendable: PASS. 402 at create, no quote row, nothing stored.
5. Cancel during an add (40 files): PASS. DELETE 200, the add answered 409 QUOTE_CLOSED, no Cloudinary file without a DB key.
6. Expiry (lifetime 1 minute): PASS. EXPIRED, confirm 410 with the approved wording, files purged (see F2, F3).
7. Over 10 MB: PASS (FILE_TOO_LARGE, other file stored). Too many files: FAIL at the default limit (F1); PASS with limit 5 (approved wording).
8. Listing and the 2-at-once cap (12 and 100 files): PASS, see F5.

Findings (not fixed):
- F1 MEDIUM: 101 files in one request returns 500. Django's DATA_UPLOAD_MAX_NUMBER_FILES (default 100) raises TooManyFilesSent before the view's own limit check, so the approved "up to {limit} files" message is unreachable at UPLOAD_QUOTE_MAX_FILES=100. 100 files work. Repro: POST 101 small PDFs to /api/v1/upload-quotes/.
- F2 LOW: the expiry text says "more than 1 minutes" when the lifetime is 1.
- F3 LOW: a quote reads OPEN after expires_at until the purge (every 5 minutes) marks it EXPIRED.
- F4 LOW: the route audit events (create, add, confirm refusals) record target types UploadQuoteCollection, UploadQuoteFiles and UploadQuoteConfirm, which have no target label; only UPLOAD_QUOTE_ENDED shows "Upload quote".
- F5 INFO: measure is slow at scale: 12 files 86 s, 100 files 280 s, with 575 slot-full retries (15 s countdown) for 100 files. Running measures stayed at 2 (the counter shows 3 for an instant: it adds 1, then subtracts).

Spend: 7 AI calls, 82,013 tokens, $0.0715. Cloudinary: every test file was removed by the purge (0 left in the folder); nothing deleted by hand.
