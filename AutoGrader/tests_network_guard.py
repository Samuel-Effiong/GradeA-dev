"""H-39: a test that forgets to mock a third-party call must fail fast and
locally, not silently succeed against real credentials. See
`AutoGrader/network_guard.py` and `docs/HARDENING_BACKLOG.md` H-39.
"""

import os
import socket

import redis
from django.conf import settings
from django.db import connection
from django.test import SimpleTestCase

from AutoGrader.network_guard import BlockedNetworkCallError, block_real_network_calls

#: RFC 5737 TEST-NET-1 — reserved for documentation, never routable. The
#: guard must refuse this before any syscall touches the wire, so the test
#: is fast and deterministic regardless of this machine's real connectivity.
_UNROUTABLE = ("192.0.2.1", 81)


class NetworkGuardTests(SimpleTestCase):
    databases = {"default"}

    def setUp(self):
        # The runner already holds the guard open, so "before" is whatever is
        # installed now; a nested use must put exactly that back.
        self._before_connect = socket.socket.connect
        self._before_connect_ex = socket.socket.connect_ex

    def test_blocks_an_unlisted_real_address(self):
        with block_real_network_calls():
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.addCleanup(sock.close)
            with self.assertRaises(BlockedNetworkCallError):
                sock.connect(_UNROUTABLE)

    def test_connect_ex_is_also_guarded(self):
        with block_real_network_calls():
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.addCleanup(sock.close)
            with self.assertRaises(BlockedNetworkCallError):
                sock.connect_ex(_UNROUTABLE)

    def test_loopback_is_never_blocked(self):
        # A closed port on loopback must fail with a real connection error,
        # never BlockedNetworkCallError — proving the guard let it through
        # to the actual syscall instead of refusing it up front.
        with block_real_network_calls():
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.addCleanup(sock.close)
            with self.assertRaises(ConnectionRefusedError):
                sock.connect(("127.0.0.1", 1))

    def test_configured_database_is_allowed(self):
        with block_real_network_calls():
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                self.assertEqual(cursor.fetchone(), (1,))

    def test_configured_redis_is_allowed(self):
        location = settings.CACHES["default"]["LOCATION"]
        if isinstance(location, (list, tuple)):
            location = location[0]
        with block_real_network_calls():
            client = redis.Redis.from_url(location)
            self.assertTrue(client.ping())

    def test_escape_hatch_disables_the_guard(self):
        os.environ["H39_ALLOW_REAL_NETWORK"] = "1"
        self.addCleanup(os.environ.pop, "H39_ALLOW_REAL_NETWORK", None)
        with block_real_network_calls():
            self.assertIs(socket.socket.connect, self._before_connect)

    def test_sockets_are_restored_after_the_context_exits(self):
        with block_real_network_calls():
            pass
        self.assertIs(socket.socket.connect, self._before_connect)
        self.assertIs(socket.socket.connect_ex, self._before_connect_ex)

    def test_unix_sockets_are_never_touched(self):
        with block_real_network_calls():
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            self.addCleanup(sock.close)
            with self.assertRaises(FileNotFoundError):
                sock.connect("/tmp/h39-network-guard-test-does-not-exist.sock")
