"""
Audit requirements.txt and requirements-dev.txt for known vulnerabilities (H-258, H-262).

    python -m pip install "pip-audit==2.9.0"
    python scripts/pip_audit_gate.py

Runs pip-audit on both requirements files with --strict. Every line there is
pinned with ==, so --no-deps --disable-pip audits exactly what is listed
without building a virtualenv.

The advisories the project has accepted for now are in .pip-audit-ignore.toml
(production) and .pip-audit-ignore-dev.toml (dev-only), grouped by package,
one advisory id per line:

    [[ignore]]
    package = "gunicorn"
    ids = [
        "PYSEC-2026-1433",
        "PYSEC-2026-1434",
    ]
    reason = "Why it is accepted for now."
    review_by = 2026-11-30

Each id is passed to pip-audit as --ignore-vuln. An entry without a package,
ids, a reason or a review date, an id listed twice, and an entry whose review
date has passed are refused before pip-audit runs: an exception cannot stay in
place silently. A new advisory on a package already in the file is not covered
by it, so the job goes red.

Exit status: pip-audit's own (0 = nothing found beyond the accepted list), or
2 when an ignore file is refused.
"""

from __future__ import annotations

import datetime
import pathlib
import subprocess
import sys

import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent
IGNORE_FILE = ROOT / ".pip-audit-ignore.toml"
IGNORE_FILE_DEV = ROOT / ".pip-audit-ignore-dev.toml"
REQUIREMENTS = "requirements.txt"
REQUIREMENTS_DEV = "requirements-dev.txt"
#: Pinned here and in .github/workflows/audit.yml (a test keeps them equal).
PIP_AUDIT_VERSION = "2.9.0"


class IgnoreFileError(Exception):
    pass


def accepted_ids(path: pathlib.Path, today: datetime.date) -> list[str]:
    """The advisory ids the file accepts, sorted. Raises IgnoreFileError for an
    entry that is incomplete, out of date, or repeats an id."""
    with open(path, "rb") as f:
        entries = tomllib.load(f).get("ignore", [])
    seen: set[str] = set()
    for entry in entries:
        package = entry.get("package")
        if not isinstance(package, str) or not package.strip():
            raise IgnoreFileError("an entry has no package")
        ids = entry.get("ids")
        if not isinstance(ids, list) or not ids:
            raise IgnoreFileError(f"{package}: no advisory ids")
        reason = entry.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise IgnoreFileError(f"{package}: no reason")
        review_by = entry.get("review_by")
        if not isinstance(review_by, datetime.date) or isinstance(
            review_by, datetime.datetime
        ):
            raise IgnoreFileError(f"{package}: review_by must be a date (YYYY-MM-DD)")
        if review_by < today:
            raise IgnoreFileError(
                f"{package}: the review date {review_by.isoformat()} has passed; "
                "upgrade, or review the exception and set a new date"
            )
        for advisory in ids:
            if advisory in seen:
                raise IgnoreFileError(f"{advisory} is listed twice")
            seen.add(advisory)
    return sorted(seen)


def check_version(installed: str) -> None:
    if installed != PIP_AUDIT_VERSION:
        raise IgnoreFileError(
            f"pip-audit {installed} is not the pinned {PIP_AUDIT_VERSION}"
        )


def audit_command(requirements: str, ids: list[str]) -> list[str]:
    command = [
        sys.executable,
        "-m",
        "pip_audit",
        "-r",
        requirements,
        "--strict",
        "--no-deps",
        "--disable-pip",
        "--progress-spinner",
        "off",
    ]
    for advisory in ids:
        command += ["--ignore-vuln", advisory]
    return command


def main() -> int:
    from importlib.metadata import version

    today = datetime.date.today()
    try:
        check_version(version("pip-audit"))
        prod_ids = accepted_ids(IGNORE_FILE, today)
        dev_ids = accepted_ids(IGNORE_FILE_DEV, today)
    except IgnoreFileError as e:
        print(f"pip-audit gate: {e}", file=sys.stderr)
        return 2

    print(
        f"pip-audit gate: {len(prod_ids)} accepted advisories from "
        f"{IGNORE_FILE.name}, {len(dev_ids)} from {IGNORE_FILE_DEV.name}"
    )

    rc = subprocess.run(  # nosec B603  # fixed argv built above, no shell
        audit_command(REQUIREMENTS, prod_ids), cwd=ROOT
    ).returncode
    if rc != 0:
        return rc

    all_ids = sorted(set(prod_ids) | set(dev_ids))
    rc = subprocess.run(  # nosec B603
        audit_command(REQUIREMENTS_DEV, all_ids), cwd=ROOT
    ).returncode
    return rc


if __name__ == "__main__":
    sys.exit(main())
