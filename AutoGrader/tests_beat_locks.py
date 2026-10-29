"""
H-65: one run at a time for each Celery Beat task (AutoGrader/beat_locks.py).

Against the real test Redis (per-process key prefix, H-9):

  * every CELERY_BEAT_SCHEDULE entry is locked or exempt with a reason, and
    each lock's max_hold is below its schedule interval;
  * a second run while one holds the lock skips and does no work, through
    every real guarded task, and its log carries the task name, key and
    holder id only;
  * two real concurrent runs: exactly one does the work;
  * a run whose lock lapsed can't delete a newer run's lock;
  * a crashed run's lock lapses, and the next run proceeds;
  * a Redis error means skip and an ERROR log (fail closed), and
    check_beat_health reports it;
  * the heartbeat keeps a live run's lock, and stops at max_hold.
"""

import threading
import time
from datetime import timedelta
from importlib import import_module
from typing import Any
from unittest.mock import patch

from celery.schedules import crontab
from django.conf import settings
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from redis.exceptions import ConnectionError as RedisConnectionError

from AutoGrader import beat_locks
from AutoGrader.beat_health import check_beat_health
from AutoGrader.beat_locks import (
    EXEMPT_BEAT_TASKS,
    SKIPPED_CACHE_ERROR,
    SKIPPED_HELD,
    BeatLock,
    declared_lock,
    lock_key,
    single_instance,
)
from AutoGrader.testing.concurrency import run_concurrently

MINUTES_IN_A_WEEK = 7 * 24 * 60
EVERY_5_MIN_TASK = "billing.tasks.escalate_stale_licence_stripe_intents"
# Beat entries that exist only on Epic A and take the lock (merge-down b5).
EPIC_ONLY_GUARDED_BEAT_TASKS = {
    "audit.tasks.sweep_audit_retention",
    "audit.tasks.sweep_audit_pii_short_retention",
}


def task_named(path):
    """The task a Beat entry names, imported from its dotted path."""
    module, name = path.rsplit(".", 1)
    return getattr(import_module(module), name)


