from django.core.management.base import BaseCommand
from django.db import IntegrityError

from users.models import BetaWhitelist


class Command(BaseCommand):
    help = "Add emails to BetaWhitelist from a file or as arguments"

    def add_arguments(self, parser):
        parser.add_argument(
            "emails",
            nargs="*",
            help="Emails to add (space-separated). If omitted, provide --file.",
        )
        parser.add_argument(
            "--file",
            dest="file",
            help="Path to a file with one email per line to add to the whitelist",
        )

    def handle(self, *args, **options):
        emails = []

        if options.get("file"):
            path = options.get("file")
            try:
                with open(path, "r") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            emails.append(line)
            except FileNotFoundError:
                self.stderr.write(self.style.ERROR(f"File not found: {path}"))
                return

        # emails passed as args
        if options.get("emails"):
            emails.extend(options.get("emails"))

        if not emails:
            self.stdout.write(self.style.WARNING("No emails provided. Nothing to do."))
            return

        added = 0
        skipped = 0
        for email in emails:
            email = email.strip()
            if not email:
                continue
            try:
                obj, created = BetaWhitelist.objects.get_or_create(
                    email=email.lower(), defaults={"is_active": True}
                )
                if created:
                    added += 1
                    self.stdout.write(self.style.SUCCESS(f"Added: {email}"))
                else:
                    skipped += 1
                    self.stdout.write(self.style.NOTICE(f"Already exists: {email}"))
            except IntegrityError:
                skipped += 1
                self.stdout.write(
                    self.style.NOTICE(f"Skipped (integrity error): {email}")
                )

        self.stdout.write(
            self.style.SUCCESS(f"Completed. Added: {added}  Skipped: {skipped}")
        )
