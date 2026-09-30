# H-62: the overage-lock work ported onto bundle 3

`task/overage-lock` was built on `b744c9f`, 350 beta commits back, as "H-23" (a label superseded: H-23 is the email-logging item; this is **H-62** in `docs/HARDENING_BACKLOG.md`). The founder revived it after batch-2b. This note records the port. `EVIDENCE.md` beside it is the branch's original evidence on the old base, carried unchanged, and its gate results refer to that base.

**Branch:** `task/h62-overage-lock`, off bundle 3 `task/beta-batch-3` @ `6212ce9`, which contains beta `755aa27` and H-56. The base and the plan were approved by the SM on 2026-09-30.

## How it was ported

Clean re-apply, not a rebase. The 12 commits were cherry-picked in order with `-x`. Each was committed through the pre-commit hooks, **except the first** (see below).

| Original | Ported | Port notes |
|---|---|---|
| `f7db2f2` receipt links after commit | `81c913f` + `e92eb06` | `billing/tests/testing_fake_stripe.py` already exists on beta (`5400759`), as this harness plus the helpers `139c57d` adds, so beta's version is kept. The stray `team` link is not carried. **`81c913f` was committed with `--no-verify` (d5's error, reported to the SM).** `e92eb06` fixes the one error its hooks then found: bundle 3's stricter mypy stubs reject `StripeClient(str \| None)`, fixed with a `cast` (same runtime behaviour). |
| `2ed80f3`, `139c57d` | `a3284cb`, `9ed1512` | clean |
| `22cc9cf` auto-replay | `ebd647f` | the migration is **renumbered 0070 → 0071**, depending on H-56's `0070_db_defaults_for_rollback`. Both new NOT NULL fields (`StripeEvent.auto_replay_attempts`, `auto_replay_note`) gain **`db_default=0` / `""`** (rule 11; H-56's guard rule (a)). The migration was regenerated from the model, and `makemigrations --check` is clean. H-28 takes 0072. |
| `2a7d285` … `b300462` (7 commits) | `d3a7c8b` … `de32685` | clean. Documents inside refer to "migration 0070"; on this branch it is 0071. |
| `47a7c3d` A01–A04 | `8288cc6` | bundle 3's mypy stubs reject `has_change_permission(None)`, so the admin test passes a `RequestFactory` request. The admin method ignores the request, so the assertion is unchanged. |
| (port) | `f618366` | rule 14: `test_overage_cap`'s bare `MagicMock(id=…, url=…)` Stripe session is now a `SimpleNamespace`, because the overage checkout reads only `id` and `url`. The event-replay spy handlers are `mock.Mock`s whose return value the replay ignores (`billing/event_replay.py:218`). |
| (port) | `af26f3b` | the mutation runner is re-anchored: 17 anchors in `billing/stripe_service.py` sat 12 lines low, each was checked at +12, and all 60 now apply. `P1MUT_DB` lets a sequential run share one test DB. |

### Beta tests that stubbed the deleted lookup (`2e5b3f5`)