def schedule_gap_seconds(schedule):
    """The shortest gap between two fires of a Beat schedule."""
    if isinstance(schedule, (int, float)):
        return float(schedule)
    if isinstance(schedule, timedelta):
        return schedule.total_seconds()
    assert isinstance(schedule, crontab), schedule
    # crontab sets its expanded fields at runtime, so the stubs lack them.
    cron: Any = schedule
    fires = [
        minute
        for minute in range(MINUTES_IN_A_WEEK)
        if minute % 60 in cron.minute and (minute // 60) % 24 in cron.hour
        # Celery's day_of_week: 0 is Sunday. Minute 0 here is a Sunday.
        and minute // (24 * 60) in cron.day_of_week
    ]
    assert fires, f"{schedule} never fires in a week"
    gaps = [b - a for a, b in zip(fires, fires[1:], strict=False)]
    gaps.append(fires[0] + MINUTES_IN_A_WEEK - fires[-1])
    return min(gaps) * 60.0


def beat_entries():
    return {
        entry["task"]: (name, entry["schedule"])
        for name, entry in settings.CELERY_BEAT_SCHEDULE.items()
    }


class BeatScheduleCoverageTests(SimpleTestCase):
    """A new Beat entry must choose: locked, or exempt with a reason."""

    def test_every_beat_task_is_locked_or_exempt(self):
        unguarded = [
            f"{entry} ({task})"
            for task, (entry, _) in beat_entries().items()
            if task not in EXEMPT_BEAT_TASKS and declared_lock(task_named(task)) is None
        ]
        self.assertEqual(
            unguarded,
            [],
            "add @single_instance(max_hold=...) under @shared_task, or an "
            "EXEMPT_BEAT_TASKS entry with its reason",
        )

    def test_every_exemption_names_a_scheduled_task(self):
        self.assertEqual(set(EXEMPT_BEAT_TASKS) - set(beat_entries()), set())

    def test_each_lock_is_named_after_its_task(self):
        for task in beat_entries():
            lock = declared_lock(task_named(task))
            if lock is not None:
                self.assertEqual(lock.name, task)

    def test_max_hold_is_below_the_schedule_interval(self):
        """So a hung run can never swallow the next scheduled one."""
        for task, (entry, schedule) in beat_entries().items():
            lock = declared_lock(task_named(task))
            if lock is None:
                continue
            with self.subTest(entry):
                self.assertGreater(lock.ttl_seconds, 0)
                self.assertLessEqual(lock.ttl_seconds, lock.max_hold_seconds)
                self.assertLess(lock.max_hold_seconds, schedule_gap_seconds(schedule))

    def test_a_hung_runs_lock_is_gone_before_the_next_scheduled_run(self):
        """N1 (1a): a run's last heartbeat can land just before max_hold and
        keep the lock a full TTL longer, and so can a worker killed right
        after a heartbeat. The worst-case lifetime is max_hold + ttl, and it
        must end before the next scheduled fire."""
        for task, (entry, schedule) in beat_entries().items():
            lock = declared_lock(task_named(task))
            if lock is None:
                continue
            with self.subTest(entry):
                self.assertLess(
                    lock.max_hold_seconds + lock.ttl_seconds,
                    schedule_gap_seconds(schedule),
                )

    def test_the_gap_calculation(self):
        self.assertEqual(schedule_gap_seconds(crontab(minute="*/5")), 300)
        self.assertEqual(schedule_gap_seconds(crontab(minute=0, hour="*/6")), 6 * 3600)
        self.assertEqual(schedule_gap_seconds(crontab(minute=30, hour=2)), 86400)
        self.assertEqual(
            schedule_gap_seconds(crontab(minute=0, hour=7, day_of_week=6)),
            7 * 86400,
        )


class LockTestCase(SimpleTestCase):
    def setUp(self):
        cache.clear()
        self.calls = []

    def tearDown(self):
        cache.clear()

    def locked(self, max_hold=60, ttl=beat_locks.DEFAULT_TTL_SECONDS, body=None):
        @single_instance(max_hold=max_hold, ttl=ttl)
        def h65_probe_task():
            self.calls.append(1)
            if body is not None:
                return body()
            return "did the work"

        return h65_probe_task

    @property
    def name(self):
        return f"{__name__}.h65_probe_task"

    def held_by(self, token, ttl_seconds=600):
        self.assertTrue(BeatLock(self.name, ttl_seconds, 600).acquire(token))

    def holder(self):
        return BeatLock(self.name, 1, 1).holder()


class SingleInstanceTests(LockTestCase):
    def test_a_run_while_another_holds_the_lock_skips_without_working(self):
        self.held_by("other-run-id")

        with self.assertLogs("AutoGrader.beat_locks", "WARNING") as logs:
            summary = self.locked()()

        self.assertEqual(summary, f"{self.name}: {SKIPPED_HELD}.")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.holder(), "other-run-id", "the holder's lock kept")
        [line] = logs.output
        self.assertIn(self.name, line)
        self.assertIn(lock_key(self.name), line)
        self.assertIn("held by run other-run-id", line)

    def test_a_run_takes_and_releases_the_lock(self):
        seen = {}

        def body():
            seen["holder"] = self.holder()
            return "did the work"

        self.assertEqual(self.locked(body=body)(), "did the work")
        self.assertTrue(seen["holder"].startswith("direct:"), seen)
        self.assertIsNone(self.holder())

    def test_the_lock_is_released_when_the_run_raises(self):
        def body():
            raise RuntimeError("boom")

        with self.assertRaises(RuntimeError):
            self.locked(body=body)()
        self.assertIsNone(self.holder())

    def test_a_lapsed_run_cannot_delete_a_newer_runs_lock(self):
        """Compare-and-delete: run A's lock lapses mid-run and run B takes
        it; A's release at the end must leave B's lock alone."""

        def body():
            cache_key = lock_key(self.name)
            beat_locks._redis().delete(cache_key)  # A's lock lapses
            self.held_by("newer-run")  # B takes it

        with self.assertLogs("AutoGrader.beat_locks", "ERROR") as logs:
            self.locked(body=body)()

        self.assertEqual(self.holder(), "newer-run")
        self.assertIn("finished without its lock", logs.output[0])

    def test_a_crashed_runs_lock_lapses_and_the_next_run_proceeds(self):
        crashed = BeatLock(self.name, 1, 1)
        self.assertTrue(crashed.acquire("crashed-run"))
        self.assertEqual(self.locked()(), f"{self.name}: {SKIPPED_HELD}.")

        deadline = time.monotonic() + 5
        while self.holder() is not None and time.monotonic() < deadline:
            time.sleep(0.05)

        self.assertEqual(self.locked()(), "did the work")
        self.assertEqual(self.calls, [1])

    def test_a_redis_error_fails_closed_with_an_error_log(self):
        with patch.object(
            beat_locks, "_redis", side_effect=RedisConnectionError("refused")
        ), self.assertLogs("AutoGrader.beat_locks", "ERROR") as logs:
            summary = self.locked()()

        self.assertEqual(summary, f"{self.name}: {SKIPPED_CACHE_ERROR}.")
        self.assertEqual(self.calls, [])
        self.assertIn(self.name, logs.output[0])
        self.assertIn(lock_key(self.name), logs.output[0])

    def test_two_concurrent_runs_do_the_work_once(self):
        """Real threads, released together; the one that gets the lock waits
        until the other has returned, so the two really overlap."""
        other_returned = threading.Event()

        def body():
            other_returned.wait(timeout=10)
            return "did the work"

        task = self.locked(body=body)

        def run(i):
            try:
                return task()
            finally:
                other_returned.set()

        results, errors = run_concurrently(
            run, 2, test=self, name="h65-run", uses_db=False
        )

        self.assertEqual(errors, [])
        self.assertEqual(
            sorted(results), sorted(["did the work", f"{self.name}: {SKIPPED_HELD}."])
        )
        self.assertEqual(self.calls, [1])
        self.assertIsNone(self.holder())

    def test_a_lapsed_run_cannot_extend_a_newer_runs_lock(self):
        """Compare-and-extend: the heartbeat of a run whose lock lapsed must
        not keep a newer run's lock alive."""
        lapsed = BeatLock(self.name, 60, 60)
        self.assertTrue(lapsed.acquire("lapsed-run"))
        beat_locks._redis().delete(lock_key(self.name))
        newer = BeatLock(self.name, 1, 1)
        self.assertTrue(newer.acquire("newer-run"))
        ttl_before = beat_locks._redis().pttl(lock_key(self.name))

        self.assertFalse(lapsed.extend())
        self.assertLessEqual(beat_locks._redis().pttl(lock_key(self.name)), ttl_before)
        self.assertEqual(self.holder(), "newer-run")

    def test_every_run_has_its_own_token(self):
        tokens = []

        def body():
            tokens.append(self.holder())

        task = self.locked(body=body)
        task()
        task()
        self.assertEqual(len(set(tokens)), 2, tokens)

    def test_a_ttl_above_max_hold_is_capped_at_max_hold(self):
        """No scheduled task passes ttl > max_hold since 1a's N1, so this is
        the cap's only test (mutant L9)."""
        seen = {}

        def body():
            seen["pttl"] = beat_locks._redis().pttl(lock_key(self.name))

        task = self.locked(max_hold=2, ttl=600, body=body)
        self.assertEqual(task.single_instance.ttl_seconds, 2)
        task()
        self.assertGreater(seen["pttl"], 0)
        self.assertLessEqual(seen["pttl"], 2000)


