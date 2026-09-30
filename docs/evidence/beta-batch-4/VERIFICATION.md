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
