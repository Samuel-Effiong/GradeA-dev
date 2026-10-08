# Verification: H-98: an unserved refresh in a cycle's last week is reported @ d78ef385

**Verifier:** 1a. **Author:** d5. **Date:** 2026-10-05.
**Branch:** `task/h98-owed-refresh-last-week` @ **d78ef385** (code tip `8513ae0d`), on `task/beta-batch-7` `27b0d2e0`. For the bundle after bundle 7. Detection only: one more ERROR log line at a licence's renewal. No migration, no model change, no settings change, nothing granted.
- `ff2edee7` (tests), `19a15de7` (the change, and the mutation runner under `docs/`)
- `8513ae0d`: the base update onto `27b0d2e0` (d5's merge, on 0b's instruction); `7ab45f1e`: evidence (docs only)
- `9e211ca4`, `d78ef385`: the mutation runner in rule 18 form, the battery again, the evidence corrected (docs only)

The evidence is in `docs/evidence/h98-owed-refresh-last-week/`.

**I ran at `7ab45f1e`** in 0b's slot, from my detached scratch checkout, with its own test DBs (`test_vf_h98`, the mutant on `test_vf_h98_mut`). The wrapper was rule 16's `systemd-inhibit` (idle, sleep and lid switch), the 6G scope with `MemorySwapMax=0`, `nice -n 10` and `timeout -k 60 1800`; serial; under rule 18 the output went straight to a file and stdin was `/dev/null`. **The delta to `d78ef385` I checked by reading:** it touches the evidence folder only. Under rule 15 I cite d5's regression (billing at `--parallel 2`, 2109 OK, at `8513ae0d`) and don't repeat it.

**Verdict: VERIFIED-WITH-NOTES.** The change does what the evidence says, on real rows as well as on the hand-built ones of its own tests. Nothing is required before the merge. N1 is the one note an operator should know.

## What H-98 changes
At a licence's renewal, H-81 logs each monthly refresh that came due in the ending cycle and was never made. It ignored every due time in the cycle's last 7 days, so that a row drifted by the old chain is not reported. H-98 counts one there when three things hold: the anchor in use is the allocation's stored one, the due time is exactly a point of it, and it came due at least one full day before the end.

## What I checked by running (at 7ab45f1e)
H-98's own new tests use hand-built rows and the pure function. My probes drive **real rows**: a teacher enrolled through the service in the middle of a cycle, refreshed by the real daily task on a set clock, and renewed by the real renewals.

