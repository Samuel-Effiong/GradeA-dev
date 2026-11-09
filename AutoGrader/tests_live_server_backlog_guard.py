"""
Guard: every LiveServerTestCase in the repository listens with a backlog
that holds its whole burst of concurrent connections.

Django's live test server listens with a backlog of 10
(django/core/servers/basehttp.py, WSGIServer.request_queue_size). The
live-server tests here open 12 to 30 connections at once. If the server
thread's accept loop stalls for a few seconds, which a loaded 4-vCPU CI
runner under coverage can do, the kernel overflows the queue and later
resets the dropped connections. The test then errors with
ConnectionResetError, and nothing about the code under test is wrong.
This broke PR #123's CI run on 2026-11-08
(users.tests_login_lockout.LoginLockoutLiveServerTests); the diagnosis is
in docs/evidence/lockout-live-reset/NOTES.md.

The rule: a live-server class's server must listen with a backlog of at
least max(MIN_BACKLOG, WORKERS). WORKERS alone is not enough, because a
class need not declare its fan-out. ThreadPoolExecutor's default is up to
32 workers, so 128 holds any undeclared burst with room to spare. A class
that declares more than 128 WORKERS needs a bigger backlog still. Use
AutoGrader.testing.live_server.BurstLiveServerThread.

Classes are found by an AST scan of every .py file (migrations,
node_modules, venv and hidden directories skipped) for class definitions
whose bases reach LiveServerTestCase, followed by name to a fixed point so
that a subclass of a subclass in another module is found too. The real
classes are then imported and checked.
"""

import ast
import functools
import importlib
import os

from django.conf import settings
from django.test import LiveServerTestCase, SimpleTestCase

MIN_BACKLOG = 128
LIVE_SERVER_ROOTS = {"LiveServerTestCase", "StaticLiveServerTestCase"}
SKIPPED_DIRS = {"migrations", "node_modules", "venv"}

#: The live-server classes on beta when this guard was written. The scan
#: must find at least these, so it cannot go blind without failing.
KNOWN_LIVE_SERVER_CLASSES = {
    "users.tests_login_lockout.LoginLockoutLiveServerTests",
    "users.tests_activity_middleware_load.HeartbeatThrottleLiveServerTests",
    "students.tests_async_edit_path.UpdateAsyncConcurrencyLiveHTTPTest",
    "students.tests_post_grading_submission_lock.PostGradingLockLiveHTTPTest",
}


def _base_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def _class_definitions():
    """(module dotted path, class name, base names) for every class in the repo."""
    root = settings.BASE_DIR
    for folder, subfolders, names in os.walk(root):
        subfolders[:] = sorted(
            n for n in subfolders if n not in SKIPPED_DIRS and not n.startswith(".")
        )
        for name in sorted(names):
            if not name.endswith(".py"):
                continue
            path = os.path.join(folder, name)
            rel = os.path.relpath(path, root)
            with open(path, encoding="utf-8") as fh:
                try:
                    tree = ast.parse(fh.read())
                except SyntaxError:
                    continue
            module = rel[: -len(".py")].replace(os.sep, ".")
            if module.endswith(".__init__"):
                module = module[: -len(".__init__")]
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef):
                    bases = {_base_name(b) for b in node.bases} - {None}
                    yield module, node.name, bases


@functools.lru_cache(maxsize=None)
def live_server_classes():
    """Every LiveServerTestCase subclass defined in the repository, imported.

    Cached: the scan reads every .py file (about 4 s), and both tests use it.
    """
    definitions = list(_class_definitions())
    family = set(LIVE_SERVER_ROOTS)
    while True:
        grown = family | {name for _, name, bases in definitions if bases & family}
        if grown == family:
            break
        family = grown
    found = {}
    for module, name, _bases in definitions:
        if name in family and name not in LIVE_SERVER_ROOTS:
            cls = getattr(importlib.import_module(module), name, None)
            if isinstance(cls, type) and issubclass(cls, LiveServerTestCase):
                found[f"{module}.{name}"] = cls
    return dict(found)


def listen_backlog(cls):
    return cls.server_thread_class.server_class.request_queue_size


class LiveServerBacklogGuard(SimpleTestCase):
    def test_every_live_server_class_holds_its_burst(self):
        problems = []
        for dotted, cls in sorted(live_server_classes().items()):
            required = max(MIN_BACKLOG, getattr(cls, "WORKERS", 0))
            backlog = listen_backlog(cls)
            if backlog < required:
                problems.append(
                    f"{dotted}: listen backlog {backlog} < required {required}"
                )
        self.assertEqual(
            problems,
            [],
            "live-server classes whose listen queue cannot hold their burst; "
            "set server_thread_class = BurstLiveServerThread "
            "(AutoGrader.testing.live_server)",
        )

    def test_the_scan_finds_the_known_live_server_classes(self):
        missing = KNOWN_LIVE_SERVER_CLASSES - set(live_server_classes().keys())
        self.assertEqual(missing, set(), "the AST scan missed live-server classes")
