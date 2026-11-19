"""Read settings as a fresh start-up does, in a new interpreter.

The Phase 2 switches are read once when settings load, so a test of their
default has to load the settings in a new process. The local .env may set
them, and settings read .env into the environment at start-up, so the given
switches are removed again right after .env is read. Then only the values a
test passes in `env` are set. The result does not depend on this machine's
.env or on this run's environment.

`under_test_runner` picks how the process looks: as `manage.py test` (True)
or as a server such as `manage.py runserver` (False). The new process never
imports pytest.
"""

import json
import os
import subprocess  # nosec B404: a fixed command, no shell
import sys

from django.conf import settings

PHASE2_SWITCHES = (
    "BILLING_HOLDS_ENABLED",
    "CREDIT_REQUESTS_ENABLED",
    "TAGS_ENABLED",
    "STUDENT_DATA_TOOLS_ENABLED",
)

_SCRIPT = """
import json, os, sys
import environ

strip = json.loads(sys.argv[1])
given = json.loads(sys.argv[2])
names = json.loads(sys.argv[3])
sys.argv = ["manage.py", sys.argv[4]]
_read = environ.Env.read_env

def _read_then_strip(cls, *args, **kwargs):
    result = _read(*args, **kwargs)
    for name in strip:
        os.environ.pop(name, None)
    os.environ.update(given)
    return result

environ.Env.read_env = classmethod(_read_then_strip)
from django.conf import settings
print(json.dumps({name: getattr(settings, name) for name in names}))
"""


def fresh_settings(names, *, env=None, under_test_runner=False, strip=None):
    """The given settings as a fresh start-up reads them, as a dict.

    `strip` names the variables removed from the environment (default: the
    four Phase 2 switches); `env` then sets some of them."""
    strip = list(PHASE2_SWITCHES if strip is None else strip)
    given = dict(env or {})
    child_env = {k: v for k, v in os.environ.items() if k not in strip}
    child_env["DJANGO_SETTINGS_MODULE"] = "AutoGrader.settings"
    done = subprocess.run(  # nosec B603: a fixed command, no shell
        [
            sys.executable,
            "-c",
            _SCRIPT,
            json.dumps(strip),
            json.dumps(given),
            json.dumps(list(names)),
            "test" if under_test_runner else "runserver",
        ],
        cwd=settings.BASE_DIR,
        env=child_env,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if done.returncode != 0:
        raise AssertionError(done.stderr[-2000:])
    return json.loads(done.stdout.strip().splitlines()[-1])
