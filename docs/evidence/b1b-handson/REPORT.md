# B1b hands-on test: results

Brief 7, 11-12 Nov 2026. The real app ran on this machine with the new credit
behavior ON. I acted as a teacher, a school-license teacher, a school admin, a
super admin and students, using the same API calls the website makes, with
real logins and real AI calls. Code: staging a0478508e, then staging
ef1080075 (the door-close fix) for the reruns.

## Summary

| # | Situation | Result | In one line |
|---|---|---|---|
| 1 | Cost estimate | PASS | Enough, and not enough, are judged right. Over 500 papers gets the owner's exact message. |
| 2 | Full batch with enough credits | PASS | Credits are held during the run and released after, and the balance is exact to the credit. |
| 3 | Credits run out partway | PASS | Finished papers stay graded, the rest wait, and a top-up restarts them by itself. |
| 4 | Waiting work expires | PASS | Warning first, then expiry. Nothing charged, and both emails word for word. |
| 5 | Cancel waiting work | PASS | Cancelled, nothing charged, no email. |
| 6 | Late door | PASS | Refused, nothing charged, the late-door email sent. |
| 7 | Retry from the kept file | PASS, with BUG-2 | The retry works without re-uploading. A cancelled upload's retry message is wrong (BUG-2). |
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
- **BUG-2 (wording, being fixed):** retrying an upload that the teacher
  cancelled says "its file was deleted after 30 days". The file was deleted
  seconds earlier, by the cancel.
- **BUG-3 (money, being fixed):** cancelling an upload that hasn't started
  yet leaves its credit hold in place, so the teacher's spendable credits stay
  21,146 lower with nothing running. Only the 36-hour backstop might ever
  return them, and the books' own self-check doesn't flag it.

The BUG-2 and BUG-3 checks are to be rerun when their fixes land.

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

## Pending rerun

- BUG-2 (the cancelled-upload retry message) and BUG-3 (the cancelled-upload
  hold) get rerun on the second database once their fixes land. The results
  will be added here.

## Cleanup

**Databases.**
- The first database (DB1) was dropped through Django, the same way it was
  created, when I moved to ef1080075.
- The second database (DB2) is kept until the BUG-2 and BUG-3 reruns are
  done, then dropped the same way.

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
- **DB2 so far: 3 created (all deleted by the app on success).**
  - Keys:
    - 47754763fcfb4fb39fbc52b2507f33a7
    - 37f65ad18e704211b350045a4fb0a31a
    - fa39b5580b784c379d8e1f485b97798e
  - They'll be confirmed with the same lookup at the final cleanup, together
    with any keys from the reruns.

**Still to do at the end:**
- Stop the web server, worker, scheduler and private Redis.
- Drop DB2.
- Finish the Cloudinary check for DB2.
- Delete the Python environment b1b_handson_env.

## AI use, stated plainly

The brief asked for about 25 papers. I used more:
- about 20 answer extractions and about 11 gradings on DB1, and about 5 more
  papers on DB2;
- 91 provider calls on DB1 in total, about 865 display credits;
- of those, 19 small text-only extractions, used to spend wallets down to the
  25% and 10% lines.
