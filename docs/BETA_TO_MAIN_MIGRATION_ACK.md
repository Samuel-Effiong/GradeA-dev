# Beta → main merge: one-time migration acknowledgement

**Read this the first time a `beta` → `main` merge fails the "Migration
safety" CI check.** This is a one-time backlog cleanup, not something
you'll need to repeat — see [MIGRATIONS.md](MIGRATIONS.md) for the rule
this backlog predates.

## Why this happens

`scripts/check_migration_safety.py` flags any migration file a PR
*introduces* that isn't in the target branch yet, and isn't marked as a
reviewed non-additive step. `beta` has months of migrations that were never
merged to `main`, several of which rename/remove/alter columns. They've
already been applied to the live beta database without incident — the
check just has no way to know that, because from `main`'s side it's never
seen these files before. This is expected, not a bug.

## The files (as of the check that first surfaced this, 2026-08-19)

```
billing/migrations/0030_alter_subscriptionplan_interval_and_more.py
billing/migrations/0032_remove_subscriptionplan_price_cents_and_more.py
billing/migrations/0033_rename_price_subscriptionplan_price_cents.py
billing/migrations/0046_remove_subscriptionplan_carry_over_max_and_more.py
billing/migrations/0048_alter_billingtransaction_transaction_type_and_more.py
billing/migrations/0054_stripeevent_attempts_stripeevent_claimed_at_and_more.py
billing/migrations/0058_alter_licensebillingrecord_record_type.py
```

If more non-additive migrations have landed on `beta` since this list was
generated, re-run the command in step 1 below to get the current, accurate
list before proceeding — don't assume this list is still complete.

## Step-by-step

### 1. Regenerate the current list of flagged files

```bash
python scripts/check_migration_safety.py --base origin/main
```

Everything printed as `FAIL` is a file that needs a decision below. If the
list matches the one above, you're dealing with exactly this backlog. If
it's longer, something non-additive landed on `beta` more recently — treat
those extra files the same way, but give them a closer look (they're more
recent, so anyone who remembers shipping them is easier to ask).

### 2. Don't try to "fix" these migrations

They already ran against the live beta database. There's nothing left to
protect against — the schema change already happened, successfully.
Rewriting or re-splitting an already-applied migration is itself a risk for
no benefit here. The only decision to make is whether to acknowledge them.

### 3. Sanity-check the riskiest ones before waving them through

Most of these are safe to acknowledge without a second thought. But four of
them made a column `NOT NULL` without a default — the specific case where a
partially-backfilled table can genuinely fail a migration outright.
Before acknowledging these four, skim their surrounding history (git log
around the commit, PR description if there is one, `#deploys` /
error-tracking channel around when they shipped) to confirm they *didn't*
cause a rollout blip on beta at the time:

```
billing/migrations/0030_alter_subscriptionplan_interval_and_more.py
billing/migrations/0048_alter_billingtransaction_transaction_type_and_more.py
billing/migrations/0054_stripeevent_attempts_stripeevent_claimed_at_and_more.py
billing/migrations/0058_alter_licensebillingrecord_record_type.py
```

They can't be undone at this point either way — this check is purely so
you go into the merge with eyes open, not to block it.

### 4. Add the acknowledgement marker to every flagged file

```bash
for f in \
  billing/migrations/0030_alter_subscriptionplan_interval_and_more.py \
  billing/migrations/0032_remove_subscriptionplan_price_cents_and_more.py \
  billing/migrations/0033_rename_price_subscriptionplan_price_cents.py \
  billing/migrations/0046_remove_subscriptionplan_carry_over_max_and_more.py \
  billing/migrations/0048_alter_billingtransaction_transaction_type_and_more.py \
  billing/migrations/0054_stripeevent_attempts_stripeevent_claimed_at_and_more.py \
  billing/migrations/0058_alter_licensebillingrecord_record_type.py; \
do sed -i '1i # expand-contract-step: contract' "$f"; done
```

If step 1 turned up additional files beyond this list, add them to the
loop before running it.

`contract` is the right label even though these didn't actually go through
the 3-step process in [MIGRATIONS.md](MIGRATIONS.md) — it represents "this
is the final, already-landed state," which is true. The marker isn't
validated against the operation type; it's a human sign-off, not proof of
correctness.

### 5. Re-run the check locally

```bash
python scripts/check_migration_safety.py --base origin/main
```

Expect `ACK` for every file from step 4, `OK` for everything else, and exit
code `0`. If anything still prints `FAIL`, it wasn't in your loop — add it
and repeat step 4 for it.

### 6. Commit, push, merge

Commit the marker additions as their own small commit on the branch you're
merging to `main` (or directly on `beta` before opening the PR — either
works, as long as it lands before the PR that actually merges to `main`).
Push, confirm the "Migration safety" CI check is green, merge normally.

### 7. Done — this doesn't repeat

From this point forward, every *new* migration goes through the real
expand/migrate/contract process in
[MIGRATIONS.md](MIGRATIONS.md#3-the-expand--migrate--contract-process). This
document only ever needs to be followed again if `beta` and `main` are
allowed to diverge by months of unmerged migrations a second time — worth
avoiding by merging more often, but if it happens, the process is the same
one written here.
