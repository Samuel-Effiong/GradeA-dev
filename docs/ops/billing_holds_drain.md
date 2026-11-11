# Draining credit holds before turning them off

Epic B1b-9 (plan amendment 08b §6.3). This is engineering text for the person running the switch-off. It is not app wording.

**Running `billing_holds_drain` on staging or production is an owner go, every time.** It has no Beat or cron entry, and none may be added.

## Why a drain

The stale-hold sweep does nothing while `BILLING_HOLDS_ENABLED` is off. Turning the flag off with holds still open would leave credits reserved on wallets and papers waiting in the queue. So the flag is turned off only after a drain prints **DRAINED**.

## The settings checklist

Set each one at the platform's shared level, so web, worker and Beat read the same value. Restart all three services after every change.

| Setting | Normal drain | Flag-off emergency |
|---|---|---|
| `BILLING_HOLDS_ENABLED` | `true` until DRAINED | already `false` |
| `BILLING_HOLDS_DRAINING` | `true` | `true` |

A process that starts with `BILLING_HOLDS_DRAINING=true` and `BILLING_HOLDS_ENABLED=false` logs one WARNING at start-up. That is the emergency state; it is allowed, but it should not last.

## What draining does

While `BILLING_HOLDS_DRAINING` is true and the flag is on:
- **New work takes no hold.** No new hold, job or queue entry is made. A single AI call, an upload at the door, and a new grading batch all run the flag-off path. A metered unit keeps the holds path only when its task row is already HELD. These are new work and run flag-off: a unit with no task row, a row with no job, a retry, a late door file, and a unit nested in a flag-off unit.
- **One difference from the flag-off path (SM ruling):** the balance check reads the *spendable* balance, which is the total less what open holds still reserve. This stops new work spending credits that a running held paper will settle against. The upload door already reads spendable.
- **What is held keeps running:** settle, fail, the sweep, and the daily invariant check.
- **Promotion stops.** No queued paper is launched.
- **No queue email is sent:** not "queued", "expiry warning", "restarted" or "expired". This includes the queued jobs the drain expires: their teachers get no email. The low-balance and drift emails are not affected.

## The normal drain

1. Set `BILLING_HOLDS_DRAINING=true` and restart web, worker and Beat.
2. Run:

   ```
   python manage.py billing_holds_drain [--interval 30] [--timeout SECONDS]
   ```

   - It refuses (exit 2) unless the flag and draining are both on in its own settings.
   - In every round it expires every `QUEUED_PENDING_CREDITS` batch job. Their unlaunched papers become FAILED `INSUFFICIENT_CREDITS_MID_BATCH`, which can be retried by hand later, so nothing is lost. It does this in every round, not only the first, because a running batch can queue its remaining papers during the drain.
   - It then lists the wallets with reserved credits, the open jobs, the held items and any invariant problem, by id. It waits `--interval` seconds (default 30) between rounds.
   - It prints **DRAINED** and exits 0 when nothing is open and `check_invariants` is empty.
   - At `--timeout` it exits 1 and names what is still open. The default is the item sweep window plus 10 minutes. With the defaults that is over an hour, because a started item is swept only after the broker's visibility timeout, the grading time limit and the margin.
3. Only after **DRAINED**: set `BILLING_HOLDS_ENABLED=false`, unset `BILLING_HOLDS_DRAINING`, and restart all three services.

If it exits 1, read the ids it named, and run it again. Do not turn the flag off.

## The flag-off emergency

Use this only for a defect that cannot wait for the sweep window. The flag may be turned off at once **only on an explicit order from the owner or the SM**. Record who ordered it, when, and why in NOTES. This is never the default path.

1. Set `BILLING_HOLDS_ENABLED=false` and `BILLING_HOLDS_DRAINING=true`, and restart all three services.
2. Run:

   ```
   python manage.py billing_holds_drain --flag-off-emergency
   ```

   - It refuses (exit 2) unless the flag is off and draining is on.
   - It expires every queued batch job, with no email.
   - It returns every remaining hold at once, through the sweep's own per-item code (`sweep_stale_holds(force=True)`). This covers started and unstarted held items, and jobs with no task row, with no window.
   - It then finishes every still-open job FAILED, and verifies: **DRAINED** (exit 0), or exit 1 naming what is open.
3. Unset `BILLING_HOLDS_DRAINING` and restart all three services.

With the flag off, the balance check reads the total, as before holds existed, so teachers are not blocked meanwhile.

**An unstarted paper still waiting in the broker** is marked FAILED `PROVIDER_FAILURE`, which is retryable and says nothing was charged, and its hold is returned. If its message is delivered later, the flag is off, so it runs as flag-off work and is charged as it runs.

## Kept files

Kept files keep their own lifetime and are purged as normal. The purge does not depend on the flag.
