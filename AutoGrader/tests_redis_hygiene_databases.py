"""H-97: the Redis test hygiene really visits each of the 16 databases.

`redis_test_hygiene._clients()` built its "one client per database" with
`redis.Redis.from_url(location, db=n)`. When the URL names a database
(`redis://host:6379/0`, the local and the CI layout) redis-py takes the
database from the URL and ignores `db=`, so all 16 clients were on the
URL's database. The teardown and the dead-pid sweep never reached the keys
that `real_redis_caches("redis://.../<n>")` suites write to databases 11
to 15: a dead run's keys stayed there for good, and a later process that
was given the same pid would have found them under its own prefix.

Real Redis. Every key here is written and read by its exact name, under a
synthetic dead pid's prefix or a live sibling's, in database 13 (no other
module uses it), and is removed in tearDown. A dead pid's key is only ever
asserted to be GONE; a key that must be seen lives under a live pid (the
rule H-94 set for these tests).
"""

import os
import subprocess
import sys

from django.conf import settings
from django.test import SimpleTestCase

from AutoGrader import redis_test_hygiene as hygiene

OTHER_DB = 13

CHILD_RUN = """
import os, sys
sys.argv = ["manage.py", "test"]
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "AutoGrader.settings")
import django
django.setup()
from AutoGrader.redis_test_hygiene import redis_test_hygiene
with redis_test_hygiene():
    print("ready", flush=True)
"""


def clients():
    return dict(hygiene._clients())


def database_of(client):
    return client.connection_pool.connection_kwargs.get("db")


class EveryDatabaseIsVisitedTests(SimpleTestCase):
    def setUp(self):
        self.clients = clients()
        self._created = []
        self._children = []

    def tearDown(self):
        for child in self._children:
            if child.poll() is None:
                child.kill()
            child.wait()
            for stream in (child.stdin, child.stdout):
                if stream:
                    stream.close()
        for db, key in self._created:
            self.clients[db].delete(key)

    def put(self, key, db):
        self.clients[db].set(key, "v")
        self._created.append((db, key))

    def exists(self, key, db):
        return bool(self.clients[db].exists(key))

    def dead_pid(self):
        pid = 4_100_000
        while hygiene.pid_is_alive(pid):
            pid -= 1
        return pid

    def sibling(self):
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                "import time; print('ready', flush=True); time.sleep(120)",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        self._children.append(child)
        assert child.stdout is not None
        assert child.stdout.readline().strip() == "ready"
        return child

    # --- The clients ---------------------------------------------------------

    def test_each_client_is_on_its_own_database(self):
        self.assertEqual(sorted(self.clients), list(range(16)))
        for db, client in self.clients.items():
            with self.subTest(db=db):
                self.assertEqual(database_of(client), db)

    def test_a_key_put_through_one_client_is_not_seen_through_another(self):
        # Under a live pid: no run's sweep removes it meanwhile (H-94).
        key = f"gaplus-t{self.sibling().pid}:1:h97-separate"
        self.put(key, OTHER_DB)

        self.assertTrue(self.exists(key, OTHER_DB))
        self.assertFalse(self.exists(key, 0))

    # --- The sweep and the teardown reach the other databases ----------------

    def test_a_dead_pids_key_in_another_database_is_swept(self):
        dead = self.dead_pid()
        key = f"gaplus-t{dead}:1:h97-left-behind"
        self.put(key, OTHER_DB)

        hygiene.sweep_dead_prefixes()

        self.assertFalse(self.exists(key, OTHER_DB))

    def test_a_live_pids_key_in_another_database_is_left_alone(self):
        sibling = self.sibling()
        key = f"gaplus-t{sibling.pid}:1:h97-live"
        self.put(key, OTHER_DB)

        hygiene.sweep_dead_prefixes()

        self.assertTrue(self.exists(key, OTHER_DB))

    def test_delete_own_keys_reaches_another_database(self):
        sibling = self.sibling()
        in_default = f"gaplus-t{sibling.pid}:1:h97-a"
        in_other = f"gaplus-t{sibling.pid}:1:h97-b"
        self.put(in_default, 0)
        self.put(in_other, OTHER_DB)

        removed = hygiene.delete_own_keys(sibling.pid)

        self.assertEqual(removed, 2)
        self.assertFalse(self.exists(in_default, 0))
        self.assertFalse(self.exists(in_other, OTHER_DB))

    # --- A reused pid ----------------------------------------------------------

    def test_the_next_run_removes_a_dead_runs_keys_before_its_pid_is_reused(self):
        """A process that is later given a dead run's pid has that run's
        prefix. The dead run's keys (a generation counter has no TTL) must
        be gone before that can happen: the next run's start-of-run sweep
        removes them, in whichever database they were left."""
        dead = self.dead_pid()
        counter = f"gaplus-t{dead}:1:cachegen:usr:h97-stale-counter"
        entry = f"gaplus-t{dead}:1:h97-stale-entry"
        self.put(counter, OTHER_DB)
        self.put(entry, 14)

        run = subprocess.run(
            [sys.executable, "-c", CHILD_RUN],
            cwd=str(settings.BASE_DIR),
            capture_output=True,
            text=True,
            timeout=120,
            env={**os.environ},
        )

        self.assertEqual(run.returncode, 0, run.stderr)
        self.assertFalse(self.exists(counter, OTHER_DB))
        self.assertFalse(self.exists(entry, 14))

    # --- Other URL shapes ------------------------------------------------------

    def test_the_database_is_set_whatever_the_url_says(self):
        for location in (
            "redis://127.0.0.1:6379/0",
            "redis://127.0.0.1:6379/7",
            "redis://127.0.0.1:6379",
            "redis://127.0.0.1:6379/",
            "redis://127.0.0.1:6379/0?socket_timeout=5",
            "redis://127.0.0.1:6379?db=3",
            "rediss://cache.example.com:6380/2",
            "unix:///tmp/redis.sock?db=4",
        ):
            with self.subTest(location=location):
                for db in (0, 5, 15):
                    client = hygiene.client_for(location, db)
                    self.assertEqual(database_of(client), db)
