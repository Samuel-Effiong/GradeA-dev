# Upload quote: new wording, APPROVED by owner 2026-11-20

Approved by the owner on 2026-11-20 and in `AutoGrader/reason_codes.py`.
US spelling, no file name.

| Code | Where the teacher meets it | Proposed message | Proposed hint |
|---|---|---|---|
| QUOTE_NOT_READY (409) | Start pressed before every file is measured | We're still working out what these files will cost. | Wait a moment, then try again. |
| QUOTE_EXPIRED (410) | Start or add pressed after the quote's lifetime (10 minutes by default, `UPLOAD_QUOTE_LIFETIME_MINUTES`) | This upload waited more than {lifetime_minutes} minutes, so we removed the files. | Upload the files again. |
| QUOTE_CLOSED (409) | Cancel after the upload was started, or Start on a cancelled quote | This upload has already been started or cancelled. | (none) |
| QUOTE_STORAGE_FULL (413) | The teacher's waiting uploads hold 1 GB, or the global ceiling is reached | We can't hold more files for you right now. | Start or cancel your waiting uploads, then try again. |
| QUOTE_TOO_MANY_OPEN (429) | A fourth waiting upload | You have too many uploads waiting for you to start them. | Start or cancel one of them, then try again. |
| QUOTE_FILE_NOT_KEPT (per file, 503) | One file's storage failed; the others are fine | We couldn't keep one of the files. | Add that file again. |

Notes for the owner:

* The expired text carries the number from `UPLOAD_QUOTE_LIFETIME_MINUTES`
  at reply time; the 410's params carry `lifetime_minutes` and `expires_at`.
* The oversize-request refusal (500 MB) reuses the Fixes Worker's
  FILE_TOO_LARGE request variant (`task/upload-request-cap`, its wording is
  their Q8). No new sentence here.
* A file refused for its own reason (type, empty, over 50 MB, unreadable) uses
  the existing codes and sentences unchanged.
* QUOTE_BALANCE_CHANGED is not written (owner Q2 = A: confirm runs what fits
  and parks the rest).
* At 0 spendable credits the quote routes answer the existing 402
  (`insufficient_credits`, already-approved wording); no new sentence.
* The "too many files" 400 for one request or one quote (100 files) is the
  one remaining proposal: "You can check up to {limit} files at a time. Remove
  some and try again." (not yet approved).
