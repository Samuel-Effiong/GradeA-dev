import os

from celery import Celery

# Set the default Django settings module for the 'celery' program.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "AutoGrader.settings")

app = Celery("AutoGrader")

# Using a string here means the worker doesn't have to serialize
# the configuration object to child processes.
# - namespace='CELERY' means all celery-related configuration keys
#   should have a `CELERY_` prefix.
app.config_from_object("django.conf:settings", namespace="CELERY")

# Load task modules from all registered Django apps.
app.autodiscover_tasks()

# AutoGrader isn't a Django app, so autodiscover_tasks() above (which only
# scans each INSTALLED_APPS entry's tasks.py) never finds this module on
# its own - explicit import, same reasoning as celery_signals above.
# Registers the before_task_publish/task_prerun/task_postrun handlers that
# propagate the request-id correlation id (see AutoGrader.request_context)
# across every .delay()/.apply_async() call, in both web and worker
# processes - both import this module (via AutoGrader/__init__.py), and the
# signal handlers below are process-global once connected.
from . import beat_health  # noqa: E402,F401
from . import celery_signals  # noqa: E402,F401
