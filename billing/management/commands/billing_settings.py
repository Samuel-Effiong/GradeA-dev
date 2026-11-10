"""Read or change the one BillingSettings row (Epic B1b-4, 08b §1.3 item 5).

BillingSettings stays out of Django admin (SM ruling 2026-11-08). This is
the operator's way to see and change it, on the platform shell:

    manage.py billing_settings --show
    manage.py billing_settings --set cold_start_cost_factor=2.4 --by ops@example.com

One field per --set; several --set are applied together or not at all.
Every value is checked first: the integer fields by their model field's own
range, the factor finite and above 0. Each change writes one STATE_CHANGE
audit row with the field, old and new value, naming the super admin given by
--by (command_actor). Operator output only: no app wording.
"""

import math
from pathlib import Path
from typing import cast

from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.db.models import Field

from audit.context import command_actor
from audit.emitter import emit
from audit.enums import AuditAction
from billing.models import BillingSettings
from users.models import CustomUser, UserTypes

COMMAND = Path(__file__).stem
FIELDS = (
    "cold_start_cost_factor",
    "unstarted_backstop_hours",
    "hold_sweep_margin_minutes",
)


class Command(BaseCommand):
    help = "Show or change the BillingSettings row; every change is audited."

    def add_arguments(self, parser):
        parser.add_argument("--show", action="store_true", help="Print the row.")
        parser.add_argument(
            "--set",
            action="append",
            default=[],
            metavar="FIELD=VALUE",
            help="Change one field (repeat for several).",
        )
        parser.add_argument("--by", help="Email of the super admin making the change.")

    def handle(self, *args, **options):
        if options["set"]:
            self._apply(options["set"], options["by"])
        if options["show"] or not options["set"]:
            row = BillingSettings.load()
            for name in FIELDS:
                self.stdout.write(f"{name}={getattr(row, name)}")

    def _apply(self, pairs, by):
        if not by:
            raise CommandError("--by is required with --set")
        operator = CustomUser.objects.filter(
            email__iexact=by,
            user_type=UserTypes.SUPER_ADMIN,
            is_superuser=True,
            is_active=True,
        ).first()
        if operator is None:
            raise CommandError("--by must name an active super admin")
        changes = [self._parse(pair) for pair in pairs]
        with transaction.atomic(), command_actor(operator, command=COMMAND):
            row = BillingSettings.load()
            for name, value in changes:
                old = getattr(row, name)
                setattr(row, name, value)
                row.save(update_fields=[name])
                emit(
                    AuditAction.STATE_CHANGE,
                    actor=operator,
                    target_type="BillingSettings",
                    # One row with an integer pk; target_id takes UUIDs only.
                    target_id=None,
                    before={name: old},
                    after={name: value},
                )
                self.stdout.write(f"{name}: {old} -> {value}")

    @staticmethod
    def _parse(pair):
        name, sep, raw = pair.partition("=")
        if not sep or name not in FIELDS:
            raise CommandError(f"unknown setting: {name}")
        # Each name in FIELDS is a concrete column (mypy sees a union).
        field = cast(Field, BillingSettings._meta.get_field(name))
        try:
            value = field.to_python(raw)
            field.run_validators(value)
        except ValidationError:
            raise CommandError(f"invalid value for {name}: {raw}") from None
        if name == "cold_start_cost_factor" and not (
            math.isfinite(value) and value > 0
        ):
            raise CommandError(f"invalid value for {name}: {raw}")
        if isinstance(value, int) and value < 0:
            raise CommandError(f"invalid value for {name}: {raw}")
        return name, value
