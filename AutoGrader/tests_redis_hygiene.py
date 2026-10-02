"""The test suite must not leak Redis keys (see `redis_test_hygiene.py`).

Real Redis, real processes: the behaviours that matter (a SIGTERM'd run
cleans up, a SIGKILL'd run leaves keys that the next run sweeps, a live
sibling run is never touched) cannot be proved with mocks. Every key these
tests create lives under a synthetic pid prefix or a child process's own
prefix and is removed in `tearDown`.

Other runs share this Redis, and under --parallel so do the other workers
of this run: any of them may sweep a DEAD pid's prefix at any moment
(H-94). So no test here asserts that a dead pid's key is still present.
A key that must be seen before it is removed lives under a live pid (a
sleeping sibling, or a killed child not yet reaped).
"""

import os
import signal
import subprocess
import sys
from unittest.mock import patch

import redis
from django.conf import settings
from django.core.cache import cache
from django.test import SimpleTestCase

from AutoGrader import redis_test_hygiene as hygiene
from AutoGrader.cache_generation import bump_generation, generation_key
from AutoGrader.test_cache import TEST_KEY_TTL_SECONDS

CHILD_PRELUDE = """
import os, sys
sys.argv = ["manage.py", "test"]
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "AutoGrader.settings")
import django
django.setup()
from django.core.cache import cache
from AutoGrader.redis_test_hygiene import redis_test_hygiene
"""

CHILD_RUN = (
    CHILD_PRELUDE
    + """
with redis_test_hygiene():
    cache.set("child-key", "v", 300)
    print("ready", flush=True)
    sys.stdin.readline()   # hold the "run" open until the parent says go
"""
)

# Another run's start-of-run sweep, from its own process: what a second
# `manage.py test`, or another parallel worker's hygiene tests, do to the
# shared test Redis while this module's tests are running (H-94).
CHILD_SWEEP = (
    CHILD_PRELUDE
    + """
from AutoGrader.redis_test_hygiene import sweep_dead_prefixes
sweep_dead_prefixes()
print("ready", flush=True)
"""
)

CHILD_KILLABLE = (
    CHILD_PRELUDE
    + """
cache.set("child-key", "v", 300)
print("ready", flush=True)
import time
time.sleep(120)
"""
)


def _raw(db=0):
    location = settings.CACHES["default"]["LOCATION"]
    if isinstance(location, (list, tuple)):
        location = location[0]
    return redis.Redis.from_url(location, db=db)


def _keys(pattern, db=0):
    return sorted(k.decode() for k in _raw(db).scan_iter(match=pattern, count=1000))


def _dead_pid():
    """A pid that is not running, and whose prefix nobody else is using."""
    pid = 4_000_000
    while hygiene.pid_is_alive(pid) or _keys(f"gaplus-t{pid}:*"):
        pid -= 1
    return pid


def _spawn(script, **kwargs):
    child = subprocess.Popen(
        [sys.executable, "-c", script],
        cwd=str(settings.BASE_DIR),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
        **kwargs,
    )
    assert child.stdout is not None
    assert child.stdout.readline().strip() == "ready", "child did not start"
    return child


class RedisHygieneTestCase(SimpleTestCase):
    def setUp(self):
        self._created = []  # (db, key) to remove no matter what
        self._children = []

    def tearDown(self):
        for child in self._children:
            if child.poll() is None:
                child.kill()
            child.wait()
            for stream in (child.stdin, child.stdout):
                if stream:
                    stream.close()
            hygiene.delete_own_keys(child.pid)
        for db, key in self._created:
            _raw(db).delete(key)

    def put(self, key, db=0):
        _raw(db).set(key, "v")
        self._created.append((db, key))

    def spawn(self, script, **kwargs):
        child = _spawn(script, **kwargs)
        self._children.append(child)
        return child

    def another_runs_sweep(self):
        """Run a real sweep from another process, and wait for it."""
        self.assertEqual(self.spawn(CHILD_SWEEP).wait(timeout=60), 0)


