# H-28 Change 1: the port onto H-62, and the commits since

`task/p1b-divergence` was built on `b744c9f`, the same old base as H-62. After H-62 landed in bundle 4, the founder's order was H-62 then H-28. This note records the port and Change 1's commits on the new base. The design is `DESIGN_PROPOSAL.md` §9, and the reproductions are `REPRODUCE_FIRST.md`. Logs from the old base keep their names and say so.

**Branch:** `task/h28-p1b`, off H-62 `f3002bc` (the verified H-62 tip; H-62 later gained only docs and one verified test commit, and is merged before handoff).

## How it was ported

Clean re-apply, not a rebase. Each of the 11 commits that were not WIP was applied with `git cherry-pick -n` and committed through every pre-commit hook, with a `(cherry picked from commit …)` line.

| Original | Ported | Port notes |
|---|---|---|
| `5ba468e` … `07d7249` (8 commits: the Phase 1 audit, the design and its reviews, the payment-failure finding) | `3dacca1` … `01ee5e3` | docs only, clean |
| `90389f0` commit 1, the reproduce-first tests | `f2f10e4` | clean |
| `a5a6508` commit 2, the intent model | `cb2e657` + `8aada3c` | The migration is **renumbered 0070 → 0072** (`0072_license_stripe_mutation_intent`), after H-56's 0070 and H-62's 0071. It is a `CreateModel`, which the H-56 rollback guard leaves out of scope (the previous release never writes a new table), so no `db_default` is added. `makemigrations --check` is clean. **Known non-bisectable commit: `cb2e657`** was committed with the renamed file still depending on `0069_price_sync_audit` (the dependency edit was made after the rename was staged), which gave `billing` three leaf migrations. `8aada3c`, the next commit, fixes it. No history rewrite (team rule). |
| `7bd028a` the SM's ruling on ESCALATED intents | `b136236` | docs only, clean |
| `f3b8d7b`, `cfd8d9e` (WIP save-points for commit 3, both committed on the old branch with `--no-verify`) | consolidated into commit 3, `af43442` | Not cherry-picked as commits. Their changes were applied to the working tree, finished, tested, and committed once through every hook. That fixed the missing imports `f3b8d7b` recorded, and a mypy error in `record_intent` (a licence with no Stripe subscription is now refused there). `commit3_reproduce_first_cancel_fixed.log` is `cfd8d9e`'s run on the old base, kept as found. |

## Change 1 on the new base

