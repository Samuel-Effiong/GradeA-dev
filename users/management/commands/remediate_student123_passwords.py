"""
H-3: neutralise accounts whose password is the known literal ``student123!``.

Production data (read-only check): 116 of 122 student accounts authenticate
with that literal, all active. ``last_login`` is NULL on all of them, but this
app never sets ``last_login``, so that does not show nobody signed in. Check
the dry-run's UserActivity count (and auth logs) before ``--execute``. Accounts
with a real mailbox recover through password reset; @student.local addresses
are not mailboxes, so those need a teacher or admin to set a new password.

    python manage.py remediate_student123_passwords             # dry run
    python manage.py remediate_student123_passwords --execute   # write

Guarantees:
- Dry-run is the default and writes nothing (no DB rows, no report file).
- Only accounts for which ``check_password("student123!")`` is true are
  touched, and only their ``password`` column. Nothing is deleted;
  enrollments, submissions and every other column are untouched.
- Idempotent: after a reset the literal no longer verifies, so a re-run finds
  nothing to do.
- Each account is reset in its own transaction by compare-and-set on the hash
  that was verified, so an interruption leaves every account either fully
  reset or fully untouched, a re-run finishes the rest, and an account whose
  password changed since the scan (a user set a real one) is skipped, never
  clobbered.

Audit record: the ``audit`` app is not on ``beta`` yet, so each reset is
appended as one JSON line to ``--report`` (default
``h3_student123_remediation_<UTC timestamp>.jsonl``) and logged on the
``users.remediation`` logger. The report holds emails (PII); it never holds
hashes or the literal. The report line is written AFTER the row commits, so
the database, not the report, is the source of truth if the process dies
between the two; re-running is safe.
"""

import json
import logging
import os
from datetime import datetime, timezone

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password, make_password
from django.core.management.base import BaseCommand
from django.db import transaction

LITERAL = "student123!"  # pragma: allowlist secret

logger = logging.getLogger("users.remediation")


def _algorithm(encoded):
    return encoded.split("$", 1)[0] if encoded else ""


class Command(BaseCommand):
    help = (
        "Set an unusable password on every account whose password is the "
        "literal 'student123!'. Dry-run unless --execute is given."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--execute",
            action="store_true",
            help="Actually write. Without this flag nothing is changed.",
        )
        parser.add_argument(
            "--report",
            help="Path of the JSONL audit report (--execute only). "
            "Default: h3_student123_remediation_<UTC timestamp>.jsonl in "
            "the current directory.",
        )

    def handle(self, *args, **options):
        execute = options["execute"]
        User = get_user_model()
        now = datetime.now(timezone.utc)

        scanned = 0
        matches = []
        for user in User.objects.only("id", "email", "password").iterator():
            scanned += 1
            if not user.has_usable_password():
                continue
            # The hashers-level check with no setter: CustomUser.check_password
            # would re-hash and SAVE a hash that merely needs an upgrade,
            # which would make the dry run write.
            if check_password(LITERAL, user.password, setter=None):
                matches.append((user.pk, user.password))

        self.stdout.write(
            f"Mode: {'EXECUTE' if execute else 'DRY-RUN (no changes will be written)'}"
        )
        self.stdout.write(f"Accounts scanned: {scanned}")
        self.stdout.write(f"Accounts with the literal password: {len(matches)}")

        details = list(
            User.objects.filter(pk__in=[pk for pk, _ in matches]).values(
                "id", "email", "user_type", "is_active", "last_login"
            )
        )
        by_type = {}
        for d in details:
            by_type[d["user_type"]] = by_type.get(d["user_type"], 0) + 1
        real_email = sum(
            1 for d in details if not d["email"].endswith("@student.local")
        )
        # last_login is NOT maintained by this app (simplejwt's
        # UPDATE_LAST_LOGIN is off and no view sets it), so a NULL there
        # proves nothing. UserActivity rows, written on login and on
        # authenticated activity, are the real "someone signed in" signal.
        from users.models import UserActivity

        active_ids = set(
            UserActivity.objects.filter(user_id__in=[d["id"] for d in details])
            .values_list("user_id", flat=True)
            .distinct()
        )
        seen_ids = active_ids | {d["id"] for d in details if d["last_login"]}
        self.stdout.write(f"  by user_type: {json.dumps(by_type, sort_keys=True)}")
        self.stdout.write(f"  active: {sum(1 for d in details if d['is_active'])}")
        self.stdout.write(f"  with a non-@student.local email: {real_email}")
        self.stdout.write(
            f"  with recorded sign-in activity (UserActivity rows): {len(active_ids)}"
        )
        self.stdout.write(
            f"  with last_login set (not maintained by this app): "
            f"{sum(1 for d in details if d['last_login'])}"
        )
        if seen_ids:
            self.stdout.write(
                self.style.WARNING(
                    f"WARNING: {len(seen_ids)} matching account(s) show sign-in "
                    "activity; resetting them locks out a possibly real user "
                    "(they must use password reset, which needs a real mailbox)."
                )
            )

        if not execute:
            self.stdout.write(
                self.style.WARNING(
                    f"Dry run: would reset {len(matches)} account(s). "
                    "Re-run with --execute to apply."
                )
            )
            return

        report_path = options.get("report") or (
            "h3_student123_remediation_" + now.strftime("%Y%m%dT%H%M%SZ") + ".jsonl"
        )
        detail_by_id = {d["id"]: d for d in details}
        unusable = make_password(None)
        reset = skipped_changed = 0

        with open(report_path, "a", encoding="utf-8") as report:
            for pk, old_hash in matches:
                with transaction.atomic():
                    # Compare-and-set on the verified hash: only writes if the
                    # row still holds exactly what we checked.
                    updated = User.objects.filter(pk=pk, password=old_hash).update(
                        password=unusable
                    )
                if not updated:
                    skipped_changed += 1
                    logger.warning(
                        "h3_student123 skipped: password changed since scan",
                        extra={"user_id": str(pk)},
                    )
                    continue
                reset += 1
                d = detail_by_id[pk]
                record = {
                    "event": "h3.student123.password_reset",
                    "at": datetime.now(timezone.utc).isoformat(),
                    "user_id": str(pk),
                    "email": d["email"],
                    "user_type": d["user_type"],
                    "is_active": d["is_active"],
                    "has_recorded_activity": pk in active_ids,
                    "last_login": (
                        d["last_login"].isoformat() if d["last_login"] else None
                    ),
                    "previous_hash_algorithm": _algorithm(old_hash),
                    "new_state": "unusable_password",
                }
                # Logged BEFORE the file write: if the disk fills after the
                # row committed, the log line still records the reset.
                logger.info("h3_student123 reset", extra={"audit": record})
                report.write(json.dumps(record, sort_keys=True) + "\n")
                report.flush()
                os.fsync(report.fileno())

        self.stdout.write(f"Reset: {reset}")
        self.stdout.write(f"Skipped (password changed since scan): {skipped_changed}")
        self.stdout.write(f"Audit report: {os.path.abspath(report_path)}")
