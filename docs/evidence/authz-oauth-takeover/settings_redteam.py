"""
Isolated lab settings for the AUTHZ-OAUTH Gate-4 replay (task/authz-oauth-takeover).
Own Postgres DB, own Redis key prefix (no FLUSHDB anywhere), locmem email,
MailerLite blanked, auth-scope throttles disabled (throttling is not what
this replay tests). Parametrised by env so the pre-fix baseline and the fix
run as two separate apps.
"""

import os as _os

from AutoGrader.settings import *  # noqa: F401,F403
from AutoGrader.settings import CACHES, DATABASES
from AutoGrader.settings import REST_FRAMEWORK as _RF

_TAG = _os.environ["REPLAY_TAG"]
DATABASES["default"]["NAME"] = f"authz_oauth_{_TAG}"
DATABASES["default"]["CONN_MAX_AGE"] = 0
CACHES["default"] = {
    **CACHES["default"],
    "LOCATION": "redis://127.0.0.1:6379/14",
    "KEY_PREFIX": f"oauthreplay{_TAG}",
}
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
MAILERLITE_API_KEY = ""
_rates = {k: None for k in _RF.get("DEFAULT_THROTTLE_RATES", {})}
REST_FRAMEWORK = {**_RF, "DEFAULT_THROTTLE_RATES": _rates}