class TtlTests(RedisHygieneTestCase):
    def test_a_never_expires_key_gets_a_ttl(self):
        for name, write in {
            "kwarg": lambda k: cache.set(k, "v", timeout=None),
            "positional": lambda k: cache.set(k, "v", None),
            "add": lambda k: cache.add(k, "v", None),
        }.items():
            key = f"hygiene-ttl-{name}"
            self.addCleanup(cache.delete, key)
            write(key)
            ttl = cache.ttl(key)
            self.assertIsNotNone(ttl, f"{name}: key still never expires")
            self.assertGreater(ttl, 0)
            self.assertLessEqual(ttl, TEST_KEY_TTL_SECONDS)

    def test_set_many_with_no_timeout_gets_a_ttl(self):
        self.addCleanup(cache.delete_many, ["hygiene-many-a", "hygiene-many-b"])
        cache.set_many({"hygiene-many-a": 1, "hygiene-many-b": 2}, timeout=None)
        self.assertLessEqual(cache.ttl("hygiene-many-a"), TEST_KEY_TTL_SECONDS)
        self.assertLessEqual(cache.ttl("hygiene-many-b"), TEST_KEY_TTL_SECONDS)

    def test_an_explicit_timeout_is_left_alone(self):
        self.addCleanup(cache.delete, "hygiene-explicit")
        cache.set("hygiene-explicit", "v", 90)
        self.assertLessEqual(cache.ttl("hygiene-explicit"), 90)

    def test_the_default_timeout_is_left_alone(self):
        self.addCleanup(cache.delete, "hygiene-default")
        cache.set("hygiene-default", "v")
        self.assertLessEqual(cache.ttl("hygiene-default"), 300)

    def test_a_generation_counter_never_expires(self):
        """An expired counter reads as generation 1 and revives every stale
        entry, so the TTL must never reach it."""
        scope = "usr"
        entity = "hygiene-counter-check"
        self.addCleanup(cache.delete, generation_key(scope, entity))
        bump_generation(scope, entity)
        bump_generation(scope, entity)
        self.assertIsNone(
            cache.ttl(generation_key(scope, entity)),
            "a generation counter was given a TTL",
        )


class SweepTests(RedisHygieneTestCase):
    def test_delete_own_keys_takes_only_that_exact_prefix(self):
        self.check_delete_own_keys_takes_only_that_exact_prefix()

    def test_delete_own_keys_is_unmoved_by_another_runs_sweep(self):
        """H-94: under --parallel another worker's sweep can run between
        this test's setup and its assertions."""
        self.check_delete_own_keys_takes_only_that_exact_prefix(
            meanwhile=self.another_runs_sweep
        )

    def check_delete_own_keys_takes_only_that_exact_prefix(self, meanwhile=None):
        """Race-free under --parallel (H-94):
        * the prefix under test belongs to a LIVE process (a sibling that
          never touches Redis), so no other run's sweep removes its keys
          before delete_own_keys does;
        * the lookalike's prefix is a dead pid's, which another run's
          sweep MAY remove at any moment, so the test does not ask
          whether it survived. It asks which keys delete_own_keys itself
          unlinked: exactly the prefix's two, never the lookalike.
        """
        sibling = self.spawn("import time; print('ready', flush=True); time.sleep(120)")
        pid = sibling.pid
        hygiene.delete_own_keys(pid)  # a recycled pid's leftovers, if any
        mine = f"gaplus-t{pid}:1:a"
        other_db = f"gaplus-t{pid}:1:b"
        lookalike = f"gaplus-t{pid}9:1:a"  # pid 12 must not match pid 123
        self.put(mine)
        self.put(lookalike)
        self.put(other_db, db=15)  # other logical databases too
        if meanwhile:
            meanwhile()
        # (Present in database 0 at least: where the URL names a database,
        # `db=15` lands in that one too, as it always has for this test.)
        self.assertIn(mine, _keys(f"gaplus-t{pid}:*"), "a live prefix was swept")

        with patch.object(hygiene, "_unlink", wraps=hygiene._unlink) as unlink:
            removed = hygiene.delete_own_keys(pid)

        self.assertEqual(removed, 2)
        self.assertEqual(_keys(f"gaplus-t{pid}:*"), [])
        self.assertEqual(_keys(f"gaplus-t{pid}:*", db=15), [])
        unlinked = sorted(
            key.decode() for call in unlink.call_args_list for key in call.args[1]
        )
        self.assertEqual(unlinked, [mine, other_db])

    def test_sweep_removes_dead_prefixes_and_nothing_else(self):
        dead = _dead_pid()
        sibling = self.spawn("import time; print('ready', flush=True); time.sleep(120)")
        alive_key = f"gaplus-t{sibling.pid}:1:sibling"
        own_key = f"gaplus-t{os.getpid()}:1:hygiene-own"
        other_key = "secreplay-hygiene-test:1:x"  # another session's namespace
        no_pid_key = "gaplus-tnotapid:1:x"
        self.put(f"gaplus-t{dead}:1:a")
        self.put(f"gaplus-t{dead}:1:b", db=15)
        self.put(f"gaplus-t{dead}:", db=10)  # the Celery-style namespace root
        for key in (alive_key, own_key, other_key, no_pid_key):
            self.put(key)

        hygiene.sweep_dead_prefixes()

        for db in (0, 10, 15):
            self.assertEqual(_keys(f"gaplus-t{dead}:*", db=db), [], f"db{db}")
        for key in (alive_key, own_key, other_key, no_pid_key):
            self.assertEqual(_keys(key), [key], f"sweep touched {key}")


