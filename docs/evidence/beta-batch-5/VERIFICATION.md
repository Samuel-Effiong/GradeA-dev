# Verification: beta-batch-5, Gate 1 (batch level) @ 0007cb0b

**Verifier:** Verification Engineer (1a). **Integrator:** Integration & Release (0b).
**Base:** beta = origin/beta `67a06817` (bundle 4). **Tip:** `0007cb0b`; the code tip is `5d977d0c` (the H2 merge). `058a9507` adds only the backlog rows and `0007cb0b` only 0b's full-run record (both docs). **Date:** 2026-10-02.

**Verdict: VERIFIED.** Bundle 5 is exactly its 13 verified items, merged cleanly, and its one strict full run passed on the exact final code tree. Nothing is required before the push. The founder still has to confirm that specific push, and the package should carry the notes in N1 to N4.

I ran no test for this gate (rule 15). The checks are git and record checks, plus my own reading of 0b's full-run log.

## What I checked (git and records)
| Check | Result |
|---|---|
| Ancestry | beta `67a06817` is an ancestor of `0007cb0b`. |
| Evil merges | All **13** first-parent merges in `67a06817..0007cb0b` are **clean** (`git merge-tree --write-tree <p1> <p2>` equals the merge's tree): `be4ceee2`, `d6bc400b`, `4cdd5ac1`, `c4ac9e08`, `499a3950`, `fc9a830f`, `138c28a8`, `d97b7e7c`, `6b627cc4`, `83fe58ca`, `f4e6e5d3`, `489c6784`, `5d977d0c`. |
| Direct commits | Two, both docs only: `058a9507` (`docs/HARDENING_BACKLOG.md`) and `0007cb0b` (`docs/evidence/beta-batch-5/FULL_SUITE.md`). |
| Each item is its verified commit plus docs only | See the table below. Each verified commit is an ancestor of what was merged, with **0** non-docs commits after it on the item's own line. Every base-update merge inside an item is clean and brings in batch commits only. |
| My records in the tree, byte for byte | All seven match my copies: H-78, H-65, H-76, H-66, H-82, H-80/H-86 (each `docs/evidence/<item>/VERIFICATION.md`) and H-55 (`docs/evidence/h55-token-epoch-pin/VERIFICATION_h55_token_epoch_pin.md`). |
| v2's records | Present in the tree for H-73, H-71, H-60/H-57, H1 and H2. I did not byte-compare them; 0b reports v2 confirmed each. |
| Migrations, models | **None** in the range: no file under any `migrations/`, no `models.py`. |
| Settings | **One change:** H2's `ENABLE_GRADING_BENCHMARK = env.bool("ENABLE_GRADING_BENCHMARK", default=False)` (7 added lines in `AutoGrader/settings.py`; nothing else moved). See N2. |
| Beat schedule, requirements | No new `CELERY_BEAT_SCHEDULE` entry; `requirements.txt` unchanged. |
| Management commands | `billing/management/commands/backfill.py` is removed (H1: it is now `scripts/one_off_backfill_stripe_schedules.py`); `grading_benchmark.py` gains H2's guard. |
| After the run tip | `058a9507..0007cb0b` changes the full-run record only, and `5d977d0c..0007cb0b` differs in **0** non-docs files. So the strict run's tree equals the push tip's code. |

## The 13 items
| Item | Verifier, verdict | Verified at | Merged tip | Batch merge |
|---|---|---|---|---|
| H-78 other school first | 1a, VERIFIED-WITH-NOTES; the fold VERIFIED | `2d94c43`; fold `560eced` | `b9e4ccb` | `be4ceee2` |
| H-65 beat locks | 1a, VERIFIED; N1 VERIFIED | `51fbb0e`; N1 `6cfebed` | `e3d7751` | `d6bc400b` |
| H-73 raw Redis client guard (test only) | v2, VERIFIED | `8bab946` | `9b9f845e` | `4cdd5ac1` |
| H-76 plan-change bucket | 1a, VERIFIED; N1 VERIFIED | `c46fdbf`; N1 `aa174a33` | `78215b89` | `c4ac9e08` |
| H-66 overage needs a payment intent | 1a, VERIFIED | `cf7df763` | `9e990e89` | `499a3950` |
| H-71 student-add role | v2, VERIFIED | `3982284` | `9724cb32` | `fc9a830f` |
| H-55 token epoch pin (test only) | 1a, VERIFIED | `5fd88ff` | `c5326c30` | `138c28a8` |
| H-69 command audit survey (docs only) | no verification needed | `8a4f4174` | `8a4f4174` | `d97b7e7c` |
| H-60/H-57 licence routes | v2, VERIFIED (after VERIFIED-WITH-NOTES) | `9153cf1f` | `71169778` | `6b627cc4` |
| H-82 annual grant anchor | 1a, VERIFIED | `45fb442c` | `c0f7c39b` | `83fe58ca` |
| H1 backfill script move | v2, VERIFIED-WITH-NOTES | `daf7179b` | `8d528229` | `f4e6e5d3` |
| H-80/H-86 ids-only logs | 1a, VERIFIED | `84c17542` | `14ba2970` | `489c6784` |
| H2 grading benchmark guard | v2, VERIFIED-WITH-NOTES | `9fb597c4` | `a2ff0cca` | `5d977d0c` |

## Gate 10 (0b's strict full run; one per bundle, rule 15, not repeated)
| Tip | Result |
|---|---|
| `058a9507` (the final code tree) | **Ran 5521 tests, OK (skipped=28)**, exit 0, 0 FAIL, 0 ERROR. 12G, `--parallel 4`, `--verbosity 2` (0b's `docs/evidence/beta-batch-5/FULL_SUITE.md`, `0007cb0b`) |

I read the full log myself (`~/Documents/Projects/GAP-0b-runs/strict_b5.log`, 7,448,838 bytes; its sha256 prefix `b271e42895550093` matches the record):
- **The result line** is `Ran 5521 tests in 1002.908s`, `OK (skipped=28)`. No test result line ends in FAIL or ERROR, and there is no FAIL or ERROR header.
- **The count checks out exactly:** bundle 4 ran 5382. The range adds 139 `def test_` methods and removes none. 5382 + 139 = **5521**. The skip count is bundle 4's 28.
- **Per-app counts** (my own count of the log's test lines) match 0b's record: ai_processor 817, AutoGrader 467, assignments 628, billing 2032, classrooms 385, dashboard 270, students 268, users 654. They sum to 5521, and no test belongs to any other app.
- **H2's hard gate (v2's note 1, the SM's ruling).** H2 had no green ai_processor + AutoGrader regression on its final code. In this run both apps ran in full (817 and 467) with no failure or error. H2's own 11 guard tests ran, and so did the 7 `CommandHistoryIntegrationTest` tests that errored in H2's earlier red run. The 10 skips in those two apps are in `tests_answer_benchmark_live` (2), `tests_real_chunked_extraction` (2), `tests_real_superadmin_unmetered` (1), `tests_ssrf_guard` (1), `tests_redis_test_isolation` (3) and `tests_beat_health` (1); none is in a module H2 touches, and the bundle's total of 28 is unchanged from bundle 4.
- **The wall clock** (2648 s against 1003 s of tests) includes two laptop suspends with the lid closed, which 0b cites from `journalctl`. The run was frozen and resumed, not hung, and ended inside its limit. No test failed or errored.
- **It was the only strict run of bundle 5, on the final code tree, and it passed.**

## Notes (not blocking)
**N1 (rollback to beta 67a06817, code only; rule 11).**
- There are no migrations and no new column between the rollback target and the tip. **The rollback section needs no SET DEFAULT statements, and should say so.**
- No beat entry was added, so there is no new `PeriodicTask` row to disable. Bundle 4's three rows are unchanged.
- H-65's locks are Redis keys with a TTL; the old code ignores them and they expire.
- Nothing bundle 5 stores needs undoing: H-82's anchored due times are ordinary due times to the old code, and H-76's retired buckets stay retired. (This is my reading of the code, not a tested rollback.)
- After a rollback the old behaviours return: 13 grants a year for 29th–31st annual starts (H-82), a paid overage session accepted without a payment intent (H-66), addresses in the licence and signal logs (H-80), `manage.py backfill` back as a command (H1; it must not be run), and the grading benchmark unguarded (H2).

**N2 (deploy: the one new setting, from H2).** Outside DEBUG the nightly grading replay is skipped unless `ENABLE_GRADING_BENCHMARK` is set. **The staging/QA worker must set it, or the nightly grading check stops there. Production must never set it.** The weekly live job needs both it and `ENABLE_AI_LIVE_QA`.

**N3 (founder).**
- **H-85, decision pending:** an unattached paying teacher's subscription status is still disclosed to a school admin who invites them (my H-78 N2).
- **H2:** it stops new writes only. Whether production already holds the benchmark teacher and its 5,000,000-credit bucket is unknown until the read-only detection query is run. That is a production action and needs founder approval.
- **H-82:** annual subscribers who started on the 29th–31st now get 12 monthly grants a year, not 13, on their own day. Rows already drifted converge at their next grant, with no migration or backfill. Licence allocations are not covered (H-88, bundle 6).
- **H1:** the script has no dry run, and running it is a production action. Runbooks outside the repo may still say `manage.py backfill`, which now fails safely with "Unknown command".

**N4 (frontend and operations).**
- **Frontend, H-60/H-57:** a licence PATCH that changes a non-patchable field is a 400 naming the field; a changed `custom_price_cents` on a STRIPE licence is a 400 (price changes go through change_plan). On the four licence routes, a Stripe change that was not recorded is a 409 or a 503 with `Retry-After`, not a 500, in the `{"success": false, "message": …}` envelope. Client messages carry no Stripe text.
- **Frontend, H-78 and H-71:** another school's teacher is refused as "This teacher already belongs to another school." before any subscription message. A staff address added as a student is refused the same way whatever its role.
- **Operations, H-80/H-86:** the licence and signal logs name users by id. Anyone who searched those logs by address must search by user id. A new ERROR "Free trial plan not found" means no new teacher is getting a trial.
- **Operations, H-66:** a paid overage session with no payment intent is refused with one ids-only ERROR and is not retried; that line is the only signal for manual reconciliation.
- **Operations, H-82 and H-65:** after a scheduler outage, owed annual grants are caught up one per daily run, each with a WARNING. A skipped scheduled run logs a WARNING naming the task; a Redis fault logs an ERROR and shows in `check_beat_health`.

**N5 (carried open items, all with backlog rows; none blocks).**
- H-81: a monthly grant still owed at the cycle end is lost (H-65 N2, H-82 O2).
- H-89: 13 traceback sites can carry an address. H-91: other modules still log addresses.
- H-92: two stale HTML renders still list `backfill`.
- H-60/H-57: a PATCH of `custom_price_cents` changes only the local price (v2's note 3), and two dead functions (note 4).
- H2: `--pdf` writes its files before the refusal (v2's note 4).

Working files: `~/Documents/Projects/GAP-1a-scratch/gate1-b5/` (`check.sh`, `check_0007cb0b.txt`).