class HeartbeatTests(LockTestCase):
    def test_a_live_run_keeps_its_lock_past_the_ttl(self):
        seen = {}

        def body():
            seen["token"] = self.holder()
            time.sleep(2.5)  # 2.5 TTLs
            seen["later"] = self.holder()

        self.locked(max_hold=30, ttl=1, body=body)()

        self.assertIsNotNone(seen["token"])
        self.assertEqual(seen["later"], seen["token"])
        self.assertIsNone(self.holder())

    def test_a_run_past_max_hold_lets_its_lock_lapse(self):
        seen = {}

        def body():
            deadline = time.monotonic() + 5
            while self.holder() is not None and time.monotonic() < deadline:
                time.sleep(0.05)
            seen["lapsed"] = self.holder() is None

        with self.assertLogs("AutoGrader.beat_locks", "ERROR") as logs:
            self.locked(max_hold=1, ttl=1, body=body)()

        self.assertTrue(seen["lapsed"])
        self.assertIn("maximum hold", logs.output[0])

    def test_a_hung_runs_lock_is_gone_within_max_hold_plus_ttl(self):
        """The worst-case lifetime the schedule guard relies on."""
        max_hold, ttl = 2, 1
        seen = {}

        def body():
            deadline = time.monotonic() + max_hold + ttl + 0.5
            while self.holder() is not None and time.monotonic() < deadline:
                time.sleep(0.05)
            seen["lapsed"] = self.holder() is None

        with self.assertLogs("AutoGrader.beat_locks", "ERROR"):
            self.locked(max_hold=max_hold, ttl=ttl, body=body)()

        self.assertTrue(seen["lapsed"])


