# flake8: noqa: E501
"""H-263: judge gunicorn's one GET /health (Hardening Engineer, 4d).

    python -I health_verdict.py <http code> <body file>

Exit 0 means gunicorn 22.0.0 booted with the Dockerfile's flags and served the
request through our WSGI app:

* 200: served and healthy.
* 503: served, and the body must name the database or the cache check as the one
  that failed, with nothing else failing (the Senior Manager's ruling,
  2026-10-27). A 503 for any other reason, or one whose body does not say, is a
  stop.

Anything else, including no answer and an unparseable body, is a stop. The
response passes through users.renderers.APIJSONRenderer, which wraps the
payload, so the `checks` map is looked for anywhere in the decoded JSON.
"""

import json
import pathlib
import sys

ENVIRONMENT_CHECKS = {"database", "cache"}


def find_checks(node):
    """Every mapping stored under a "checks" key, at any depth."""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "checks" and isinstance(value, dict):
                yield value
            yield from find_checks(value)
    elif isinstance(node, list):
        for item in node:
            yield from find_checks(item)


def verdict(code, body):
    if code == "200":
        return 0, "/health answered 200: served and healthy"
    if code != "503":
        return 1, f"/health answered {code!r}: not a served health answer"
    try:
        payload = json.loads(body)
    except ValueError as exc:
        return 1, f"/health answered 503 with a body that is not JSON ({exc})"
    maps = list(find_checks(payload))
    if len(maps) != 1:
        return (
            1,
            f"/health answered 503 with {len(maps)} 'checks' maps in its body; expected one",
        )
    checks = maps[0]
    failing = {name: value for name, value in checks.items() if value != "ok"}
    if not failing:
        return 1, "/health answered 503 but its body names no failing check"
    outside = sorted(set(failing) - ENVIRONMENT_CHECKS)
    if outside:
        return (
            1,
            f"/health answered 503 and the failing checks are not only the environment's: {outside}",
        )
    named = ", ".join(f"{name} ({value})" for name, value in sorted(failing.items()))
    return (
        0,
        f"/health answered 503, failing only on the environment's own checks: {named}",
    )


def main():
    code, body_path = sys.argv[1], pathlib.Path(sys.argv[2])
    body = body_path.read_text(errors="replace") if body_path.exists() else ""
    status, line = verdict(code.strip(), body)
    print(f"{'health ok' if status == 0 else 'HEALTH STOP'}: {line}")
    return status


if __name__ == "__main__":
    sys.exit(main())