| # | Commit | What |
|---|---|---|
| 3 | `af43442` | the phase plumbing (`billing/license_stripe_mutation.py`) and `cancel_license_subscription` on it |
| 4 | `f190771` | `update_seats` on it, with F4 and F5: an unpaid or declined seat increase is reverted and its own invoice voided (`undo_unpaid_change`) |
| 5 | `318c7d8` | `change_license_plan` on it, with F0 and F1–F3; `change_license_price` delegates to it |
| 6 | `83dd6c2` | `convert_license_to_offline` (P0) on it: no compensation, and a refused or lost delete is read back |
| — | `8e450e6` | merge of bundle 4 (`bd29d1f`: H-62's final tip and the `expire_bucket` race fix), so H-28's rule-15 runs cover what it lands beside; then `04314f6` logs H-67 (backlog only) |
| 7 | `f3e8d4d` | the bounded retry of the local write (§9e); the named Gate-5 assertion is `test_h28_finalise_retry.test_after_the_connection_is_killed_the_retry_succeeds_on_a_fresh_one` (see *The kill in commit 7's test* below) |
| 8 | `0cfe219` | a human is told (§9g–§9i): every reconciliation alert also emails every active super admin; the stale-intent periodic task `escalate-stale-licence-stripe-intents` (every 5 min, one query, no Stripe call; beat entry and health expectation added); and `manage.py resolve_licence_stripe_intent` (§9h-bis) |
| 9 | `77e8c95` | the per-request Stripe budget (§9i (2)); the named Gate-5 assertion is `test_h28_stripe_budget.test_slow_stripe_on_every_call_ends_in_the_error_branch_with_its_alert_in_time` (see *The request budget* below) |
| 10 | `01bde47` | docs: the detector-spec amendment (`SPEC_audit_stripe_divergence.md`): compare price, quantity, renewal and open change-invoices, not only status, and read the intent ledger first |
| — | `69dada7` | the mutation battery runner |
| 11 | `2e770c2` | the SM's review of commit 9: every licence Stripe call through `LicenceStripe`, bounded at the socket (see *The request budget*) |
| — | `e70d641` | test-only fixes from the rule-15 run (below) |

## F1: the 4-point behaviour-change record

F1 is the one customer-visible behaviour change in Change 1 (`FINDING_licence_payment_failure_paths.md`; approved in principle, conditional on this record).

1. **Previous behaviour:** a licence plan upgrade whose invoice needed 3D Secure (`requires_action`) raised "Upgrade payment requires additional authentication (3D Secure). Please update your payment method and retry." and did nothing else at Stripe. The new price stayed live at Stripe with its invoice open, while `change_license_plan`'s transaction rolled the local plan back. Stripe billed the new price while the application showed the old one. It was latent until now, because F0 stopped every plan change reaching Stripe.
2. **New behaviour:** the same message, and the upgrade is undone at Stripe. The old price is restored (`proration_behavior="none"`, idempotency key `h28-licence-<intent>-revert`), and the upgrade's own invoice is voided (`…-void`). The intent ends FAILED, and the licence stays on its old plan on both sides. If the undo fails, the intent is ESCALATED and a human is alerted.
3. **Why this is correct:** licence upgrades have no 3D Secure completion flow (DESIGN_PROPOSAL.md §9h item 3), so an upgrade left awaiting authentication could never complete through the application. Leaving it live is the divergence H-28 removes. The individual upgrade path already does exactly this (`_revert_to_previous_price`), so both paths now agree. The message already told the customer to retry.
4. **Tests that prove it:** `test_h28_licence_stripe_divergence.test_F1_plan_upgrade_needing_3d_secure_leaves_both_sides_agreeing` and `test_latent_F1_price_change_needing_3d_secure_is_reverted_and_voided` (reproductions, failing on the old base), and `test_h28_plan_phases.test_F1_an_upgrade_needing_3d_secure_is_reverted_and_voided` (the revert and void keys, FAILED, and nothing left open).

## `change_license_price` now runs the whole operation

Its only production caller was `change_license_plan`. Called on its own, it changed Stripe only and left the local plan to its caller, which is H-28's divergence by construction. It now delegates to `change_license_plan` (`new_custom_price_cents=None` meaning the plan's own price, as before), so nothing can change a licence's price at Stripe without recording it. The latent F1–F3 reproductions still call it directly, unchanged. `test_change_license_price_now_records_the_change_too` pins the new contract.

## The kill in commit 7's test

The named Gate-5 assertion needs Postgres to really end a session, as the 60 s idle-in-transaction timeout does. Every session's test database lives on the same Postgres server, so the kill must never reach anyone else's backend (0b's condition):

- It runs `SELECT pg_terminate_backend(pg_backend_pid())` on the raw connection being killed, so it can only end that very session. It never looks up a pid in `pg_stat_activity`.
- Just before, it reads that session's `(pg_backend_pid(), current_database())`. The test asserts the database is the test's own (`connection.settings_dict["NAME"]`, starting `test_`), and that the retry then committed on a **different** backend pid.
- It fires only on phase D's row lock (`FOR UPDATE`) after Stripe applied the change, so it tests the retry of the local write, not phase C's status write.

## The request budget (commit 9, reworked in commit 11)

Each licence operation runs under one deadline (`REQUEST_BUDGET_SECONDS`, 75 s, which is 25 s inside gunicorn's 100 s). Every Stripe call in the licence flows goes through `LicenceStripe` and is bounded **at the socket** by the time left.

- **Why an adapter.** stripe-python 14.4.1's legacy resource API (`stripe.Subscription.modify` and the like) accepts no per-call timeout. Its request options are `api_key`, `stripe_version`, `stripe_account`, `stripe_context`, `max_network_retries`, `idempotency_key`, `content_type` and `headers`. Its HTTP client (`stripe.default_http_client`) is process-wide, so bounding it would change every Stripe call in the app, which is wider than H-28. So `LicenceStripe` builds a `StripeClient` per call, as `billing/receipts.py` does for receipt lookups. It uses the app's api_key and **the app's Stripe API version** (`stripe.api_version`, so licence calls never drift to another version; the SM's condition), `max_network_retries=1`, and a `RequestsClient` whose timeout is `(time left − retry sleeps) × 0.9 ÷ attempts`, clamped to 1–30 s. The six call types are subscription retrieve, update and cancel (DELETE), invoice retrieve and void, and price create.
- **The outer wait remains, as the outer bound only (commit 9's `call_stripe`).** A call normally fails at its socket inside the budget. The outer wait covers only a reply trickling in slower than the per-read timeout. A thread it abandons still ends at its own socket timeout, so abandoned threads cannot pile up (the SM's review of commit 9).
- **An abandoned call may still land.** So a mutation abandoned after it started is never read back and never classified "not applied": its intent stays PENDING, a human is alerted, and the stale-intent task escalates it. Only a call that never started is FAILED. The named test joins the abandoned modify, sees it land at the fake, and checks the intent is still PENDING.
- **The worker thread only calls Stripe**, and closes any database connection its thread opened (`connections.close_all()` is per-thread), so none can leak against Postgres's limit.
- **The proof that no Stripe call runs inside a transaction survives the thread:** the worker carries its caller's `in_atomic_block` (`caller_in_atomic_block()`), which the test fake and the idle-in-transaction kill read.

Tests: `LicenceStripeAdapterTests` covers the client's key, version, retries, sized timeout, and the idempotency key in its options; the mapping of each call; and the sizing. `test_a_hung_stripe_fails_at_the_socket_inside_the_budget` uses a real socket against a local server that never answers: the call fails with a connection error, not the outer wait's, inside a 3 s budget, and leaves no thread. `NoLegacyStripeCallTests` scans the licence flows' source for any direct `stripe.Subscription/Invoice/Price` call, with a guard-on-guard. The test fake now patches `LicenceStripe`.

## Runs

Dev runs, each under rule 13 (`systemd-run` MemoryMax=6G, `nice -n 10`, `timeout`), with logs in `port_h62/`:

| Run | Tree | Result |
|---|---|---|
| Reproduce-first: the 18 reproductions at `b136236` (commits 1–2 ported, no fix), disposable worktree | `b136236` | **18 of 18 fail** |
| Commit 3's modules | commit-3 tree | 58 ran; only the 16 expected failures (sites commits 4–6 fix) |
| Commit 4's modules | commit-4 tree | 74 ran; only the 11 expected failures (commits 5–6) |
| Commit 5's modules, with `test_mailerlite_sync` (it calls the rewritten plan change and cancel) | commit-5 tree | 109 ran; only the 2 expected failures (convert to offline, commit 6) |
| Commit 6's modules | commit-6 tree | **119 ran, OK: all 18 reproductions pass** |
| Commit 7's modules | commit-7 tree, over `04314f6` | 123 ran, OK |
| Commit 8's modules, with `AutoGrader.tests_beat_health` and `AutoGrader.test_health` (a new beat entry) | commit-8 tree | 165 ran, OK |
| Commit 9's modules | commit-9 tree | 173 ran, **1 failure in a new test of mine**: `test_the_worker_thread_leaves_no_database_connection_open` compared `django.db.connection`, a proxy shared by every thread. Fixed to compare each thread's `connections["default"]`; `test_h28_stripe_budget` re-run: 8 OK. The other 172 passed in the first run |
| Commit 11's modules (the socket-bound rework), with `AutoGrader.tests_network_guard` and the live-QA registry | commit-11 tree | first run: 195 ran, **1 failure that found a real bug**. `test_a_hung_stripe_fails_at_the_socket_inside_the_budget` showed the outer wait firing: a new thread does not inherit contextvars, so on the worker the deadline was invisible and every call got the 30 s maximum socket timeout. Fixed by running the worker in a copy of the caller's context (mutant L29 pins it). Re-run: **195 ran, OK** |

## Rule 15: the author's gates for Change 1 (at `2e770c2`, then `e70d641`)

Every run is under `systemd-run` MemoryMax=6G, `nice -n 10` and `timeout`, one process at a time, with `EXEMPT_EMAIL_DOMAINS` empty. Logs are in `port_h62/rule15/`: trimmed to one outcome line per test, with emails redacted.

| Run | Result | Log |
|---|---|---|
| Changed modules: the 9 H-28 modules, `test_mailerlite_sync` (calls the rewritten plan change and cancel), `test_live_qa_scenario_registry`, `AutoGrader.tests_beat_health`, `AutoGrader.test_health` (the new beat entry), `AutoGrader.tests_migration_rollback_defaults` (reads 0072) | 195 ran, OK (`2e770c2`) | `rule15/changed_modules_2e770c2.log` |
| Mutation battery: 29 mutants, one per guard (`mutation/run_mutants.py`), each running the 9 H-28 modules in a disposable worktree, restored from the commit blob and sha256-checked | 25 of 29 killed at `2e770c2`; the 4 survivors were weak tests, strengthened in `e70d641` (test-only) and then killed: **29 of 29** | `mutation/results.tsv` (every run), `mutation/logs/`, `rule15/mutation_battery_2e770c2.log`, `rule15/survivors_e70d641.log` |
| The ONE owning-app regression: `billing` | 1882 ran at `2e770c2`: **5 failed**, all in `test_license_cancellation` (below); the rest passed. After the test-only fix, that module and the three strengthened ones: 50 ran, OK (`e70d641`). No second billing run (0b's ruling: the fixes are test-only) | `rule15/app_billing_2e770c2.log`, `rule15/touched_modules_e70d641.log` |

**Regression scope.** The new model (`LicenseStripeMutationIntent`) and the four rewritten operations are read or called only in `billing`: no other app calls `cancel_license_subscription`, `update_seats`, `change_license_plan`, `change_license_price` or `convert_license_to_offline`. The one change outside `billing` is `AutoGrader/settings.py` (a beat entry and its health row), and its readers ran in the changed modules. So `billing` is the owning-app regression, and it runs with bundle 4 merged (`8e450e6`).

**What the regression found.** `billing.tests.test_license_cancellation`, an existing beta module, patched the legacy `stripe.Subscription.modify`, which cancel no longer calls (it goes through `LicenceStripe` since commit 11). Five tests failed. `e70d641` retargets the patch to `LicenceStripe.modify_subscription`; the assertions are unchanged, plus the idempotency key the adapter now receives. **Its failure-path test (`test_stripe_failure_is_surfaced_and_leaves_local_state_untouched`) had kept passing only by accident:** its stale patch intercepted nothing, and the H-39 network guard blocked the real Stripe call that then went out, which still surfaced as the ValueError the test expected (0b asked for this to be recorded). My caller search missed the module because it searched for callers, not for tests patching the removed call. A wider search since then (every test file, for a legacy `stripe.Subscription/Invoice/Price` patch together with a licence operation or endpoint) found no other.

The mutation table, with every mutant's result:

| Mutant | Guard | Result |
|---|---|---|
| L01 | cancel phase A durable | KILLED (FAILED (errors=1)) |
| L02 | seats phase A durable | KILLED (FAILED (errors=1)) |
| L03 | unknown outcome: a timeout is read back | KILLED (FAILED (failures=7, errors=3)) |
| L04 | a CardError is not a refusal (payment_errors) | **SURVIVED** at `2e770c2` (a weak test); KILLED at `e70d641` by `test_the_licence_stays_guarded_while_a_card_error_is_undone` |
| L05 | a refused delete is read back (read_back_on) | KILLED (FAILED (failures=1, errors=1)) |
| L06 | the unpaid change's invoice is voided | KILLED (FAILED (failures=16, errors=1)) |
| L07 | only the change's own invoice (new_invoice_since) | **SURVIVED** at `2e770c2` (a weak test); KILLED at `e70d641` by `test_with_no_new_invoice_an_older_open_one_is_left_alone` |
| L08 | finalise compensates where no money moved | KILLED (FAILED (failures=8, errors=1)) |
| L09 | a paid seat increase is never compensated | KILLED (FAILED (failures=1)) |
| L10 | a paid plan upgrade is never compensated | KILLED (FAILED (failures=1)) |
| L11 | F0: a price change reaches Stripe | KILLED (FAILED (failures=21, errors=1)) |
| L12 | the per-licence guard is reported as busy | KILLED (FAILED (errors=7)) |
| L13 | abandon frees the licence (FAILED) | KILLED (FAILED (failures=2)) |
| L14 | stale check: only intents older than STALE_AFTER | KILLED (FAILED (failures=1)) |
| L15 | stale check alerts once (ESCALATED not re-selected) | **SURVIVED** at `2e770c2` (a weak test); KILLED at `e70d641` by `test_an_old_escalated_intent_is_not_alerted_again` |
| L16 | alerts email the super admins | KILLED (FAILED (failures=3)) |
| L17 | resolve --apply needs a note | KILLED (FAILED (failures=5)) |
| L18 | the local write is retried | KILLED (FAILED (failures=1, errors=2)) |
| L19 | a retry after a lost commit reply writes nothing twice | KILLED (FAILED (failures=1)) |
| L20 | the retry runs on a fresh connection | **SURVIVED** at `2e770c2` (a weak test); KILLED at `e70d641` by the persistent-failure test now asserts a different backend pid per attempt |
| L21 | an abandoned started call stays PENDING | KILLED (FAILED (failures=1)) |
| L22 | the budget bounds each call | KILLED (FAILED (failures=5)) |
| L23 | the worker reports its caller's transaction | KILLED (FAILED (failures=1)) |
| L24 | the worker closes its own connections | KILLED (FAILED (failures=1)) |
| L25 | convert phase A durable | KILLED (FAILED (errors=1)) |
| L26 | plan change phase A durable | KILLED (FAILED (errors=1)) |
| L27 | each call is bounded at the socket by the time left | KILLED (FAILED (failures=2)) |
| L28 | licence calls use the app's Stripe API version | KILLED (FAILED (errors=1)) |
| L29 | the worker runs in the caller's context (sees the deadline) | KILLED (FAILED (failures=1)) |


## For the verifier

- **Hooks over the whole range:** `port_range_hooks.log`, `pre-commit run --from-ref f3002bc --to-ref HEAD` (the SM's rule for a range with `--no-verify` history). No commit on this branch used `--no-verify`. The two old-branch WIPs that did are not in it as commits (see the port table).
- **Known non-bisectable commit:** `cb2e657`, where migration 0072 still depended on 0069; fixed at `8aada3c`.
- **Named Gate-5 assertions:** `test_h28_finalise_retry.test_after_the_connection_is_killed_the_retry_succeeds_on_a_fresh_one` (§9e) and `test_h28_stripe_budget.test_slow_stripe_on_every_call_ends_in_the_error_branch_with_its_alert_in_time` (§9i (2)).
- **Behaviour changes a reviewer should weigh:** F1 (its 4-point record is above); `change_license_price` now runs the whole recorded operation; and the MailerLite sync after a plan change now runs after commit.
- **Deploy notes (0b has them):** the beat task `escalate-stale-licence-stripe-intents`, and the command `resolve_licence_stripe_intent`.
- **Not done here, by design:** Gate 7 against Stripe test mode, including the DELETE idempotency question in §9j, and the detector itself (`SPEC_audit_stripe_divergence.md`, which needs production read access).