class EveryFiveMinuteScaledTests(LockTestCase):
    """1a's probe L: the every-5-minute task's real lock values, scaled 1:60
    so the ratios are exact (its 300 s interval becomes 5 s)."""

    def test_a_hung_run_does_not_swallow_the_next_every_5_minute_run(self):
        lock = declared_lock(task_named(EVERY_5_MIN_TASK))
        assert lock is not None
        max_hold, ttl = lock.max_hold_seconds // 60, lock.ttl_seconds // 60
        self.assertEqual(
            (max_hold * 60, ttl * 60), (lock.max_hold_seconds, lock.ttl_seconds)
        )
        interval = 5
        release = threading.Event()
        ran = []

        @single_instance(max_hold=max_hold, ttl=ttl)
        def h65_scaled_task():
            ran.append(time.monotonic())
            if len(ran) == 1:
                release.wait(interval + 4)  # hung
            return "ran"

        started = time.monotonic()
        hung = threading.Thread(target=h65_scaled_task)
        with self.assertLogs("AutoGrader.beat_locks", "ERROR"):
            hung.start()
            time.sleep(max(0.0, started + interval + 0.1 - time.monotonic()))
            result = h65_scaled_task()
            release.set()
            hung.join(timeout=15)

        self.assertEqual(
            (len(ran), result),
            (2, "ran"),
            "the next scheduled run was skipped: the hung run's lock outlived "
            "the interval",
        )


class RealTaskSkipTests(TestCase):
    """Every guarded Beat task, with its lock held by another run, returns
    the skip summary: the decorator returns before the task body runs."""

    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def test_every_guarded_beat_task_skips_while_another_run_holds_it(self):
        guarded = [
            (task, declared_lock(task_named(task)))
            for task in beat_entries()
            if declared_lock(task_named(task)) is not None
        ]
        # Beta's own count, kept as beta pins it: a guarded task added on
        # beta fails here at the next merge-down until this 21 follows
        # beta's. Epic A's own guarded tasks are named, not counted in.
        names = [task for task, _ in guarded]
        self.assertLessEqual(EPIC_ONLY_GUARDED_BEAT_TASKS, set(names))
        beta_guarded = [n for n in names if n not in EPIC_ONLY_GUARDED_BEAT_TASKS]
        self.assertEqual(len(beta_guarded), 21)
        for task, lock in guarded:
            with self.subTest(task):
                assert lock is not None
                self.assertTrue(BeatLock(task, 60, 60).acquire(f"other-run-for-{task}"))
                with self.assertLogs("AutoGrader.beat_locks", "WARNING") as logs:
                    result = task_named(task).apply()
                self.assertEqual(result.get(), f"{task}: {SKIPPED_HELD}.")
                # The eager Celery task id is the log's "this run" id.
                self.assertIn(f"this run {result.id}:", logs.output[0])
                self.assertNotIn("@", logs.output[0])


class BeatHealthLockTests(TestCase):
    def test_the_watchdog_reports_a_lock_store_that_is_not_answering(self):
        with patch.object(
            beat_locks, "_redis", side_effect=RedisConnectionError("refused")
        ), self.assertLogs("AutoGrader.beat_health", "ERROR"):
            message = check_beat_health()
        self.assertIn("failing closed and skipping", message)

    def test_a_healthy_lock_store_adds_nothing(self):
        self.assertIsNone(beat_locks.lock_store_problem())


# ---------------------------------------------------------------------------
# Isolation: a lock left held by one test never makes a later test skip.
# The runner clears this process's beat-lock keys before every test
# (AutoGrader/testing/beat_locks.py). The two classes below run in this
# order (both TestCase, alphabetical within the module): the first leaves a
# real task's lock held on purpose, and the second runs that task.
# ---------------------------------------------------------------------------

CLEANUP_TASK = "billing.tasks.cleanup_expired_credit_buckets"
_left_held = []


class BeatLockIsolation1LeavesALockHeld(TestCase):
    def test_leaves_the_cleanup_tasks_lock_held(self):
        self.assertTrue(BeatLock(CLEANUP_TASK, 600, 600).acquire("left-by-test-1"))
        _left_held.append(CLEANUP_TASK)


class BeatLockIsolation2TheNextTestStillRuns(TestCase):
    def test_the_task_executes_instead_of_skipping(self):
        if _left_held:  # the leftover is real when the module runs in order
            self.assertIsNone(BeatLock(CLEANUP_TASK, 1, 1).holder())

        result = task_named(CLEANUP_TASK).apply().get()

        self.assertTrue(result.startswith("Credit bucket cleanup:"), result)


class ClearBeatLocksTests(SimpleTestCase):
    def tearDown(self):
        cache.clear()

    def test_the_runner_installed_the_per_test_clearing(self):
        from AutoGrader.testing.beat_locks import is_installed

        self.assertTrue(is_installed())

    def test_it_clears_only_beat_lock_keys(self):
        from AutoGrader.testing.beat_locks import clear_beat_locks

        BeatLock(CLEANUP_TASK, 600, 600).acquire("leftover")
        cache.set("h65-not-a-lock", "kept", 60)

        self.assertEqual(clear_beat_locks(), 1)
        self.assertIsNone(BeatLock(CLEANUP_TASK, 1, 1).holder())
        self.assertEqual(cache.get("h65-not-a-lock"), "kept")
