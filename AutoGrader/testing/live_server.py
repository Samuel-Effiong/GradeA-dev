"""
A live test server whose listen queue holds a test's whole burst.

Django's live test server listens with a backlog of 10
(django/core/servers/basehttp.py, WSGIServer.request_queue_size). A test
that opens more connections than that at once relies on the server thread
accepting them quickly. If the accept loop stalls for a few seconds, which
a loaded CI runner under coverage can do, the kernel overflows the queue
and later resets the dropped connections. The test then errors with
ConnectionResetError, and the code under test did nothing wrong.

A reset connection was never accepted, so its request never reached
Django. It is a harness failure, not a security or correctness signal.
Retrying would hide a real server crash, so the queue is made big enough
instead. Production is unaffected: gunicorn listens with its default
backlog of 2048.

Use it on every LiveServerTestCase:

    class MyLiveTests(LiveServerTestCase):
        server_thread_class = BurstLiveServerThread

AutoGrader.tests_live_server_backlog_guard enforces this. Diagnosis:
docs/evidence/lockout-live-reset/NOTES.md.
"""

from django.core.servers.basehttp import ThreadedWSGIServer
from django.test.testcases import LiveServerThread

#: Holds any burst the suite sends (the largest is 30) with room to spare.
LIVE_SERVER_BACKLOG = 128


class BurstWSGIServer(ThreadedWSGIServer):
    request_queue_size = LIVE_SERVER_BACKLOG


class BurstLiveServerThread(LiveServerThread):
    server_class = BurstWSGIServer
