# Verification: H-62 overage-lock port onto bundle 3 @ f3002bc

**Verifier:** Verification Engineer (1a, grade-automator-plus-c2). **Author:** Hardening (d5).
**Branch:** `task/h62-overage-lock`. **Base:** bundle 3 `6212ce9`, with beta `abeda10` merged (`79506fe`). **Tip:** `f3002bc` (docs-only over `c26a049`). **Date:** 2026-09-30.

**Verdict: VERIFIED-WITH-NOTES.** Nothing is required before merge. The notes are docs corrections and test-adoption suggestions.

## What I checked
My own detached checkout with its own test DB, `EXEMPT_EMAIL_DOMAINS` empty, every run under `systemd-run` MemoryMax=6G, `nice -n 10`, `timeout`.

### History and port fidelity
| Check | Result |
|---|---|
| Ancestry | `6212ce9` and beta `abeda10` are ancestors of `f3002bc`. |
| Evil merge | `79506fe` (beta `abeda10` into the branch): `git merge-tree --write-tree` equals the merge's tree. The only other merge in the range, `0c95d4e`, is bundle 3's, already verified. |
| Cherry-picks against their `-x` originals (patch-id) | 8 of 12 are **identical**. The 4 that differ differ only as the port notes say:<br>• `81c913f` is `f7db2f2` file for file, minus `billing/tests/testing_fake_stripe.py` and the stray `team` file. Beta's harness is **byte-identical** to the original branch's after `139c57d`, and the range doesn't touch it.<br>• `9ed1512` omits only the harness helpers beta already has.<br>• `ebd647f` renumbers the migration to 0071 (depends on `0070_db_defaults_for_rollback`) and adds `db_default=0` / `""` to both new fields.<br>• `8288cc6` passes a `RequestFactory` request to `has_change_permission`. |
| Port commits | `e92eb06` (a `cast`), `f618366` (rule 14), `af26f3b` (runner re-anchor), `2e5b3f5` (stubs; see below), `8e7e865` (runner rule; see below). `fa05589`, `c26a049` and `f3002bc` are docs only; `fa05589` replaces the old-base mutation logs. |
| Non-docs delta after the regression commit | `8e7e865..f3002bc` changes only `AutoGrader/tests_cache_invalidation_coverage.py`, which is bundle 3's `f9f3d94` (already verified) arriving through the beta merge. |

