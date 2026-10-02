#!/usr/bin/env python
"""
Fails if gunicorn's --timeout in the Dockerfile CMD and
WEBHOOK_REQUEST_HARD_TIMEOUT_SECONDS in billing/webhooks.py have drifted
apart. These are the same load-bearing value duplicated in two files - see
the comments at both sites for why raising one without the other silently
breaks Stripe webhook claim-staleness timing (a request killed past
gunicorn's timeout needs STRIPE_EVENT_CLAIM_STALE_AFTER, derived from this
same number, to still treat the claim as abandoned rather than merely
slow).

Plain text parsing on purpose, not a Django import: this only needs to
compare two integers, so it runs with no dependencies and no settings/env
vars required.
"""
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = REPO_ROOT / "Dockerfile"
WEBHOOKS = REPO_ROOT / "billing" / "webhooks.py"


def gunicorn_timeout():
    text = DOCKERFILE.read_text()
    match = re.search(r"gunicorn\b.*?--timeout[= ](\d+)", text)
    if not match:
        print(
            f"error: could not find `gunicorn ... --timeout N` in {DOCKERFILE}",
            file=sys.stderr,
        )
        sys.exit(2)
    return int(match.group(1))


def webhook_timeout():
    text = WEBHOOKS.read_text()
    match = re.search(r"WEBHOOK_REQUEST_HARD_TIMEOUT_SECONDS\s*=\s*(\d+)", text)
    if not match:
        print(
            f"error: could not find WEBHOOK_REQUEST_HARD_TIMEOUT_SECONDS in {WEBHOOKS}",
            file=sys.stderr,
        )
        sys.exit(2)
    return int(match.group(1))


def main():
    gunicorn_seconds = gunicorn_timeout()
    webhook_seconds = webhook_timeout()

    if gunicorn_seconds != webhook_seconds:
        print(
            f"FAIL: Dockerfile's gunicorn --timeout is {gunicorn_seconds}s "
            f"but billing/webhooks.py's WEBHOOK_REQUEST_HARD_TIMEOUT_SECONDS "
            f"is {webhook_seconds}s. These must match - see the comments at "
            f"both sites. If you're changing the gunicorn timeout on "
            f"purpose, update WEBHOOK_REQUEST_HARD_TIMEOUT_SECONDS to match "
            f"in the same change."
        )
        return 1

    print(
        f"OK: gunicorn --timeout ({gunicorn_seconds}s) matches "
        f"WEBHOOK_REQUEST_HARD_TIMEOUT_SECONDS ({webhook_seconds}s)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
