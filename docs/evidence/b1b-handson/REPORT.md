# B1b hands-on test: results

Brief 7, 11-12 Nov 2026. The real app ran on this machine with the new credit
behavior ON. I acted as a teacher, a school-license teacher, a school admin, a
super admin and students, using the same API calls the website makes, with
real logins and real AI calls. Code: staging a0478508e, then staging
ef1080075 (the door-close fix) for the reruns, then staging b3c900aaf (the
cancel-hold fix) for the BUG-3 rerun, then staging 30926b024 (the retry-message
fix) for the BUG-2 rerun.

## Summary

| # | Situation | Result | In one line |
|---|---|---|---|
| 1 | Cost estimate | PASS | Enough, and not enough, are judged right. Over 500 papers gets the owner's exact message. |
| 2 | Full batch with enough credits | PASS | Credits are held during the run and released after, and the balance is exact to the credit. |
| 3 | Credits run out partway | PASS | Finished papers stay graded, the rest wait, and a top-up restarts them by itself. |
| 4 | Waiting work expires | PASS | Warning first, then expiry. Nothing charged, and both emails word for word. |
| 5 | Cancel waiting work | PASS | Cancelled, nothing charged, no email. |
| 6 | Late door | PASS | Refused, nothing charged, the late-door email sent. |
| 7 | Retry from the kept file | PASS on the fix (BUG-2) | The retry works without re-uploading. A cancelled upload's retry message was wrong (BUG-2), now fixed and confirmed. |
| 8 | Low-balance warnings | PASS (0% not reached) | The 25% and 10% emails are word for word, and the screens show the right status. |
| 9 | Other AI actions | PASS | Charged right, holds released, refused right when short. |
| 10 | Two batches at once, one wallet | PASS | Credits never went below zero; the extra paper waited, then ran. |
| 11 | School-license teacher | PASS | Same behavior, with the "ask your school administrator" line. |
| 12 | Students | PASS | No job ids or kept-file dates shown, and no access to other students' work. |
| 13 | Switch-off safety | PASS on the fix (was blocked by BUG-1) | Drain reached DRAINED with work in flight; flag off behaves as before. |
| 14 | Wording | PASS, with notes | Every text I could reach matches. Three approved texts read oddly (listed below). |
| 15 | Errors and logs | PASS | No server errors and no crashes in any log. |

**Product bugs found: 3.** All were reported with steps, and the SM assigned them.

- **BUG-1 (fixed, confirmed):** an upload job never closed after its files
  finished, on all three upload routes (teacher batch, student, assignment). It
  stayed "reserved" forever, so switch-off (13) could never finish. Fixed on
  staging ef1080075, and confirmed here: a fresh upload ends SETTLED and drain
  reaches DRAINED.
- **BUG-2 (fixed, confirmed):** retrying an upload that the teacher
  cancelled said "its file was deleted after 30 days". The file had been
  deleted seconds earlier, by the cancel. Fixed on staging 30926b024, and
  confirmed here: a cancelled upload gets the plain "can't be retried"
  answer, and only a file that really reached its 30 days says so (see
  "BUG-2 rerun" below).
- **BUG-3 (fixed, confirmed):** cancelling an upload that hasn't started
  yet left its credit hold in place, so the teacher's spendable credits stayed
  21,146 lower with nothing running. Only the 36-hour backstop might ever
  have returned them, and the books' own self-check didn't flag it. Fixed on
  staging b3c900aaf, and confirmed here: the hold comes back the moment the
  upload is cancelled, and a normal upload is still charged exactly (see
  "BUG-3 rerun" below).

All three bugs are fixed and confirmed.

## How it was run

- **Setup:**
  - A fresh database, created and dropped through the app's own Django
    connection (owner OK).
  - A private Redis, so no other session's tasks were touched.
  - The web server, a task worker and the scheduler.
  - Emails printed to the log, never sent. MailerLite and Sentry off.
- **Done in the Django shell, and only for setup or moving the clock:**
  - Creating plans and accounts. A fresh database has no plans, so I made a
    small Standard plan and a Pro License plan.
  - Creating the school license (offline billing).
  - Moving `expires_at` (queue expiry, one grant bucket).
  - Deleting one stored file on purpose, to make a "lost file".
  - Running the hold sweep once while the worker was stopped.
  - Reading job ids. No API returns a batch's job id, so I read it in the
    shell and then called the real `billing/jobs/{id}` endpoint.