### Hooks (the SM's rule for a range containing a `--no-verify` commit)
| Check | Result |
|---|---|
| `pre-commit run --from-ref 6212ce9 --to-ref f3002bc`, my own run at `f3002bc` | **rc 0, every hook passes**, and nothing was rewritten. It agrees with d5's `port_range_hooks.log`. |
| Per commit (each commit's own files at that commit) | 19 of 20 pass. **`81c913f` fails mypy by itself**: `billing/receipts.py:90`, `StripeClient(str \| None)` [arg-type]. `e92eb06`, the very next commit, fixes it with a `cast` (no runtime change). d5 disclosed this. |
| SM ruling (2026-09-30) | **PASS.** The standard is the whole-range from-ref/to-ref run. `81c913f` is recorded as a known non-bisectable commit (mypy only). No history rewrite. |

### Migration 0071
Two AddFields on `StripeEvent`, both NOT NULL with `db_default` beside the Python default. So a code-only rollback to beta `abeda10` can still insert `StripeEvent` rows, and H-56's guard rule (a) holds. `makemigrations --check` reports no changes. H-28 takes 0072.

### Focus 1 (SM): `2e5b3f5`'s 47 retargeted beta tests
| Check | Result |
|---|---|
| Every test method in the 4 files, before and after (AST comparison) | **177 test methods, 173 unchanged.** The 4 that changed:<br>• `test_free_plan_activation_security…test_checkout_webhook_redelivery_grants_the_paid_plan_once`: the patch target string only.<br>• the flake test: seam and message (below).<br>• the 2 H-50 webhook tests (below).<br>The other edits are fixtures only: `setUp`, the stub helper, `setUpModule` and `tearDownModule`. **No assertion was removed or loosened.** |
| Is the new patch target effective? | All 10 production call sites of `schedule_receipt_url_fill` are in `billing/stripe_service.py` and use its module-level import (`:83`), so patching `billing.stripe_service.schedule_receipt_url_fill` intercepts every grant path. The old stub (`resolve_stripe_receipt_url` returning None) and the new one (no fill queued) leave the same database state. |
| H-50 tests | They asserted `PaymentIntent.retrieve` called once with the payment intent. They now assert **no** retrieve, plus **one** fill queued for a BillingTransaction carrying that payment intent. That's H-62's contract, and no weaker. **My mutant V3** (the lookup put back inside the upgrade transaction) is **killed by both**. |
| The flake test still proves a written-but-uncommitted grant | The seam (`stripe_service.py:3015`) sits after `grant_overage_bucket` and the BillingTransaction record, inside `handle_checkout_completed`'s atomic block.<br>• **Probe P3:** from the blocked worker's own connection the grant reads 500 (written) and `in_atomic_block` is True, while the main thread reads 0 (not committed). After release, it commits.<br>• **My mutant V4** (the fill moved to after commit) is **killed** by the flake test itself and by P3. |
| All 177 ran | In my changed-modules run below, and in d5's billing regression (all 4 modules present). |

### Focus 2 (SM): the runner's load-failure rule (`8e7e865`)
| Check | Result |
|---|---|
| Can a surviving mutant become a kill? | **No.** A mutant whose run exits 0 is SURVIVED before any marker is read (`run_mutants.py:735-741`). The marker only decides between KILLED and BROKEN for a failing run, so the change can only move BROKEN → KILLED. A crash before `Ran ` is still BROKEN. |
| Is a genuinely missing test still caught? | Probed with this Python and Django version. A missing class (`test_receipts.NoSuchVfClass`), a missing method (`FillReceiptUrlTests.test_no_such_vf_method`) and a missing module each report `unittest.loader._FailedTest`, so the runner's own logic classifies all three as **BROKEN, never KILLED**. |
| The only 2 reclassified mutants | M02 is killed by 8 assertion failures (Stripe calls inside the transaction, a lost grant, a lock timeout). M26 is killed by 1 assertion failure. |
| Kills by error alone at `af26f3b` | M21, M24 and M30. Each error is the exception the removed guard existed to catch (`TypeError` on a missing row; `RuntimeError` propagating). Those are genuine kills. |
| Baseline | The battery's 5 test modules pass unmutated at `af26f3b` (`changed_modules.log`, 106 OK). None of the 4 stale-stub modules is a battery module. |

### Focus 3 (SM): can two beat runs, or a replay and a live webhook, double-process one event?
**Protected, on two independent layers.**
1. **The claim.** `_claim_for_replay` is one conditional UPDATE, FAILED to PROCESSING, fenced on the `auto_replay_attempts` it read. Overlapping replays can't both win, because Postgres re-checks the WHERE clause on the locked row under READ COMMITTED. A live delivery's `_claim_stripe_event` refuses a fresh PROCESSING row, and the replay refuses anything not FAILED. d5's tests: `test_concurrent_sweeps_replay_once` and `test_sweep_racing_a_live_redelivery_grants_once` (20 threads × 10 rounds). Mutants P08, P09 and P14 are killed.
2. **The handler.** `_handle_overage_checkout_completed` takes `CreditWallet … select_for_update()` **before** `_overage_already_granted`. Any two handler runs for one payment intent therefore serialise, and the second sees the committed grant. P11 is killed.

d5's race tests reuse the **same** event id, so they exercise only layer 1. Two real paths bypass the claim and rest on layer 2 alone. I probed both:
- **P1:** Stripe's documented duplicate, a **different event id carrying the same checkout session**, delivered live while replays of two FAILED copies race (12 threads × 6 rounds). The block is granted exactly once.
- **P2:** a replay whose claim **goes stale while its handler still runs**. `sweep_stale_stripe_events` settles the row FAILED, a second replay re-claims it and blocks on the wallet lock, and the first is then released. The block is granted once, and the row ends SUCCEEDED. The first run's finish is fenced out.

**My mutant V1** (the wallet lock removed) is **killed** by P1 (4000 granted, 500 expected), by P2 (1000 against 500), and by 4 beta concurrency tests in `test_overage_purchase_integrity`. d5's replay race tests do not catch it.

The other beat job, `sweep_missing_receipt_urls`, only reads Stripe (GET), and its fill is a conditional UPDATE (M18), so an overlap costs at most a duplicate GET.

### Runs
| Run | Result |
|---|---|
| Changed modules at `f3002bc`:<br>• the 7 H-62 billing modules;<br>• the 4 modules `2e5b3f5` retargets;<br>• `AutoGrader.tests_migration_rollback_defaults` (reads 0071);<br>• my probes P1–P3. | **Ran 320, OK**, 232 MB peak, 1:47 wall |
| Beat-schedule readers outside billing: `AutoGrader.tests_beat_health` and `AutoGrader.test_health` (N2) | **Ran 30, OK** |
| Author regression (rule 15) | Relied on: `billing` **1758 OK** at `8e7e865` (`port_runs/8e7e865/app_billing.log`, no FAIL or ERROR). Only `f9f3d94`'s test constant changes outside docs after it. Also relied on: reproduce-first 12 of 12 failing on `6212ce9`; 60 of 60 mutants killed with sha-checked restores. |
| Rule 14 | The only new mocks are 3 `mock.Mock` handler spies in `test_event_replay`, and the replay discards their return values. `test_overage_cap`'s bare MagicMock session is now a `SimpleNamespace` (`f618366`). |

## My mutants (4), all killed, every restore sha-checked
| Mutant | Result |
|---|---|
| V1: the overage handler's wallet `select_for_update` removed | **killed** (6): P1, P2, and 4 in `ConcurrentOverageDeliveryTests` |
| V2: the replay runs the handler outside `transaction.atomic` | **killed** (3): `test_concurrent_sweeps_replay_once`, `test_stuck_paid_purchase_is_credited`, `test_replaying_an_already_granted_purchase_grants_nothing_more` |
| V3: `PaymentIntent.retrieve` put back inside the upgrade transaction | **killed** (2): both H-50 webhook tests |
| V4: the overage fill deferred to after commit | **killed** (2): the flake test and P3 |

Probes and the mutant script are in `GAP-1a-records/`: `h62_test_vf_h62_probe.py` and `h62_vf_mutants.py`.

## Notes (not blocking)
**N1 (the SM's ruling).** `81c913f` is non-bisectable: by itself it fails mypy (arg-type), and `e92eb06` fixes it. The whole range passes every hook.

**N2 (docs).** `PORT_BUNDLE3.md`'s regression-scope section says billing is the only app reading the new beat entries. That's not quite right: `AutoGrader/beat_health.py` and `AutoGrader/health.py` read `BEAT_HEALTH_EXPECTATIONS` for every entry, and `AutoGrader.tests_beat_health` and `AutoGrader.test_health` cover them. I ran both (30 OK), so nothing is owed. The sentence should name them. (`dashboard/tests.py` reads only three unrelated schedule keys.)

**N3 (test adoption, recommended).** The replay-specific protection against a duplicate event id and a stale re-claim is the handler's wallet lock. Beta's integrity tests pin that lock, but not along the replay path. P1 and P2 (`h62_test_vf_h62_probe.py`) cover it, and adopting them into `test_event_replay` would keep that pinned if the handler is ever refactored. d5's call; a backlog row is fine.

**N4 (cosmetic).** In the stale re-claim case (P2), both replay runs return REPLAYED, and both log "a customer … has now been credited", though only one grant exists. The first run's settle is correctly fenced out. The note on the row is the second run's. This happens only if a replay outlives the claim window (the webhook hard timeout plus 5 minutes), which is now unlikely: the handler makes no network call.

**N5 (runner hygiene).** Errors count as kills, which is sound only when the battery's modules pass unmutated at the battery commit. A stale `mock.patch` target raises "does not have the attribute", which neither the old nor the new marker treats as a load failure, so a clean baseline is the real defence. Any future battery should record its baseline run beside it, as `af26f3b/changed_modules.log` does.

**N7 (depends on Gate 4 (b), still open).** Layer 2 of Focus 3 runs `_overage_already_granted` only `if payment_intent_id`. With a null `payment_intent`, the handler still takes the wallet lock but has no idempotency key, so two different event ids for the same paid session would both grant. The claim still covers the same event id. A null payment intent with `payment_status == "paid"` is exactly the open red-team question (b), the H-40 interaction, in `EVIDENCE.md`'s Gate 4 row. My Focus 3 conclusion holds for every session that carries a payment intent, and that question decides whether any real signed event doesn't. I'm taking the Gate 4 red-team items next (see the handover to the SM); they are not part of this verdict.

**N6.** `docs/evidence/flaky_overage/repro_overage_concurrency_flake.py` still patches the deleted `resolve_stripe_receipt_url`. It is historical, and only runs against a pre-H-62 tree, as `PORT_BUNDLE3.md` says.
