"""Every Celery worker consumes both task queues however it is started.

Railway's worker does not run scripts/start-worker.sh. Its custom start
command (owner, 2026-11-23) is exactly RAILWAY_WORKER_ARGS below, with no -Q.
Celery's default consume set was then only the default queue "celery", so
the upload quote measure (routed to "quote_measure") never ran and quotes
stayed measuring. The app now declares both queues (CELERY_TASK_QUEUES), and
a worker started without -Q consumes every declared queue.
"""

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from django.conf import settings
from django.test import SimpleTestCase

from AutoGrader.celery import app

#: The Railway worker's start command (owner, 2026-11-23), after `celery`.
RAILWAY_WORKER_ARGS = [
    "-A",
    "AutoGrader",
    "worker",
    "-l",
    "info",
    "--pool=gevent",
    "--concurrency=200",
    "--prefetch-multiplier=1",
]

# Runs celery's own `worker` command on the given arguments in a separate
# process, with WorkController.start stubbed out: celery parses the command
# line, builds the worker (its queue selection included) and stops before
# connecting to the broker. A separate process keeps the worker's global side
# effects (trace optimizations, signals, Django fixups) out of this test run.
_PROBE = """
import json, sys
import django
django.setup()
from unittest.mock import patch
from celery.bin.celery import celery as cli
from celery.worker.worker import WorkController
seen = {}
def start(self):
    seen["worker"] = self
with patch.object(WorkController, "start", start):
    try:
        cli.main(args=sys.argv[1:], standalone_mode=False)
    except SystemExit:
        pass
w = seen["worker"]
print(json.dumps({
    "consume": sorted(w.app.amqp.queues.consume_from),
    "pool": w.pool_cls.__module__,
    "concurrency": w.concurrency,
}))
"""

BOTH = ["celery", "quote_measure"]


def _worker_started_with(args):
    env = {
        **os.environ,
        "PYTHONPATH": str(settings.BASE_DIR),
        "DJANGO_SETTINGS_MODULE": os.environ["DJANGO_SETTINGS_MODULE"],
    }
    result = subprocess.run(
        [sys.executable, "-c", _PROBE, *args],
        cwd=settings.BASE_DIR,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(result.stderr[-3000:])
    return json.loads(result.stdout.strip().splitlines()[-1])


class DeclaredQueues(SimpleTestCase):
    def test_the_app_declares_both_queues(self):
        self.assertEqual(sorted(q.name for q in app.conf.task_queues), BOTH)

    def test_the_default_queue_is_still_celery(self):
        self.assertEqual(app.conf.task_default_queue, "celery")

    def test_each_queue_keeps_the_binding_celery_created_for_it(self):
        # Before the declaration Celery made both queues itself: exchange of
        # the queue's own name, direct, routing key = the name. Keeping those
        # means messages already waiting still reach the queue while web and
        # worker roll over to this release in any order.
        for queue in app.conf.task_queues:
            with self.subTest(queue=queue.name):
                self.assertEqual(queue.exchange.name, queue.name)
                self.assertEqual(queue.exchange.type, "direct")
                self.assertEqual(queue.routing_key, queue.name)


class RailwayWorkerCommand(SimpleTestCase):
    def test_the_railway_command_consumes_both_queues(self):
        worker = _worker_started_with(RAILWAY_WORKER_ARGS)
        self.assertEqual(worker["consume"], BOTH)
        # The probe really built the Railway worker, not a default one.
        self.assertEqual(worker["pool"], "celery.concurrency.gevent")
        self.assertEqual(worker["concurrency"], 200)

    def test_start_worker_script_consumes_both_queues(self):
        script = (Path(settings.BASE_DIR) / "scripts" / "start-worker.sh").read_text()
        # No -Q: the script takes the same consume set as every other worker,
        # from the declared queues, so a queue added later cannot be missed.
        self.assertNotIn("-Q", script)
        worker = _worker_started_with(["-A", "AutoGrader", "worker"])
        self.assertEqual(worker["consume"], BOTH)


class RoutingUnchanged(SimpleTestCase):
    def route(self, name):
        amqp: Any = app.amqp
        return amqp.router.route({}, name)["queue"].name

    def test_the_measure_goes_to_its_own_queue(self):
        self.assertEqual(
            self.route("students.tasks.measure_quote_file"), "quote_measure"
        )

    def test_other_tasks_go_to_the_default_queue(self):
        self.assertEqual(self.route("students.tasks.grade_engine_async"), "celery")

    def test_every_beat_entry_lands_on_a_declared_queue(self):
        declared = {q.name for q in app.conf.task_queues}
        schedule: dict[str, Any] = settings.CELERY_BEAT_SCHEDULE
        for key, entry in schedule.items():
            with self.subTest(entry=key):
                queue = (entry.get("options") or {}).get("queue")
                self.assertIn(queue or self.route(entry["task"]), declared)
