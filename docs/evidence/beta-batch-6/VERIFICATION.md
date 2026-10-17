# Verification: beta-batch-6, Gate 1 (batch level) @ 28ee42cd

**Verifier:** Verification Engineer (1a). **Integrator:** Integration & Release (0b).
**Base:** beta = origin/beta `74bfc8d3` (bundle 5). **Tip:** `28ee42cd`; the code tip is `78a099c4` (the H-99 merge). `fd2bcdbf` adds only the backlog rows and `28ee42cd` only 0b's full-run record (both docs). **Date:** 2026-10-02.

**Verdict: VERIFIED.** Bundle 6 is exactly its four verified items, merged cleanly, and its one strict full run passed on the exact final code tree. Nothing is required before the push. The founder still has to confirm that specific push, and the package should carry the notes in N1 to N4.

I ran no test for this gate (rule 15). The checks are git and record checks, plus my own reading of 0b's full-run log.

The bundle closed by the SM's cut rule when H-99 was verified and merged. H-91, H-97 and H-89 are not in it; they move to bundle 7.

## What I checked (git and records)
| Check | Result |
|---|---|
| Ancestry | beta `74bfc8d3` is an ancestor of `28ee42cd`. |
| Evil merges | All **4** first-parent merges in `74bfc8d3..28ee42cd` are **clean** (`git merge-tree --write-tree <p1> <p2>` equals the merge's tree): `93cb8648`, `ac2ec323`, `76cc9b97`, `78a099c4`. |
| Direct commits | Two, both docs only: `fd2bcdbf` (`docs/HARDENING_BACKLOG.md`) and `28ee42cd` (`docs/evidence/beta-batch-6/FULL_SUITE.md`). |
| Each item is its verified commit plus docs only | See the table below. Each verified commit is an ancestor of what was merged, with **0** non-docs commits after it on the item's own line. Every base-update merge inside an item is clean and brings in batch commits only. |
| My records in the tree, byte for byte | Both match my copies: H-88/H-93/H-81 (`docs/evidence/h88-licence-grant-anchor/VERIFICATION.md`) and H-85 (`docs/evidence/h85-neutral-subscription-refusal/VERIFICATION.md`, with the delta section). |
| v2's records | Present in the tree for H-94 and H-99. I did not byte-compare them; v2 has offered to. |
| Migrations | **One:** `billing/0073_schoolcreditallocation_grant_anchor_at.py`, a single `AddField` (`SchoolCreditAllocation.grant_anchor_at`, nullable, no default), depending on `0072`; the only leaf. 0b's run reports `makemigrations`: no changes detected. |
| Models | `billing/models.py` gains that one field. Nothing else. |
| Settings, beat schedule, requirements | **None.** `AutoGrader/settings.py` is identical to beta's; no `CELERY_BEAT_SCHEDULE` entry; `requirements.txt` unchanged. |
| After the run tip | `fd2bcdbf..28ee42cd` changes the full-run record only, and `78a099c4..28ee42cd` differs in **0** non-docs files. So the strict run's tree equals the push tip's code. |

## The four items
| Item | Verifier, verdict | Verified at | Merged tip | Batch merge |
|---|---|---|---|---|
| H-94 Redis hygiene test race (test only) | v2, VERIFIED-WITH-NOTES | `5eb85fbc` | `09ead6b0` | `93cb8648` |
| H-88 / H-93 / H-81: licence refreshes on the anchor day, the consumption window, owed-grant detection | 1a, VERIFIED-WITH-NOTES | `206fbd84` | `5bcf6386` | `ac2ec323` |
| H-85 neutral subscription refusal | 1a, VERIFIED; the delta on the batch base VERIFIED | `ef67cee3`; delta `eabc8cab` | `ec404bf2` | `76cc9b97` |
| H-99 placeholder email (HIGH) | v2, VERIFIED-WITH-NOTES | `b910034e` | `78cb3a1c` | `78a099c4` |

## Gate 10 (0b's strict full run; one per bundle, rule 15, not repeated)
| Tip | Result |
|---|---|
| `fd2bcdbf` (the final code tree) | **Ran 5601 tests, OK (skipped=28)**, exit 0, 0 FAIL, 0 ERROR. 12G, `--parallel 4`, `--verbosity 2`; mypy and `makemigrations --check` clean first (0b's `docs/evidence/beta-batch-6/FULL_SUITE.md`, `28ee42cd`) |

I read the full log myself (`~/Documents/Projects/GAP-0b-runs/strict_b6.log`, 7,631,561 bytes; its sha256 prefix `30f1d9874b779696` matches the record):
- **The result line** is `Ran 5601 tests in 554.728s`, `OK (skipped=28)`. No test result line ends in FAIL or ERROR, there is no FAIL or ERROR header, and no expected failure or unexpected success.
- **The count checks out exactly:** bundle 5 ran 5521. The range adds 80 `def test_` methods and removes none. 5521 + 80 = **5601**. The skip count is still 28.
- **Per-app counts** (my own count of the log's test lines) match 0b's record: ai_processor 817, AutoGrader 469, assignments 628, billing 2094, classrooms 401, dashboard 270, students 268, users 654. They sum to 5601, and no test belongs to any other app.
- **Where the 80 are:** billing +62 (`test_licence_grant_anchor` 25, `test_allocation_anchor` 16, `test_owed_grant_detection` 12, `test_neutral_subscription_refusal` 9), classrooms +16 (`tests_h99_placeholder_email`), AutoGrader +2 (H-94). Each of those modules ran in full.
- **The apps no item's own regression ran on the combined tree.** H-99's regression covered classrooms, users, dashboard and AutoGrader (SM ruling), and H-85's base update onto H-88 was gated on 23 billing modules without a whole-app run (SM ruling). This run is the first of the whole billing app (2094), students (268) and assignments (628) on the combined bundle; all three ran in full with no failure or error.
- **It was the only strict run of bundle 6, on the final code tree, and it passed**, with no suspend this time.

## Notes (not blocking)
**N1 (rollback to beta 74bfc8d3, code only; rule 11).**
- The one new column is nullable, so **the rollback section needs no SET DEFAULT statement, and should say so.** Old code never writes the column; inserts leave it NULL.
- The column can stay after a rollback. If the code is later rolled forward again, rows the old code renewed or re-enrolled carry a stale anchor; each heals at its next refresh, with no extra refresh and no manual step (my H-88 verification, finding F1, now fixed).
- No beat entry was added, so there is no `PeriodicTask` row to disable.
- H-99 needs no data step: accounts created meanwhile keep their longer placeholder addresses, which the old code handles (it only checks the domain).
- After a rollback the old behaviours return: 13 licence refreshes a year for 29th–31st enrolments (H-88), the window reopening only after a full calendar month (H-93), no owed-grant ERROR (H-81), the refusal that names the address and the subscription (H-85), and the placeholder address that can attach an existing account (H-99).
- This is my reading of the code, not a tested rollback.

**N2 (user-facing texts and the frontend).**
- **H-85:** a school admin adding a teacher who has an active individual subscription is told "This teacher can't be added to your school yet. Please ask them to contact support." No address, no mention of a subscription. The add-teachers response's per-teacher error carries it, and the API schema example shows it.
- **H-99:** no new wording. A student added with no email gets a longer generated address. **The API no longer accepts an `@student.local` address on add; it never showed one** (it returns `email: null` for these accounts). If five generated addresses in a row were taken, the add fails with the views' existing fallback sentence. The SM confirms with QA that no client sends such an address; only the backend was read.

**N3 (founder).**
- **H-99's detection query is a production action and needs approval:** `~/Documents/Projects/GAP-detect-shared-placeholder-students.sql`, read-only, ids and counts only: placeholder accounts enrolled in courses of more than one teacher. The fix stops new attaches and does not repair records already merged. The query cannot show two same-named pupils of ONE teacher merged into one record.
- **H-85:** the neutral sentence is used for this one refusal only, so someone who knows the product can still infer the cause. The founder saw the six-address table and kept H-85 as built.
- **H-88:** teachers enrolled on the 29th–31st now get 12 refreshes a year, not 13, on their own day. Rows older than the new column converge at their next refresh; there is no data migration.
- **H-99's two things examined and left for the founder** (in its evidence): the free-join rule for an account tied to no school, and how a student's schools are derived from their teachers' current schools.

**N4 (operations).**
- **H-81:** a renewal now logs one ERROR (ids only) for each monthly grant that came due in the ending cycle and was never made. It only detects; support credits the customer by hand. It does not report a subscription or licence that ends without renewing.
- **H-88:** after a scheduler outage, owed licence refreshes are caught up one per daily run, each with a WARNING.
- **Known edge (SM-accepted):** a teacher's last monthly point less than a day before the cycle end is not served; under a day is lost and the previous bucket's grace covers it.

**N5 (open rows; none blocks).**
- **H-98** (LOW-MEDIUM, d5): H-81 ignores any due time within 7 days of the cycle end, so a genuine anchored point left unserved by an outage running to the end is not reported and the renewal does not make it up.
- **H-100** (LOW): two unordered `.first()` calls (the roster name match; the session create's school pick).
- **H-102** (LOW): the direct-add view answers a save-time refusal with a 500 instead of a 400.
- **H-101** (MEDIUM) is an Epic A row, not a beta one.
- **Moved to bundle 7:** H-91 (ids-only logs everywhere), H-97 (the Redis sweep misses other databases), H-89 (the log scrubber).
- **Carried from the items:** H-94's residual race between two separate runs (v2's note 1); H-99's notes on the case-sensitive masking of placeholder addresses and the domain rule skipped for any `@student.local` address, both with the SM; H-99's merge-down note for Epic A (the epic's form of the hunks, and the `ROW_STAFF_EMAIL` code).

Working files: `~/Documents/Projects/GAP-1a-scratch/gate1-b6/` (`check.sh`, `check_28ee42cd.txt`).
