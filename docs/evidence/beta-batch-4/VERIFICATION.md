# Verification: beta-batch-4, Gate 1 (batch level) @ d28d4de

**Verifier:** Verification Engineer (1a, grade-automator-plus-c2). **Integrator:** Integration & Release (0b).
**Base:** beta `abeda10` (bundle 3). **Tip:** `d28d4de`; the code tip is `2f77394`, and `d28d4de` adds only 0b's full-run record (docs). **Date:** 2026-09-30.

**Verdict: VERIFIED.** Bundle 4 is exactly its verified items, merged cleanly, and its one strict full run passed on the exact final code tip. Nothing is required before the push. The founder still has to confirm that specific push, and the package's rollback steps should include N1.

## What I checked (git and records)
| Check | Result |
|---|---|
| Ancestry | beta `abeda10` is an ancestor of `2f77394`. |
| Evil merges | All **7** first-parent merges in `abeda10..2f77394` are **clean** (`git merge-tree --write-tree <p1> <p2>` equals the merge's tree): `ede7101`, `98b0ddf`, `1afebe4`, `bd29d1f`, `86022c2`, `957cc15`, `2f77394`. The one direct commit, `2ea9b92` (v2's record for the worktree script), is docs only. |
| Each item is its verified SHA plus docs only | • expire-bucket race: **`5ee8bff`** (1a VERIFIED) → `0a8ff68`<br>• H-62: **`f3002bc`** (1a VERIFIED-WITH-NOTES, Gate 4 PASS) → `4f74845`<br>• H-62 N3 tests: **`1448f14`** (1a VERIFIED) → `f3be010`, `c1458ba`<br>• task-worktree.sh base ref: **`cd4e8ae`** (v2 VERIFIED)<br>• H-28 Change 1: **`1109ffd`** (1a VERIFIED-WITH-NOTES after R1/R2) → `1113bec`<br>Each merged tip has **0** non-docs changes after its verified SHA, and each verified SHA is an ancestor of what was merged. |
| Records in the tree, byte for byte | My records: expire-bucket race (`docs/evidence/expire-bucket-race/VERIFICATION.md`), H-62 (`…/p1_overage_lock/VERIFICATION_h62_overage_lock.md`, with the N3-closed section), H-62 Gate 4 (`…/VERIFICATION_h62_gate4_redteam.md`) and H-28 (`…/h28_p1b/VERIFICATION_h28_change1.md`, with the re-check). v2's record for the worktree script is `2ea9b92`. |
| Migrations | billing **`0071`** (H-62: two AddFields on `StripeEvent`, each with a `db_default`) and **`0072`** (H-28: one `CreateModel`, `LicenseStripeMutationIntent`), in a line from H-56's `0070`, with one leaf. |
| Rollback to beta `abeda10` (code only) | 0071's columns have DB defaults, so the old code still inserts `StripeEvent` rows. 0072's table is never touched by the old code. **See N1 for the beat entries.** |