- **Top-ups:** manual credit grants through the super-admin API. Stripe
  wasn't used.
- **AI use:** real provider calls.
  - First database: 91 calls (27 assignment extractions, 20 answer
    extractions, 22 gradings, 11 grade write-ups, 11 student summaries), about
    865 display credits.
  - The rerun on the second database: about 5 more papers.
  - That's more than the ~25 papers the brief aimed for. Most of the extra
    was 19 small text extractions used to spend a wallet down for the
    low-balance emails.
- **Files:** tiny fake PDFs only.
  - Cloudinary first rejected the credentials in `.env`. The app fell back to
    sending files inline, as designed. The owner fixed the credentials, and
    the kept-file checks ran after that.
  - Cleanup is in the last section.

## Details

Credit numbers are raw credits (1,000 raw = 1 credit on screen).

1. **Estimate.**
   - Enough: SUFFICIENT, likely 234,783, up to 352,175, against 469,635.
   - Not enough: INSUFFICIENT, likely 234,657, against 120,013.
   - Both are "rough" estimates, because there were no graded papers like
     these yet.
   - 501 papers: refused with "You can get a cost estimate for up to 500
     papers at a time. Pick fewer papers, or choose all ungraded papers." 500
     is accepted.
   - The on-screen sentences (texts 1-4) are built by the website, not the
     API, so they weren't testable here.
2. **Full batch.**
   - 3 papers held 352,176 up front and released them as each finished.
   - Grading cost 65,583, plus 13,921 for the write-up and summary calls
     after grading.
   - The wallet went 469,635 → 390,131, exactly 79,504. Reserved went back
     to 0.
   - Kept files are deleted on success: confirmed by looking up the stored
     file after success (gone).
3. **Run out partway** (second teacher, 120,013).
   - 1 paper ran and 2 waited. The job read "waiting for credits", 2 left,
     with a 30-day expiry.
   - A grant of 160,000 restarted both waiting papers on its own, with the
     "started again" email. All 3 settled, and reserved went to 0.
4. **Expiry.**
   - Expiry set 47 hours ahead: the "expire in 2 days" email.
   - Expiry set into the past: the "stopped waiting" email.
   - Job EXPIRED, 0 charged. Both papers are retryable with "Credits ran out
     before any of the 2 items were finished."
5. **Cancel.** Cancel gave 200 with 2 cancelled. The job is CANCELLED, 0
   charged, the wallet unchanged, and no email.
6. **Late door.**
   - Two uploads were left waiting with the worker off. The backstop failed
     them, the wallet was emptied (a clock move on the grant), and the worker
     was restarted.
   - Both were refused with nothing charged, and "We couldn't process
     {file}" was sent for each.
7. **Retry.**
   - A refused upload, retried after a top-up, re-ran from its kept file
     without re-uploading. It succeeded, and the stored file was then
     deleted.
   - A file lost from storage: the retry fails with "We couldn't process this
     submission upload. Please check the file and try again, or contact
     support if this continues." The next retry is refused with "This item
     can't be retried as it is."
   - BUG-2, as above.
8. **Low balance.**
   - 25% email at 41 of 170 credits; 10% email at 29 of 330.
   - The wallet, the teacher's own profile and the super-admin user list all
     agree (spendable, reserved, low / very low).
   - The 0% email wasn't reached. A call that doesn't fit is refused before
     it charges anything, so a wallet never lands on exactly 0 in a test this
     small. Unit tests cover it.
   - Note: with small plans, the 10% line can sit below the smallest call's
     hold, so that email arrives only for bigger allocations.
9. **Other AI actions.**
   - Assignment from text: charged 13,768 exactly and released the rest.
   - Answer uploads and an assignment PDF upload: charged and released.
   - On 20 credits: refused with "There aren't enough AI credits to do this.
     Top up your credits, then try again." Nothing was held.
10. **Two batches at once.**
    - 3 papers needed 351,800 but the wallet had 339,779. Exactly 2 started;
      spendable never went below 105,216.
    - The third waited, then was restarted by the 5-minute scheduler.