The first `billing` regression, at `af26f3b`, failed with 45 errors and 2 failures. **All 47 were stale stubs of `billing.stripe_service.resolve_stripe_receipt_url`**, the in-transaction Stripe lookup that H-62 deletes by design. Four modules added on beta after `b744c9f` referred to it. None came from the base (bundle 3's strict full run on `6212ce9` had `billing` clean) or from the run environment. `2e5b3f5` retargets them to `schedule_receipt_url_fill`, the call that replaced the lookup inside the grant's transaction:

| Module | Was | Now |
|---|---|---|
| `test_overage_purchase_integrity` (42 errors, every one in setUp, the flake test included) | stub `resolve_stripe_receipt_url` | stub `schedule_receipt_url_fill`, so no broker and no network. The slow-worker flake test blocks there, after the grant is written and before commit, so it still exercises a written-but-uncommitted grant |
| `test_overage_refund_lifecycle` (2 errors: setUpModule, once for its TestCase classes and once for its TransactionTestCase class, which Django runs separately) | module-level stub of the lookup | module-level stub of the scheduling |
| `test_free_plan_activation_security` (1 error) | stub of the lookup | stub of the scheduling |
| `test_subscription_cycle_integrity` (2 failures; H-50) | asserted that the handler called `PaymentIntent.retrieve` once | asserts that the handler makes **no** Stripe call and queues one fill for its payment intent |

`docs/evidence/flaky_overage/repro_overage_concurrency_flake.py` still patches the deleted function. It is the historical repro of the pre-H-62 flake and is run only against a pre-H-62 tree, so it is left as it was.

### The mutation runner's load-failure rule (`8e7e865`)

In the first battery, M02 and M26 were marked BROKEN, although they were caught by assertion failures (8 and 1). The runner refused a kill whenever the output contained "has no attribute". That rule was meant to catch a test class missing at the commit, but each of these mutants raises an AttributeError of its own at run time:

- M26: `'str' object has no attribute 'get'` on the unexpanded charge;
- M02: `'list' object has no attribute 'replace'` from the inline lookup meeting `test_overage_cap`'s Stripe fakes.

The marker is now `unittest.loader._FailedTest`, which is how unittest reports a class or module it cannot load. No other battery log contains any load-failure marker, so the change reclassifies only these two, and both were re-run with the fixed runner.

**Hooks over the whole range:** `pre-commit run --from-ref 6212ce9 --to-ref HEAD` passes every hook (`port_range_hooks.log`), as the SM required because of the `--no-verify` commit.

## Runs (rule 15; every run under `systemd-run` MemoryMax=6G, `nice -n 10`, `timeout`)

Logs are in `port_runs/<tip>/`, trimmed to one outcome line per test with emails redacted, beside each stream's `SUMMARY.txt`. Mutant logs are in `mutation/logs/`, and every mutant run is in `mutation/results.tsv`. Every run had `EXEMPT_EMAIL_DOMAINS=` empty (production parity) and its own test database.

| Run | Tree | Result | Log |
|---|---|---|---|
| Reproduce first: the two behaviour modules (`test_overage_lock_across_network`, `test_receipt_lookup_outside_transaction`) on bundle 3 **without** the port | `6212ce9` | **12 of 12 fail**, as on the old base `b744c9f` (`g1_repro_b744c9f.log`) | `af26f3b/repro_6212ce9.log` |
| Changed modules: the 7 H-62 `billing` test modules and `AutoGrader.tests_migration_rollback_defaults` (the H-56 guard, which reads 0071) | `af26f3b` | 106 OK | `af26f3b/changed_modules.log` |
| Mutation battery, all 60 mutants, sequential, one shared DB (`P1MUT_DB`, dropped after) | `af26f3b` | 58 KILLED, 2 BROKEN (M02, M26; see the runner fix above) | `af26f3b/mutation_battery.log` |
| Owning-app regression: `billing` | `af26f3b` | **FAILED**: 1697 ran, 45 errors, 2 failures, all stale stubs of the deleted lookup (above) | `af26f3b/app_billing.log` |
| M02 and M26 with the fixed runner | `8e7e865` | 2 of 2 KILLED | `8e7e865/mutation_m02_m26.log`, `mutation/logs/M02.log`, `M26.log` |
| Owning-app regression (rule 15): `billing` | `8e7e865` | **1758 OK** | `8e7e865/app_billing.log` |

**60 of 60 mutants are killed**, each restore sha256-checked against the commit blob. The first regression ran 61 fewer tests because `test_overage_refund_lifecycle`'s `setUpModule` failed before its tests ran.

The regression scope is `billing` alone, because H-62 changes only `StripeEvent` (read only in `billing`) and `BillingTransaction` writes in `billing`. `AutoGrader.tests_migration_rollback_defaults` is the only test outside `billing` that reads the new migration, and it ran with the changed modules.
