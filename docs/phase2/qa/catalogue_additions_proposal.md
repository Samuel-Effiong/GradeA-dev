> **APPROVAL RECORD.** Approved **as written** by the founder, acting as QA, on 2026-09-30. The SM relayed it; nothing in the text below was changed.
> - Every code in sections A to G, including the changed `INSUFFICIENT_CREDITS_MID_BATCH` remediation (section G).
> - Question 2: the two new checks, `ROW_EMAIL_INVALID` and `ROW_DUPLICATE`, are confirmed.
> - Question 3: the neutral privacy wording is confirmed. `TEACHER_HAS_INDIVIDUAL_SUBSCRIPTION` keeps the proposed text. The founder was told it discloses the teacher's own-subscription status. The text lives in ONE constant (`AutoGrader/reason_codes.py`), so it can be switched to the generic "This email can't be added as a teacher." later.
> - Question 4: `FILE_NOT_A_PDF` is a separate code (option 1).
>
> Source: `task/qa-catalogue-proposal` at 0ecad75. Built in Epic A slice S7d (sections B–F); A and G were already built. The "staging only, not beta" line below no longer applies to the approved codes.

# Error catalogue: proposed additions for QA approval

**For:** QA, sent by the founder. **From:** the engineering team (drafted by ed, Security). **Date:** 2026-09-30.
**Asks:** approve, change or reject each code below. Until QA approves a code, it may be used on **staging only, not beta**.

## Why this is needed
QA-ERR-02 lists **10 error conditions**, each with a stable code, a message and a remediation, and the product already uses them. Some screens show errors that aren't among those 10:
- **roster import**: adding students from a spreadsheet;
- **licence teacher management**: a school admin adding or removing teachers;
- **publishing grades**;
- **the sign-in locks**;
- **one upload hint**.

The founder decided (F9, 2026-09-29) that roster import and licence teacher management must report **per row**, with a row number, a code and a message, as batch grading does. QA owns the catalogue, so every new code needs QA's approval first.

## How to read the tables
- **Code**: the stable machine name the app sends (`reason_code`). It never changes once approved.
- **Where**:
  - **request**: the whole request is refused with an HTTP status;
  - **row** / **teacher** / **item**: one line in a per-item result list; the rest of the request goes ahead.
- **HTTP**: the status for a request-level refusal. For compatibility we propose **keeping today's status** unless noted.
- **Message**: what the user sees. `{…}` is a value filled in (listed under **Params**). The message goes only to the person who made the request, who is entitled to see these values.
- **Remediation**: the "what to do next" line shown with it.
- **Today**: the current text, for comparison.

Every coded error also carries `error_class` (USER for all of these), `retryable`, `params` and `reference` (a support reference). Screens don't show those.

---

## A. Sign-in locks (already built, awaiting approval)
Built and verified (staging only). The responses keep every field the frontend already uses; the code and envelope are added beside them.

| Code | Where / HTTP | Message | Params | Remediation | Retryable |
|---|---|---|---|---|---|
| `RESET_LOCKED` | password reset / 429 | Unchanged dynamic text: "For your security, password reset is paused on this account because the code was entered incorrectly 5 times. You can request a new code after {time} UTC (in {minutes} minutes). Your password has not been changed…" | (in the text) | "Wait until the time shown, then request a new code. Your password has not been changed." | yes |
| `VERIFY_LOCKED` | email verification / 429 | Unchanged: "Too many incorrect codes for this email address. Please wait, then request a new verification email." | — | "Wait, then request a new verification email." | yes |
| `ACCOUNT_LOCKED` | sign-in / 401 | Unchanged: "Too many failed login attempts. Please try again later." | — | "Wait a few minutes and try again, or reset your password." | yes |

Note: VERIFY_LOCKED answers exactly the same whether or not an account exists for the address, so it reveals nothing.