11. **License teacher.**
    - The waiting email ends "Ask your school administrator for more credits
      to continue."
    - A grant restarted the work ("2 of the 2 waiting papers").
    - The license version of the 402 refusal wasn't seen: my test call fit.
12. **Students.**
    - Their submission list and detail have no job id and no kept-file date.
    - Another student's submission: "We couldn't find that submission. It may
      have been deleted."
    - A student upload works. The teacher sees the kept-file date and the
      student doesn't.
13. **Switch-off (on ef1080075).**
    - With 2 papers in flight, every process was switched to draining. The
      drain command printed DRAINED, and the papers finished and settled.
    - Flag off: an upload and a grading batch ran the old way, and a short
      wallet got the same refusal.
14. **Wording.** Seen in the app or emails and checked word for word against
    team/approved_wording_b1b_2026-11-09.md and approved_wording_2026-11-08.md:
    - #5 (individual and license closings), #6, #7, #8, #9, #10, #11, #12,
      the 500-paper message, and the late-door email: **all match**.
    - Not reachable through the API: #1-#4 (the estimate sentences), #14
      (labels) and #15 (job status line). The website builds those; the API
      supplies the numbers, which are right.
    - #13 wasn't reached.
    - **Approved but reading oddly**, flagged stale in NOTES §11.2, for the
      owner. What the app shows today:
      - Text 1, on expired *grading* papers (no file was ever uploaded):
        "Top up credits, then resume the remaining items. Your files are kept
        for 30 days, so you won't need to upload them again."
      - Text 3, the late-door email: "We couldn't process Elif_Kaya.pdf
        because your credits ran out while it was waiting. Nothing was
        charged for it. Your file is kept for 30 days. Top up your credits,
        then retry it. You won't need to upload it again." (True in this
        test: the file was kept.)
      - #13's last sentence, "Batches that run out will wait for 30 days.":
        not seen (0% not reached).
      - Also: the "waiting for credits" email says "Your credits ran out"
        even when the credits were only held by the teacher's other running
        batch (scenario 10).
15. **Logs.** Across both runs (web, worker, scheduler):
    - 0 server errors (HTTP 5xx), 0 tracebacks, 0 critical lines.
    - 30 lines "Error decoding JSON" come from older code that logs an error
      for every plain-text assignment (`ai_processor/services.py:1366`). Not
      B1b, but noisy.
    - 3 error lines were expected from the cancel and lost-file tests.

**Minor, for triage:**
- `billing/jobs` keeps showing "left 2, failed 0" after a cancel or an
  expiry.
- The sweep's summary line doesn't count items the backstop failed.
- A teacher with no plan shows balance status "out".

## BUG-3 rerun (staging b3c900aaf, DB2, 12 Nov)

The app was moved to b3c900aaf and restarted (no migrations in the change).
All steps went through the API as the teacher. The Django shell was used only
to read the item's hold, the job and the ledger self-check, and to read job
ids.

| Check | Result | What I saw |
|---|---|---|
| (a) Cancel an unstarted upload | PASS | The hold came back at once and the upload job closed. Nothing was charged, then or later. |
| (b) A normal upload is still charged right | PASS | Charged 12,146; the job's consumed figure is 12,146. |

**(a) Cancel an unstarted upload.**
- I stopped the worker so the upload couldn't start, then uploaded one paper
  (Gina_Park.pdf) to Biology Quiz 1.
  - Wallet: spendable 433,979 → 412,833, held 21,146.
  - The item was PENDING with its hold HELD, and the upload job was RESERVED.
    This is the BUG-3 starting point.
- `tasks/cancel-session` returned 200 ("cancelled_count": 1).
  - Wallet straight after: spendable 433,979, held 0. The hold was back at
    once.
  - The item was CANCELLED with its hold RETURNED.
  - The upload job was closed: FAILED, 0 consumed, "items: total 1, failed 1,
    left 0" (`billing/jobs/{id}`).
  - Ledger self-check: no problems.
- I restarted the worker, and it picked up the queued task. It saw the cancel
  and stopped before any AI call. The wallet stayed at 433,979 and the self-check
  stayed clean.
- The app deleted the paper's stored file at the cancel.