class ProcessLifecycleTests(RedisHygieneTestCase):
    def test_sigterm_still_cleans_up_the_runs_own_keys(self):
        child = self.spawn(CHILD_RUN)
        self.assertNotEqual(_keys(f"gaplus-t{child.pid}:*"), [])

        child.send_signal(signal.SIGTERM)
        self.assertEqual(child.wait(timeout=60), 128 + signal.SIGTERM)

        self.assertEqual(
            _keys(f"gaplus-t{child.pid}:*"),
            [],
            "a SIGTERM'd run left its keys behind",
        )

    def test_sigkill_leaves_keys_and_the_next_run_sweeps_them(self):
        self.check_sigkill_leaves_keys_and_the_next_run_sweeps_them()

    def test_sigkill_leaves_keys_whatever_another_run_sweeps_meanwhile(self):
        """H-94: the same race as SweepTests', on the killed child's keys."""
        self.check_sigkill_leaves_keys_and_the_next_run_sweeps_them(
            meanwhile=self.another_runs_sweep
        )

    def check_sigkill_leaves_keys_and_the_next_run_sweeps_them(self, meanwhile=None):
        child = self.spawn(CHILD_KILLABLE)
        pattern = f"gaplus-t{child.pid}:*"
        self.assertNotEqual(_keys(pattern), [])

        child.kill()
        # Race-free under --parallel (H-94): wait for the child to die
        # WITHOUT reaping it. A zombie's pid still exists, so every run's
        # sweep still takes it for a live run and leaves its keys alone
        # until this test has looked at them.
        os.waitid(os.P_PID, child.pid, os.WEXITED | os.WNOWAIT)
        self.assertTrue(hygiene.pid_is_alive(child.pid))
        if meanwhile:
            meanwhile()
        self.assertNotEqual(_keys(pattern), [], "nothing in-process can run on SIGKILL")

        child.wait(timeout=60)  # reaped: the pid is dead to every sweep now
        hygiene.sweep_dead_prefixes()

        self.assertEqual(_keys(pattern), [], "the sweep missed a dead run's keys")

    def test_a_second_run_never_touches_a_live_first_run(self):
        """Two concurrent runs: the second one's start-of-run sweep and its
        teardown must both leave the first one's keys alone."""
        first = self.spawn(CHILD_RUN)
        first_keys = _keys(f"gaplus-t{first.pid}:*")
        self.assertNotEqual(first_keys, [])

        second = self.spawn(CHILD_RUN)  # its sweep runs now, before "ready"
        self.assertEqual(_keys(f"gaplus-t{first.pid}:*"), first_keys)
        second_keys = _keys(f"gaplus-t{second.pid}:*")
        self.assertNotEqual(second_keys, [])

        second.stdin.write("go\n")
        second.stdin.flush()
        self.assertEqual(second.wait(timeout=60), 0)  # clean exit, teardown ran
        self.assertEqual(_keys(f"gaplus-t{second.pid}:*"), [])
        self.assertEqual(
            _keys(f"gaplus-t{first.pid}:*"),
            first_keys,
            "the second run's teardown removed the first run's keys",
        )

        first.stdin.write("go\n")
        first.stdin.flush()
        self.assertEqual(first.wait(timeout=60), 0)
        self.assertEqual(_keys(f"gaplus-t{first.pid}:*"), [])


class RunnerWiringTests(SimpleTestCase):
    def test_the_project_uses_the_hygiene_runner(self):
        self.assertEqual(
            settings.TEST_RUNNER, "AutoGrader.redis_test_runner.RedisHygieneRunner"
        )

    def test_hygiene_failure_never_fails_a_run(self):
        original = hygiene.sweep_dead_prefixes
        hygiene.sweep_dead_prefixes = lambda *a, **k: 1 / 0
        self.addCleanup(setattr, hygiene, "sweep_dead_prefixes", original)
        with self.assertLogs(hygiene.logger, level="WARNING"):
            with hygiene.redis_test_hygiene():
                pass
