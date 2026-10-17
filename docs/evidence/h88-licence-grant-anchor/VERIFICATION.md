# Verification: H-88, H-93, H-81: licence refreshes on the anchor day, the consumption window, owed grants @ 206fbd84

**Verifier:** 1a. **Author:** d5. **Date:** 2026-10-02.
**Branch:** `task/h88-licence-grant-anchor` @ **206fbd84** (code tip `8ea91e21`), on beta `74bfc8d3`. Bundle 6; it carries billing migration `0073`.
- H-88: `ec7e1caa` (tests), `db287b54` (fix and migration), `a175c2a9` (more tests, the battery)
- H-93: `b03f5d7c` + `4d92609e` (the first window rule, now history), `117e4afb` (tests), `a26c8b5e` (the licence-point rule)
- H-81: `f800fa80` (tests), `5e533159` (the detection)
- From my pre-review: `fc4fa05a` (tests) and `cc22f6e2` (fix) for F1; `8ea91e21` (my late-renewal test at both sites)
- `84ec0ddc`, `206fbd84`: evidence (docs only)

The evidence is in `docs/evidence/h88-licence-grant-anchor/`.

I ran in 0b's slots from my detached scratch checkout at 206fbd84, with its own test DBs (`test_vf_h88`, mutants `test_vf_h88_mut`). The wrapper was rule 16's `systemd-inhibit` (idle, sleep and lid switch), the 6G scope with `MemorySwapMax=0`, `nice -n 10` and `timeout -k 60 1800`. Under rule 15 I cite d5's regression (billing + users + classrooms + dashboard + 9 guards, 3469 OK at 8ea91e21) and don't repeat it.

**Verdict: VERIFIED-WITH-NOTES.**
- The SM's focus points all hold (below). The finding of my pre-review (F1) is fixed and my observation (O1) is removed by the new window rule. My three mutants are killed.
- **N1 needs the SM's ruling but does not block:** a teacher's last monthly point that falls in the final days of a cycle can go unserved, and H-81 does not report it. It is a property of rules already in the branch, it predates nothing in production, and its size is small (below).