## B. Student registration paused
| Code | Where / HTTP | Message | Params | Remediation | Retryable |
|---|---|---|---|---|---|
| `REGISTRATION_PAUSED` | student sign-up from an invitation / 429 | "Student registration is paused for a short while because of too many invalid activation codes. Please try again later; if your code has expired by then, ask for a new one." (today's text, unchanged) | — | "Try again in a few minutes. If your code has expired, ask your teacher for a new one." | yes |

Today this is a plain "too many requests" answer, with no code, so it can't be told apart from the normal per-network rate limit.

## C. Upload hint: a photo saved as a PDF
| Code | Where / HTTP | Message | Params | Remediation | Retryable |
|---|---|---|---|---|---|
| `FILE_NOT_A_PDF` | answer or assignment upload / 422 (request or item) | "{file_name} is not a PDF. If it is a photo or scan, upload it as an image instead." | `file_name` | "Upload the photo or scan as an image (JPEG, PNG, GIF or WebP)." | no |

Today this case falls under `FILE_UNREADABLE` ("We couldn't read {file_name}. It may be damaged, password-protected or incomplete."). The more helpful hint exists in the code but never reaches the user.
- **Alternative:** keep `FILE_UNREADABLE` and add this sentence as its remediation when the file is an image.
- **Recommendation:** a separate code, so support can count how often it happens.

---

## D. Roster import (add students from a spreadsheet or pasted list)
### D1. Whole-request refusals (the import doesn't start)
| Code | HTTP | Message | Params | Remediation | Today |
|---|---|---|---|---|---|
| `ROSTER_NO_INPUT` | 400 | "Upload a roster file or paste your student list." | — | "Choose a CSV file, or paste rows copied from your spreadsheet." | "Either a CSV file or raw text data must be provided." |
| `ROSTER_EMPTY` | 400 | "This roster has no student rows." | — | "Check that the file has one student per row, then try again." | "No valid student data found in input" |
| `ROSTER_FILE_UNREADABLE` | 400 | "{file_name} isn't readable as text." | `file_name` | "Export your roster as a CSV file (UTF-8) and try again." | "This file isn't readable as UTF-8 text. Export your roster as a CSV file and try again." |
| `ROSTER_TOO_MANY_ROWS` | 400 | "This roster has {row_count} rows. Upload at most {max_rows} rows at a time." | `row_count`, `max_rows` | "Split the roster into smaller files." | same text |
| `FILE_TOO_LARGE` (existing code) | **413** (today 400) | "{file_name} is {actual} and the limit is {limit}." | as the existing code | as the existing code | "This file is too large. Upload a roster of at most 2048 KB." |

### D2. Per-row results
Every row in the result list gains its **row number** (`row`, counting from the first data row) and, when it isn't added, a code:

| Code | Result | Message | Params | Remediation | Today |
|---|---|---|---|---|---|
| `ROW_NAME_MISSING` | failed | "Row {row}: a first and a last name are required." | `row` | "Add the missing name and import the row again." | "First and last names are required." |
| `ROW_NAME_INVALID` | failed | "Row {row}: each name needs between 2 and 150 characters." | `row` | "Correct the name and import the row again." | the raw field rule ("Ensure this value has at least 2 characters…") |
| `ROW_ALREADY_ENROLLED` | skipped | "Row {row}: {student_display} is already in this course." | `row`, `student_display` | none (nothing to do) | "Already enrolled" |
| `ROW_NAME_CLASH` | failed | "Row {row}: a student named {student_display} is already in this course." | `row`, `student_display` | "Add an email address to tell the two students apart." | "A student with the exact name '…' is already enrolled in this course." |
| `ROW_STAFF_EMAIL` | failed | "Row {row}: this email can't be added as a student." | `row` | "Use the student's own email address." | "This email belongs to a {teacher / school admin …} account and cannot be added as a student." That told any teacher the role of an arbitrary address. **Being fixed on beta** (H-71) to the proposed text, without the row, on single add, bulk import and direct add. |
| `ROW_OTHER_SCHOOL` | failed | "Row {row}: this account can't be added to this school. If you believe this is a mistake, contact your school administrator." | `row` | (in the message) | same text, without the row. It is **deliberately generic**: it never names the other school. |
| `ROW_ACCOUNT_DISABLED` | skipped | "Row {row}: this student's account is disabled." | `row` | "Contact support if they should have access." | "This student's account is disabled. Contact support if they should have access." |
| `ROW_EMAIL_INVALID` | failed | "Row {row}: "{email}" isn't a valid email address." | `row`, `email` | "Correct the email and import the row again." | **Not checked today** (see note) |
| `ROW_DUPLICATE` | skipped | "Row {row} repeats row {first_row}." | `row`, `first_row` | none | Not detected today; the repeat shows as "Already enrolled" |
| `ROW_FAILED` | failed | "Row {row}: this student couldn't be added." | `row` | "Check the row and try again. If it keeps failing, contact support and quote the reference." | "Could not add this student — check the row data and try again." |

**Note on `ROW_STAFF_EMAIL`:** the neutral text is the intended wording, not a placeholder. Naming the role ("a teacher account", "a school admin") would let a teacher learn who on the platform is staff by typing addresses into the form. For the same reason, the message carries no `account_type` param. The server log records the account id and its type for an admin. It is the student-side counterpart of `TEACHER_EMAIL_OTHER_ROLE` in section E.

**Note on `ROW_EMAIL_INVALID` and `ROW_DUPLICATE`:** these are **new checks**, not new wording.
- Today an invalid address such as "abc" in the email column creates an account and queues an invitation that can never be delivered.
- Today a repeated row is reported as "Already enrolled".

QA, please confirm you want these checks as part of this work.

## E. Licence teacher management (school admin)
### E1. Whole-request refusals
| Code | HTTP | Message | Params | Remediation | Today |
|---|---|---|---|---|---|
| `TEACHER_LIST_EMPTY` | 400 | "Add at least one teacher." | — | "Enter the teachers' email addresses." / "Choose the teachers to remove." | "teacher_emails is required" / "teacher_ids is required" |
| `LICENCE_INACTIVE` | 400 | "This licence isn't active, so teachers can't be added to it." | — | "Renew the licence, or contact us." | same |
| `LICENCE_SEATS_EXCEEDED` | 400 | "Your licence has {remaining} seats left, but you're adding {adding} teachers ({in_use} of {max_seats} in use)." (with remaining 0: "Your licence has no seats left ({in_use} of {max_seats} in use).") | `remaining`, `adding`, `in_use`, `max_seats` | "Add fewer teachers, remove a teacher, or ask us to add seats." | the same two texts |

### E2. Per-teacher results
The result gains a **list of successes** (today only a count) and a code on each failure:

| Code | Result | Message | Params | Remediation | Today |
|---|---|---|---|---|---|
| `TEACHER_EMAIL_NOT_BUSINESS` | failed | "{email} isn't a school or work email address." | `email` | "Use the teacher's school or work email." | "Email … is not a business email. Only business emails are allowed." |
| `TEACHER_EMAIL_OTHER_ROLE` | failed | "This email can't be added as a teacher." | — | "Use the teacher's own account email." | "Email … already belongs to a STUDENT account, not a teacher." This told any school admin the role of an arbitrary address. **Already fixed on beta** (the add-teachers disclosure fix) to the proposed text. |
| `TEACHER_HAS_INDIVIDUAL_SUBSCRIPTION` | failed | "{email} has their own subscription, which must be cancelled before they can join the licence." | `email` | "Ask the teacher to cancel their individual subscription, then add them again." | a longer version of the same. **Please weigh:** this tells any school admin whether an arbitrary teacher pays for their own subscription (their billing status). It is useful to a genuine admin but a disclosure to anyone else. The alternative is the generic "This email can't be added as a teacher." |
| `TEACHER_IN_OTHER_SCHOOL` | failed | "This teacher already belongs to another school." | — | "Contact support if the teacher has moved schools." | "Teacher '…' already belongs to school '{other school's name}'…". That named another school to any admin; **already fixed on beta** to the proposed text. |
| `TEACHER_ALREADY_ON_LICENCE` | skipped | "{email} is already on this licence." | `email` | none | Silently skipped today (not reported at all) |
| `TEACHER_NOT_ON_LICENCE` | failed (removal) | "This teacher isn't an active teacher on this licence." | — | none | same |
| `TEACHER_ADD_FAILED` / `TEACHER_REMOVE_FAILED` | failed | "We couldn't add this teacher." / "We couldn't remove this teacher." | — | "Try again. If it keeps failing, contact support and quote the reference." | similar generic texts |

## F. Publishing grades
| Code | Where / HTTP | Message | Params | Remediation | Today |
|---|---|---|---|---|---|
| `SUBMISSION_NOT_GRADED` | single publish: request / 400. Publish-all: item, skipped | "This submission hasn't been graded yet, so it can't be published." | — | "Grade it first, then publish." | single: "Cannot publish an ungraded submission."; publish-all: counted as `ungraded_count`, not listed |

"Nothing to publish" on publish-all stays a normal answer, not an error, as today.

## G. Retrying failed batch items, and credits running out mid-batch (slices S7b / S7c)
Teachers can now retry the failed items of a batch (a batch upload or grade-all) instead of starting over.

| Code | Where / HTTP | Message | Params | Remediation | Retryable | Status |
|---|---|---|---|---|---|---|
| `NOT_RETRYABLE` | retrying one item / 409 | "{why}". The general case: "This item can't be retried as it is." For a failed **upload** item (its file isn't kept, founder decision F4): "This upload can't be retried as it is, because its file isn't kept. Upload the file again." | `why` (fixed server text, one of the two above), `resolution` = `"replace_file"` for an upload item (tells the app to offer "upload again") | "Fix what its failure message describes first, then try again." | no | **new, beyond the 10**: please approve |
| `INSUFFICIENT_CREDITS_MID_BATCH` | an item in a batch / (402 if ever answered directly) | "Credits ran out after {completed} of {total} items. The finished items are saved." (unchanged) | `completed`, `total` | **Changed:** "Top up credits, then resume the remaining items. Unfinished uploads need their files uploaded again." Approved text: "…There is no need to upload them again." It changed because uploads aren't stored (F4), so an unfinished **upload** must be sent again. | yes | one of QA's 10: **approve the new remediation** |

**A new field on the batch result, for QA's awareness:** the batch progress now carries `stopped_at_item`, the position of the first item that stopped for lack of credits (none if credits never ran out), and `resumable`. The app can then say "stopped at item 12 of 30" and offer "Resume". No new text is involved.

---

## What we need from QA
1. For each code: **approve**, **change** (the wording, the remediation or the status) or **reject**.
2. Confirm the two **new checks** in D2 (`ROW_EMAIL_INVALID`, `ROW_DUPLICATE`).
3. Note the **privacy changes** in E2: `TEACHER_IN_OTHER_SCHOOL` and `TEACHER_EMAIL_OTHER_ROLE` were fixed on beta as cross-tenant disclosures; confirm the wording. Decide `TEACHER_HAS_INDIVIDUAL_SUBSCRIPTION` (it discloses billing status).
4. Choose the option in C (a separate `FILE_NOT_A_PDF` code, recommended, or a variant of `FILE_UNREADABLE`).
5. Section G: approve `NOT_RETRYABLE` and its two `why` texts, and the **changed remediation** of `INSUFFICIENT_CREDITS_MID_BATCH`.

Until then:
- sections A and G are built, and stay on staging only;
- sections B–F aren't built yet (slice S7d waits for your answers).