| Probe | Result |
|---|---|
| **W1: an outage that runs to the cycle's end.** Three teachers, anchored on the 27th, on the 31st (so the last point is 28 February, clamped) and on the 26th with the outage starting two months earlier. | **Reported: owed 1, 1 and 3**, by the renewal task, each line with the licence's, the allocation's and the user's id and no address. Before the renewal, the stored anchor and the due time read back from the database were exactly on one chain after eight or ten real refreshes. That exactness is what the new rule depends on, and no test of the author's reads it from real rows. |
| **W2: the last point's distance from the end, swept.** Nine distances from 8 days to 6 hours, each with Beat healthy and with an outage from that point on: 18 cases. | **Healthy: never a line.** Outage: one line at 8 days, 7 days + 1 h, 7 days, 7 days - 1 h, 3 days, 1 day + 1 h and 1 day + 1 min; none at 1 day - 1 min and at 6 h. So the count is continuous across the 7-day line where H-81's rule hands over to H-98's, and the one-day line is where the evidence says. |
| **W4: an offline renewal before the cycle's end** (the count then runs to the renewal's moment, not to the end). | A point still an hour in the future: no line. Unserved and due 23 hours before the renewal: no line. Unserved and due 25 hours before: **one line**. Served, renewed 25 hours after: no line. |
| **W5 (the author's suggestion, on a real row).** | A due time moved to two hours off its point: no line. A stored anchor on another rhythm than the due time: no line. |
| **W3: the margin against a late run.** | See N1. |

## Evidence
| Check | Result |
|---|---|
| **Run** @ 7ab45f1e: my probes + `billing.tests.test_allocation_anchor` + `billing.tests.test_owed_grant_detection` | **68 tests OK** (62 s): my 28, the author's 28 and H-81's 12. |
| **My mutant Y11** (the last-week point counted only when nothing earlier was owed; not in the author's battery) | **KILLED** (`Ran 68 tests`, failures=2), by exactly the two tests I wrote down before the run (`h98_expected_kills_Y11.txt`): the author's `test_an_outage_that_ran_to_the_end_counts_the_last_point_too` and my W1's third case. |
| d5's gates (cited) | Repro red at `ff2edee7`; 34 labels with the repo-wide guards, 470 OK; billing 2109 OK; the battery 11 of 11, every failing set the one written down first. |
| The battery ran twice, and the second is the gate | The first battery's inner runs were collected through a pipe; d5 raised it themselves and the SM ruled a re-run. I read the runner at `d78ef385`: each mutant's run writes to a file with stdin from the null device, and a kill needs the run's own "Ran" line and named failing tests. The second battery's log shows 11 of 11 with the restore verified, the same result as the first. I see no reason to doubt the first either: one billing module, no browser driver, every run ended with its result line. |
| The battery is on the final test module | `billing/tests/test_allocation_anchor.py` has not changed since `ff2edee7` (rule 17's addendum). |
| No code change after the code tip | `git diff 8513ae0d d78ef385` touches the evidence folder only. |
| Hooks | `pre-commit run --from-ref 27b0d2e0 --to-ref d78ef385` passes (the range; I did not run each commit separately). |
| Merges | `git merge-tree --write-tree` of `d78ef385` is **clean** against beta `63c3da22` (bundle 7, pushed). |
| Rule 14 | No MagicMock in the new tests or in my probe. |

**The repro is weak, as the author says.** At `ff2edee7` all but one of its failures are one `TypeError` (the new argument does not exist yet). The behaviour rests on the one failure through the real caller and on the tests at the tip; my probes add the real rows.

**Rule 17.** Both runs had `PYTHONDONTWRITEBYTECODE=1`, and the mutant was applied with `python -B`. `__pycache__` under `AutoGrader/` and `billing/` was deleted before the baseline, before the mutant and after the restore (the logs show 0 directories each time). The restored `billing/refresh_timing.py` matched the commit blob's sha256, and no tracked file was changed afterwards.

**The credential check on my own files** (the widened form, values not printed): the record, the probe, the mutant, the expected-kills note and the two logs hold no URL with anything in the password position, no assignment form and no percent-encoded form. My probe creates no account of its own; it uses the author's test base class.

## Notes
**N1 (the one-day margin has no slack for a daily run that starts late; SM ruling: a note, no code change, no new row).**
- With every run on time the margin is exact: a point due at least a day before the end always has a run that serves it (W2).
- The daily task does not serve a licence whose cycle ends within the run's 5-minute tolerance. So when a cycle ends just after 03:05 UTC and a teacher's last point falls due between 03:05 and that time on the day before, only the last run can serve the point, and only if it starts on time.
- **W3 shows it** with the real task and the real renewal: a cycle ending at 03:05:30 and a point due a day and ten seconds earlier. The last run on time: served, no line. The last run 45 seconds late: not served, and the renewal logs "owed 1" at ERROR although Beat was never down.
- **For an operator: an "owed 1" line for a licence whose cycle ended just after 03:05 UTC, with Beat healthy, means the last daily run started late, not that there was an outage.** The line is still true: the refresh was due and was not made.
- It needs three things at once: the cycle starting within seconds or minutes after 03:05 UTC, the teacher enrolled in that same narrow window of the day, and on the calendar day before the cycle's day.

**N2 (what stays unreported, by design; in the evidence).** A point due in the last 24 hours before the end (H-117, closed by the SM as by design); a drifted row, near a point and not on it; a row with no stored anchor, until its first refresh stores one. H-98, like H-81, reports and does not make the grant up.

**N3 (what the count means in two cases I read, not ran).**
- An active allocation of a licence that was inactive for part of the cycle is not refreshed while it is inactive (the task's filter), so its unserved points would be reported at the next renewal. That is H-81's behaviour, unchanged; H-98 extends it into the last week.
- The licence admin's own allocation is counted like a teacher's. In my probes the licence has no such row.

**N4 (rollback).** Code only; no step. The line simply stops being logged for the last week.

Logs: `runs/h98_7ab45f1e.log`, `runs/h98_mutant_Y11_7ab45f1e.log`. Probe: `h98_probe_test_vf1a_h98_probe.py`. Mutant: `h98_mutant_Y11.py`. Written before the run: `h98_expected_kills_Y11.txt`.
