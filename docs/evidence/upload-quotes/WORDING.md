# Upload quote: new wording, proposals only (owner approval needed)

Nothing below is approved. Each sentence is in `AutoGrader/reason_codes.py`
as a placeholder so the codes work; the owner's final text replaces it. US
spelling, no file name, no number of days.

| Code | Where the teacher meets it | Proposed message | Proposed hint |
|---|---|---|---|
| QUOTE_NOT_READY (409) | Start pressed before every file is measured | We're still working out what these files will cost. | Wait a moment, then try again. |
| QUOTE_EXPIRED (410) | Start pressed after the quote's lifetime (24 h by default) | This upload was waiting too long, so we removed the files. | Upload the files again. |
| QUOTE_CLOSED (409) | Cancel after the upload was started, or Start on a cancelled quote | This upload has already been started or cancelled. | (none) |
| QUOTE_STORAGE_FULL (413) | The teacher's waiting uploads hold 1 GB, or the global ceiling is reached | We can't hold more files for you right now. | Start or cancel your waiting uploads, then try again. |
| QUOTE_TOO_MANY_OPEN (429) | A fourth waiting upload | You have too many uploads waiting for you to start them. | Start or cancel one of them, then try again. |
| QUOTE_FILE_NOT_KEPT (per file, 503) | One file's storage failed; the others are fine | We couldn't keep one of the files. | Add that file again. |

Notes for the owner:

* The expired text must never say "30 days": the quote's own lifetime is a
  setting (24 hours by default), and `QUEUE_LIFETIME_DAYS` is a different clock.
* The oversize-request refusal (500 MB) reuses the Fixes Worker's
  FILE_TOO_LARGE request variant (`task/upload-request-cap`, its wording is
  their Q8). No new sentence here.
* A file refused for its own reason (type, empty, over 50 MB, unreadable) uses
  the existing codes and sentences unchanged.
* QUOTE_BALANCE_CHANGED is variant B of the credits-drop question and is not
  written until the owner rules (slice 5).