## Gate 10 (0b's strict full run; one per bundle, rule 15)
| Tip | Result |
|---|---|
| `2f77394` (the final code tip) | **Ran 5303 tests, OK (skipped=28)**, 0 blocked outbound. `RACE_COST_*` unset, 12G, `--parallel 4`; whole-repo mypy and `makemigrations --check` ran first (0b's `docs/evidence/beta-batch-4/FULL_SUITE.md`, `d28d4de`) |

**It was the first strict run on the exact final code tree, and it passed.** Unlike bundle 3, no failure had to be fixed and re-run.

## Notes (not blocking)
**N1 (rollback: the beat entries).** Bundle 4 adds three `CELERY_BEAT_SCHEDULE` entries: `sweep-missing-receipt-urls` and `replay-safe-failed-stripe-events` (H-62), and `escalate-stale-licence-stripe-intents` (H-28). Beat runs `django_celery_beat.schedulers.DatabaseScheduler`, which copies the settings schedule into `PeriodicTask` rows **and never deletes one**. After a code-only rollback to `abeda10` the three rows would stay enabled, and the old workers would reject every run as an unregistered task: an error every 5 minutes or hour, with no data harm. **The package's rollback steps should disable those three `PeriodicTask` rows** (Django admin → Periodic tasks), and re-enable them if rolled forward.

**N2 (carried).** The H-28 notes, all non-blocking: N1, the helper case of the Stripe scan (latent, stated); N2, the licence customer and set-up-intent calls on the legacy API; N3, the per-read socket timeout; N4, Gate 7 in Stripe test mode. H-62's N7 (a null `payment_intent`) was closed by its Gate 4, and S1 became backlog H-66.

**N3 (deploy, from 0b's notes).** `ENABLE_STRIPE_LIVE_QA` must be off wherever live Stripe keys are used (H-62 Gate 4 S2: a config read for the founder). The new command `resolve_licence_stripe_intent` is dry-run by default.

---

## Gate 1 refresh @ 0c95c56 (after the F6 fold-in). Verification Engineer 1a, 2026-09-30
**Verdict for the tip 0c95c56: VERIFIED.** Bundle 4 is still exactly its verified items. The four F6 fixes are each their verified SHA plus docs only, merged cleanly, and the one strict full run passed on the exact final code tree. Nothing is required before the push; the founder still has to confirm that specific push.

**The range since my Gate 1 at d28d4de (recorded at 8de3078):**

| Check | Result |
|---|---|
| Ancestry | 8de3078 is an ancestor of 0c95c56. First-parent commits: 3b94aa8, 7faaedf, e190f06, 9627f7d, 4e629d4 (merges) and 0c95c56 (docs). |
| Evil merges | For all **5** merges, `git merge-tree --write-tree <p1> <p2>` equals the merge's own tree. |
| Each F6 item is its verified SHA plus docs only | • **Item 1, the add_teachers school-name leak:** **`152a8da`** (1a VERIFIED-WITH-NOTES) → `82e724a`, merged at 3b94aa8<br>• **Item 2, H-38 tasks namespace and auto-grade:** **`970c010`** (1a VERIFIED, round 2) → `d755926`, merged at 7faaedf<br>• **Item 3, the mid-cycle grant and trial-expiry re-checks:** **`128adf5`** (1a VERIFIED re-check; round 1 VERIFIED-WITH-NOTES) → `2bfa2e8`, merged at e190f06<br>• **Item 4, the monthly rollover lost to the 05:00 cleanup:** **`4da21c3`** (1a VERIFIED, round 2; round 1 REJECTED), merged at 9627f7d. Its record `52dbdc8` (docs only) is merged at 4e629d4<br>Each verified SHA is an ancestor of what was merged, with **0** non-docs changes after it. |
| Records in the tree, byte for byte | My four records match my copies exactly: `docs/evidence/add-teachers-school-leak/VERIFICATION_1a_152a8da.md`, `docs/evidence/h38-tasks-namespace/VERIFICATION_h38_tasks_namespace.md`, `docs/evidence/midcycle-grant-recheck/VERIFICATION.md` and `docs/evidence/monthly-rollover-cleanup-race/VERIFICATION.md`. So does this Gate 1 record at 8de3078 (`docs/evidence/beta-batch-4/VERIFICATION.md`). |
| Code in the range | `assignments/tasks.py`, `users/views.py`, `students/task_access.py`, `billing/{license_service,services,tasks,refresh_timing}.py`, and tests. Nothing else. |
| Migrations, settings | **None** in 8de3078..0c95c56 (no `migrations/` or settings file). **No new beat entries**, so my N1 (the three `PeriodicTask` rows) is unchanged. |
| After 4e629d4 | **Docs only:** `docs/evidence/beta-batch-4/FULL_SUITE.md`. The strict run's tree (4e629d4) therefore equals the push tip's code. |

## Gate 10 (0b's strict full run after the fold-in; rule 15, not repeated)
| Tip | Result |
|---|---|
| `4e629d4` (the final code tip) | **Ran 5382 tests, OK (skipped=28)**, 0 blocked outbound. `RACE_COST_*` and `AUDIT_BENCH` unset; whole-repo mypy and `makemigrations --check` clean first (0b's `FULL_SUITE.md`, 0c95c56) |

- **The count checks out:** 5382 − 5303 = **79**. The range adds 72 `def test_` methods and removes none. The rollover module's `RefreshRaceFixture` mixin puts its 7 shared tests in both path classes (annual and licence), so they run twice: 72 + 7 = 79.
- **The wall clock** (3096 s) includes a laptop suspend from 22:48:01 to 23:30:31, which 0b cites from `journalctl`. Django's own time (514 s) comes from a monotonic clock, which doesn't advance while suspended, so the run was paused, not hung. No test failed or errored.
- **It was the first strict run on the final code tree, and it passed**, with no re-run.

## Notes (not blocking)
- **R1 (scope: H-55 is not in bundle 4).** `5fd88ff` (H-55, the token_epoch pin test, 1a VERIFIED, test only) is **not** an ancestor of 0c95c56. The bundle's F6 list names four items, and this matches it. I'm noting it only because H-55 was verified alongside them; it ships with the next bundle unless the SM says otherwise.
- **R2 (rollback to beta abeda10, code only).** There are no migrations, so the rollback is clean.
  - Monthly buckets granted by the new code keep their later expiry (up to 2 days past the due time). The old code still retires them by the newest unprocessed bucket, so nothing is spendable twice.
  - Buckets the new cleanup kept back are written off by the old cleanup at its next 05:00. That's the pre-fix behaviour returning, with no new harm.
- **R3 (carried open items, the SM's to rule on; none blocks):**
  - add_teachers N1 (a sibling path);
  - H-38 N3 (no `GRADING_FAILED` event on a refusal) and N4 (no run-time check on extraction);
  - mid-cycle N1 (if card-on-file trials are ever introduced, `expire_active_trials` must leave Stripe-trialing rows to the webhooks);
  - H-76 (`apply_immediate_plan_change` leaves the old bucket unprocessed; bundle 5).
- **R4 (production actions).** Running the F6 read-only detection queries against production (`detect_*`, including `detect_monthly_rollovers_lost_to_cleanup.sql`) is a production action and needs founder approval. The outputs are ids and amounts only.
- Everything under **N1 to N3 above** (the beat rows on rollback, the H-28 notes, `ENABLE_STRIPE_LIVE_QA`) still applies unchanged.