**(b) A normal upload is still charged right** (the guard against the fix
refunding a paper that succeeded).
- The first three tries failed for correct reasons, so they don't count as the
  guard. Each ran its AI extraction, and each charge was refunded:
  - Hugo_Mendez.pdf: Hugo isn't on the roster. The AI extraction ran, and its
    charge was refunded, as designed: an upload that fails before the
    submission is saved refunds its extraction (`metered_unit`, F1/S6d).
    Net charge 0.
  - Chen_Wei.pdf: "This assignment has already been graded, so it can no
    longer be submitted again." Net charge 0.
  - Dana_Levi.pdf: I added Dana to the course through the API. She had never
    signed in, so her enrollment stays PENDING until she accepts (as
    designed), and the upload said "Dana_Levi.pdf belongs to Dana Levi, who
    isn't enrolled in this course." Net charge 0.
- The guard itself: I made a copy of Biology Quiz 1 through the API (one
  assignment extraction, 13,631 charged), then uploaded Amara_Okafor.pdf to it
  with the worker running.
  - Wallet: 420,348 before; spendable 390,234 and held 30,114 while it ran;
    408,202 after, held 0. The wallet went down by 12,146.
  - The item was SUCCESS with its hold RETURNED, and the job was SETTLED with
    12,146 consumed (`billing/jobs/{id}`: consumed 12,146, unbilled 0,
    completed 1).
  - Consumed matches the wallet drop exactly. Ledger self-check: no problems.

**Minor, for triage (not bugs I'm stopping for):**
- An upload whose only paper was cancelled closes as "FAILED" in
  `billing/jobs`, with the cancel counted under "failed". There's no
  "cancelled" state there.
- The worker logs the cancel as an ERROR line ("raised unexpected:
  TaskCancelledError"). This also happened on DB1, before the fix.
- Chen's "already graded" refusal keeps its file for 30 days to retry, but a
  retry can't succeed for that reason. (The roster-miss and not-enrolled
  refusals deleted their files at once.)

## BUG-2 rerun (staging 30926b024, DB2, 12 Nov)

The app was moved to 30926b024, the two new migrations were run (students
0042 purge_reason, billing 0086), and the servers were restarted. All steps
went through the API. The Django shell was used only to read the kept-file
rows, to move one kept-until date into the past and queue the app's own
hourly purge task, and to read the self-check.

| Check | Result | What I saw |
|---|---|---|
| (a) Cancel an upload, then retry | PASS | 409 "This item can't be retried as it is." No "30 days". |
| (b) Lost file | PASS, unchanged | The retry fails with the usual message, and the next retry gets the plain 409. No "30 days". |
| (c) A real 30-day purge | PASS | 409 "This upload can't be retried because its file was deleted after 30 days. Upload the file again." |

**(a) Cancel, then retry.**
- I stopped the worker, uploaded Bruno_Silva.pdf, cancelled the session
  (200), and retried at once: 409 "This item can't be retried as it is."
  (resolution replace_file).
- I restarted the worker. It saw the cancel and discarded the kept file: the
  row reads purged, reason "discarded", and the file is gone.
- I retried again, after the discard. This is the exact BUG-2 case: 409
  "This item can't be retried as it is.", with no "30 days". Before the fix
  this said "…its file was deleted after 30 days…".

**(b) Lost file (unchanged path).**
- teacher2 set up a course and quiz through the API, with small manual grants
  so the paper wouldn't fit. Amara's upload failed INSUFFICIENT_CREDITS,
  nothing was charged, and the file was kept for retry.
- I deleted that one Cloudinary object by its exact key and confirmed it gone
  with a lookup. The row was left as it was (LIVE).
- Top-up (API grant), then retry 1: 202, then FAILURE "We couldn't process
  this submission upload. Please check the file and try again, or contact
  support if this continues."
- Retry 2: 409 "This item can't be retried as it is." (replace_file), no
  "30 days".
- Nothing was charged for the lost file, the same as on DB1.

**(c) A real 30-day purge** (shell used to move the date).
- Chen's refused paper still had its kept file (LIVE until 12 Dec). In the
  shell I set its kept-until one minute into the past and queued the app's
  own `purge_kept_uploads` task, which the worker ran: "purged 1, failed 0".
  - The row reads purged, reason "lifetime".
  - The app deleted the file from Cloudinary itself.
- Retry through the API: 409 "This upload can't be retried because its file
  was deleted after 30 days. Upload the file again."

**At the end:** ledger self-check with no problems, and no job left reserved,
running or waiting.

**Minor, for triage:** the web log shows "Error decoding JSON: Expecting
value…" around the assignment extraction (twice in this rerun, 28 times on
DB1). Both assignment creations behaved correctly (one refused with 402, one
created).