## The SM's focus points
| Point | Result |
|---|---|
| Already-drifted allocations get no second refresh in a period | **Holds.** X2: 15 legacy cases run under the old rule up to a deploy date, then the new code. Every one has 11 refreshes; the smallest gap between two refreshes is 28 days. |
| The anchor is stored at the first refresh | **Holds.** X2: every legacy row has its anchor stored, equal to the licence start. X2B: a re-enrolled legacy row (due on the 20th, off the licence's chain) stores the due time and stays on the 21st run each month, gaps of 30 days or more. |
| N catch-ups grant exactly N months and reopen the window once | **Holds.** X3: outages over 1, 2 and 3 anchors, two start dates. The catch-ups arrive on consecutive days, 12 monthly buckets and 12 × the allocation in all, the window reopened once during each catch-up, and one ids-only WARNING per caught-up refresh. |
| The window reopens at every refresh of a 31st chain | **Holds.** X4: 11 refreshes, 11 reopenings. |
| The window with mixed anchors (my O1, the licence-point rule) | **Holds.** X4B: four teachers on the 1st, 9th, 17th and 25th: 44 refreshes, the window reopened 11 times, once per licence month, on the licence's day. X4C: a licence on the 31st whose teachers are on the 9th and the 25th: 11 reopenings, each at the first refresh after the licence's point. |
| H-81 only detects; ids-only ERROR; no false report for a legacy drifted row | **Holds.** X5: nothing is logged for a chain served to the end (renewed 1 hour, 10 days and 40 days late), a mid-cycle enrolment, a legacy uncapped row and a legacy row drifted to the 28th. After an outage to the contract end, one ERROR with the right count (1 and 3), no address. Nothing is granted by the detection. |
| Migration 0073 nullable with a clean rollback | **Holds.** One `AddField`, nullable, no default, so rule 11 needs no DB default and old code ignores the column. Rule 15's model addendum is met: only billing and users code reads `SchoolCreditAllocation`, and d5's regression covers billing, users, classrooms and dashboard. |

## My pre-review findings
- **F1 (a stale stored anchor): fixed.** At 84ec0ddc a stored anchor was trusted even when the due time had been moved off its chain, which brought a second refresh within days (anchor 5 Jan, due 28 Mar → next 5 Apr). `cc22f6e2` applies the 7-day test to a stored anchor too, and the QA time-travel tool now clears the anchor with the due time.
  - X6, by a run: the same row now refreshes on 28 Mar, 28 Apr and 28 May, and stores 28 Mar as its anchor.
  - By the pure helpers (no run): a stored anchor on every day 1–31, with the due time moved by −20 to +20 days in 6-hour steps (49,910 cases). The smallest gap to the next refresh is 21 days, at exactly 7 days off the chain: one shortened period, as accepted for H-82. Otherwise 28 days or more.
  - The evidence's rollback note now says stale anchors heal at the next refresh, with no manual step.
- **O1 (the window reopening about every 24 days with mixed anchors): removed.** `a26c8b5e` reopens the window on the licence's own monthly points. The "month less 7 days" rule and its six-week window after an outage are gone.
- **The late-renewal gap: closed.** d5's control renewed a served chain one hour late, which could not tell `min(cycle end, now)` from `now`. My test (a renewal 20 days late) is adopted at both sites, with mutants O9 and O10.

## Evidence
| Check | Result |
|---|---|
| **Run** @ 206fbd84: my probes X1–X6 + `billing.tests.test_licence_grant_anchor` + `test_allocation_anchor` + `test_owed_grant_detection` | **62 tests OK** (175 s). My probes run the real refresh task at 03:00 on every day of the cycle, due or not. |
| **X1:** enrolment on days 1, 15, 28–31 at two times of day, leap-year starts, and three mid-cycle enrolments | **19 cases, 0 failing.** One refresh per anchor point, on the first run after it, none at or after the cycle end, no bucket born expired, the anchor stored as the enrolment moment. One case has an unserved tail: see N1. |
| **X5 extended** (the unserved-tail case added after 0b's question), with d5's detection module | **13 tests OK.** The renewal logs nothing for the unserved last point. |
| **My mutant Y4** (the window reopens on every refresh) | **KILLED** by three of my probes (X3, X4B, X4C) and three of d5's tests. Same idea as d5's W5. |
| **My mutant Y6** (the window's points counted from the teacher's anchor, not the licence's start) | **KILLED** by X4B, X4C and d5's `test_teachers_on_the_1st_and_the_25th_share_12_windows_a_year`. |
| **My mutant Y5** (the owed report measured to now) | **KILLED** by X5 and d5's adopted `test_a_late_renewal_of_a_chain_served_to_the_end_reports_nothing`. Same as d5's O9. |
| d5's gates (cited) | at 8ea91e21: repro 37 tests / 15 failures + 9 errors; (a) 14 modules 207 OK; (b) 31/31 mutants killed, restores sha-verified; (c) billing + users + classrooms + dashboard + 9 guards 3469 OK (skipped=8). |
| Hooks | `pre-commit run --from-ref 74bfc8d3 --to-ref 206fbd84` passes, and each of the 14 commits passes. |
| Merges | `git merge-tree --write-tree` against beta `74bfc8d3` is **clean**, and against H-85's `e8642276` (both edit `billing/license_service.py`, in different hunks). |
| Rule 14 | No MagicMock in the three new test modules. |

**Rule 17.** Every run had `PYTHONDONTWRITEBYTECODE=1`, and each mutant was applied with `python -B`. `billing/**/__pycache__` was deleted before each baseline, before each mutant and after each restore (the logs show 0 directories each time). Each restored `billing/license_service.py` matched the commit blob's sha256, and no tracked file was changed afterwards.

**Disclosure: one baseline run was discarded, for a fault in my own probe.** `runs/h88_206fbd84_PROBE_FAULT_1.log`: X1 required every chain to end exactly at the cycle end, and one case ends on the teacher's last point instead (N1). That is the code's intended behaviour, so I relaxed that one expectation, made the probe print the case, and re-ran. The results above are from the second run. After 0b's question I added the same case to X5 and ran X5 again with d5's detection module (`runs/h88_206fbd84_x5_with_tail.log`).

## Notes
**N1 (for the SM): a last monthly point in the final days of a cycle can go unserved, and H-81 is silent about it.**
- **What happens.** The refresh task serves a due time only while the licence's cycle has not ended. H-81 counts a due time as owed only when it is more than 7 days before the cycle end. So a teacher's last point that falls inside the final 7 days and is not served is not reported either.
- **Is the silence ruled?** The 7-day rule exists by the SM's ruling against false reports for legacy rows drifted near the end. Applying it to a genuine anchored point is my reading of the code, not a ruling.
- **Case A, no outage.** The point is less than a day before the end and no daily run falls between the two. My X1 case: a licence from 1 Jan 01:00, a teacher enrolled 31 Mar 14:00; the last point is 31 Dec 14:00, 11 hours before the end; the 03:00 run comes too early that day and too late the next.
  - Who: only a teacher enrolled mid-cycle within the 24 hours before the licence's own day of the month; at most about 1 in 30 mid-cycle enrolments. A teacher enrolled at the licence's start is not affected: their last point is the cycle end itself.
  - How often: one point per cycle for such a teacher.
  - What is lost: less than a day. The previous bucket's grace runs to the cycle end (X1 shows it expiring at the end), and the renewal then grants every teacher a fresh month. The teacher in my case got 9 grants for 9 months and 11 hours.
- **Case B, an outage over the cycle's last days.** A teacher whose last point falls anywhere in the final 7 days (about a quarter of mid-cycle enrolments), with the scheduler down from that point to the end. One grant per such teacher is unserved and unreported. The renewal does not make it up: it restarts the month and overwrites the due time.
- **Suggested:** accept case A as it stands. For case B, either accept it (it needs an outage at exactly the cycle's end) or have H-81 report a genuine anchored point inside the 7 days while still ignoring a drifted one; the stored anchor now makes the two distinguishable. A backlog row either way.

**N2 (rollback, for the package).** After a code-only rollback the old code ignores the anchor column. On a roll-forward, rows the old code renewed or re-enrolled carry a stale anchor; each heals at its next refresh (F1's fix), with no extra refresh and no manual step.

**N3 (carried, accepted by the SM before).** H-81 only detects, and does not report a subscription or licence that ends without renewing.

Logs: `runs/h88_206fbd84.log`, `runs/h88_206fbd84_x5_with_tail.log`, `runs/h88_mutant_Y4_206fbd84.log`, `runs/h88_mutant_Y6_206fbd84.log`, `runs/h88_mutant_Y5_206fbd84.log`, `runs/h88_206fbd84_PROBE_FAULT_1.log` (discarded, kept). Probe: `h88_probe_test_vf1a_h88_probe.py`. Mutants: `h88_mutant_Y4.py`, `h88_mutant_Y5.py`, `h88_mutant_Y6.py`.