## Cleanup

**Databases.**
- The first database (DB1) was dropped through Django, the same way it was
  created, when I moved to ef1080075.
- The second database (DB2) was dropped the same way after the BUG-2 rerun.
  It had no open connections, and a check afterwards found it gone.

**Cloudinary.** Only exact keys were deleted, one at a time: no listing, no
prefix delete. Every key was then looked up to confirm it's gone.

- **DB1 plus my connection probe: 12 created, 12 confirmed gone.**
  - 9 were deleted by the app itself (on success, cancel or refusal).
  - 1 was deleted by me to stage the lost-file test.
  - 2 were deleted by me at cleanup: the S8 refused upload's file, which was
    correctly still kept for retry, and the probe.
  - Keys:
    - 1dea8efa7091412b9ea5edfd0315b737
    - 61e2330597ba448cad63455db73a9b89
    - 97df05b083c64467949fdc8f5382dfb7
    - e859625c6b16438e94b24aa6edf7ad69
    - 71926a2da5ba426bbec70f97fd7c81ad
    - 5373368d4b7142ccbae599e1846eee8d
    - 16a387e225024934adc27ba5307b1506
    - d3249b1757894386a461260ab4156fb4
    - 0e0f8a3c8c0943bb96ce7a08e208cdff
    - 8e8461d8d9e145cc90594c3a0655613b
    - 00f49c208fd9471f9d65e937ae28387c
    - 28b96a973a574018ba663fdca4df9a3e (the probe)

    All are under `kept_uploads/`.
  - The logs don't print storage keys, so the list comes from the database
    table plus the probe.
  - The 3 uploads tried before the credentials were fixed created nothing.
- **DB2: 10 created, 10 confirmed gone.**
  - 9 were deleted by the app itself (on success, cancel, refusal or discard,
    and Chen's by the 30-day purge in BUG-2 check (c)). That's why I deleted
    nothing by hand at the end: Chen's file was already gone.
  - 1 was deleted by me, by its exact key, to stage the lost-file test
    (BUG-2 check (b)).
  - Keys:
    - 47754763fcfb4fb39fbc52b2507f33a7
    - 37f65ad18e704211b350045a4fb0a31a
    - fa39b5580b784c379d8e1f485b97798e
    - 0011f54b4e6840c2b49e54a1938f2aa4 (BUG-3 rerun, cancelled)
    - ec8b2d561c8d4c96b8a7b01fd6e9f99b (Hugo, not on the roster)
    - d617345ff4ab409a802455052fb060be (Chen, already graded: removed by the 30-day purge)
    - 0657e88c4a8f4f71ac9e8b73ecd75417 (Dana, not enrolled)
    - ad9dbcbda02245deaf018ad580d91313 (Amara, the guard)
    - c58ef959abdc498fa59da1b64345f8d3 (BUG-2 (a), cancelled and discarded)
    - d185f14103fa4abab5d1d8d3973cda05 (BUG-2 (b), deleted by me for the
      lost-file test)
  - All are under `kept_uploads/`. Each was looked up at the end and was
    gone.

**Total: 22 created, 22 confirmed gone.**

**Also done:**
- The web server, worker, scheduler and private Redis are stopped. No
  hands-on process is left.
- The Python environment b1b_handson_env is deleted. Nothing used it.
- The local logs and the settings override were moved out of the repo and
  never committed.

## AI use, stated plainly

The brief asked for about 25 papers. I used more:
- about 20 answer extractions and about 11 gradings on DB1, and about 5 more
  papers on DB2 before the BUG-3 rerun;
- the BUG-3 rerun on DB2: 4 answer extractions (Hugo, Chen, Dana and Amara;
  only Amara's was charged, as the other three were refused and refunded) and
  1 assignment extraction;
- the BUG-2 rerun on DB2: 1 assignment extraction (teacher2's quiz) and no
  paper calls;
- 91 provider calls on DB1 in total, about 865 display credits;
- of those, 19 small text-only extractions, used to spend wallets down to the
  25% and 10% lines.
