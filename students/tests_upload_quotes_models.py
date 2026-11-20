"""Upload quote, slice 2 (plan rev 3 sections 6 and 7; BE-B-04/05/06):
the two tables, the settings, the purge, the cascade and the data map.

Nothing here touches the task machinery: a quote is its own pair of tables,
so no processing task, session, hold or ledger row can exist for a quote
nobody confirmed (the consent-by-construction ruling of the design round).
"""

import importlib
from datetime import timedelta
from typing import Any
from unittest.mock import patch

from celery.schedules import crontab
from django.conf import settings as django_settings
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.utils import timezone

from ai_processor.tests_real_chunked_extraction import make_teacher_with_credits
from AutoGrader.beat_locks import declared_lock
from classrooms.models import Course, Session
from students import kept_files as kept_files_module

settings: Any = django_settings
kept_files: Any = kept_files_module


def other_teacher(email):
    from users.models import CustomUser, UserTypes

    return CustomUser.objects.create_user(
        email=email,
        password="password123",  # pragma: allowlist secret
        user_type=UserTypes.TEACHER,
        first_name="Other",
        last_name="Teacher",
        is_active=True,
    )


def models() -> Any:
    return importlib.import_module("students.models")


def quotes() -> Any:
    return importlib.import_module("students.upload_quotes")


class Fixture(TestCase):
    def setUp(self):
        kept_files.MEMORY.clear()
        self.teacher = make_teacher_with_credits("quote-s2@example.com")
        self.session = Session.objects.create(name="Q2", teacher=self.teacher)
        self.course = Course.objects.create(
            name="Quote2", teacher=self.teacher, session=self.session
        )

    def quote(self, **kwargs):
        defaults = {
            "teacher": self.teacher,
            "kind": models().UploadQuote.Kind.ASSIGNMENT_FILES,
            "course": self.course,
            "expires_at": timezone.now() + timedelta(hours=24),
        }
        defaults.update(kwargs)
        return models().UploadQuote.objects.create(**defaults)

    def file(self, quote, position=0, content=b"%PDF-1.4 x", **kwargs):
        stored = kept_files.store(content)
        defaults = {
            "quote": quote,
            "position": position,
            "state": models().UploadQuoteFile.State.MEASURED,
            "storage_key": stored.storage_key,
            "original_name": "paper.pdf",
            "content_type": "application/pdf",
            "size_bytes": stored.size_bytes,
            "sha256": stored.sha256,
        }
        defaults.update(kwargs)
        return models().UploadQuoteFile.objects.create(**defaults)


class Tables(Fixture):
    def test_a_quote_starts_open_with_a_random_id(self):
        quote = self.quote()
        self.assertEqual(quote.state, models().UploadQuote.State.OPEN)
        self.assertEqual(len(str(quote.pk)), 36)
        self.assertIsNone(quote.session)
        self.assertIsNone(quote.confirmed_at)

    def test_the_states_are_the_four_of_the_plan(self):
        self.assertEqual(
            {value for value, _ in models().UploadQuote.State.choices},
            {"OPEN", "CONFIRMED", "CANCELLED", "EXPIRED"},
        )

    def test_both_kinds(self):
        self.assertEqual(
            {value for value, _ in models().UploadQuote.Kind.choices},
            {"answer_papers", "assignment_files"},
        )

    def test_one_idempotency_key_a_teacher(self):
        self.quote(idempotency_key="k1")
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.quote(idempotency_key="k1")

    def test_the_same_key_for_another_teacher_is_fine(self):
        other = other_teacher("quote-s2-other@example.com")
        self.quote(idempotency_key="k1")
        self.quote(teacher=other, idempotency_key="k1")

    def test_quotes_without_a_key_are_not_unique(self):
        self.quote()
        self.quote()

    def test_the_same_bytes_twice_in_a_quote_are_refused_by_the_table(self):
        quote = self.quote()
        self.file(quote, 0)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.file(quote, 1)

    def test_the_same_bytes_in_two_quotes_are_fine(self):
        self.file(self.quote(), 0)
        self.file(self.quote(), 0)

    def test_refused_files_have_no_hash_and_may_repeat(self):
        quote = self.quote()
        for position in (0, 1):
            models().UploadQuoteFile.objects.create(
                quote=quote,
                position=position,
                state=models().UploadQuoteFile.State.REFUSED,
                reason_code="FILE_EMPTY",
                original_name="a.pdf",
                size_bytes=0,
                sha256="",
            )

    def test_one_position_a_quote(self):
        quote = self.quote()
        self.file(quote, 0)
        with self.assertRaises(IntegrityError), transaction.atomic():
            self.file(quote, 0, content=b"other bytes")

    def test_file_states(self):
        self.assertEqual(
            {value for value, _ in models().UploadQuoteFile.State.choices},
            {"STORING", "STORED", "MEASURING", "MEASURED", "REFUSED", "NOT_KEPT"},
        )

    def test_no_column_holds_a_file_name_but_original_name(self):
        names = {f.name for f in models().UploadQuoteFile._meta.get_fields()}
        self.assertIn("original_name", names)
        self.assertFalse({"name", "file_name", "filename"} & names)


class Settings(TestCase):
    def test_defaults(self):
        self.assertTrue(hasattr(settings, "UPLOAD_QUOTES_ENABLED"))
        self.assertEqual(settings.UPLOAD_QUOTE_LIFETIME_MINUTES, 10)
        self.assertEqual(settings.UPLOAD_QUOTE_OPEN_BYTES, 1_000 * 1024 * 1024)
        self.assertEqual(settings.UPLOAD_QUOTE_MAX_OPEN, 3)
        self.assertEqual(settings.UPLOAD_QUOTE_GLOBAL_OPEN_BYTES_MAX, 40 * 1024**3)
        self.assertEqual(settings.UPLOAD_QUOTE_GLOBAL_OPEN_BYTES_ALERT, 20 * 1024**3)
        self.assertEqual(settings.UPLOAD_QUOTE_CONFIRMED_KEEP_DAYS, 7)
        self.assertEqual(settings.UPLOAD_QUOTE_MEASURE_CONCURRENCY, 2)
        self.assertEqual(settings.UPLOAD_QUOTE_MAX_FILES, 100)


class Handover(Fixture):
    """The quote file's delete signal: the asset goes with the row, except
    after a confirm handed its key to a KeptUpload (key nulled)."""

    def test_deleting_a_row_deletes_its_asset_after_commit(self):
        row = self.file(self.quote())
        key = row.storage_key
        with self.captureOnCommitCallbacks(execute=True):
            row.delete()
        self.assertNotIn(key, kept_files.MEMORY)

    def test_a_row_whose_key_was_handed_over_deletes_nothing(self):
        row = self.file(self.quote())
        key = row.storage_key
        models().UploadQuoteFile.objects.filter(pk=row.pk).update(storage_key=None)
        row.refresh_from_db()
        with self.captureOnCommitCallbacks(execute=True):
            row.delete()
        self.assertIn(key, kept_files.MEMORY)

    def test_a_course_erasure_removes_open_quotes_and_their_assets(self):
        row = self.file(self.quote())
        key = row.storage_key
        with self.captureOnCommitCallbacks(execute=True):
            self.course.delete()
        self.assertFalse(models().UploadQuote.objects.exists())
        self.assertNotIn(key, kept_files.MEMORY)

    def test_a_teacher_erasure_removes_their_quotes_and_assets(self):
        row = self.file(self.quote())
        key = row.storage_key
        with self.captureOnCommitCallbacks(execute=True):
            self.teacher.delete()
        self.assertFalse(models().UploadQuote.objects.exists())
        self.assertNotIn(key, kept_files.MEMORY)


class RowFirst(Fixture):
    def test_store_takes_a_key_chosen_before_the_bytes(self):
        key = kept_files.new_key()
        stored = kept_files.store(b"abc", key=key)
        self.assertEqual(stored.storage_key, key)
        self.assertEqual(kept_files.load(key), b"abc")

    def test_a_new_key_is_in_the_deployment_folder_and_random(self):
        first, second = kept_files.new_key(), kept_files.new_key()
        self.assertNotEqual(first, second)
        self.assertTrue(
            first.startswith(f"kept_uploads/{kept_files.deployment_folder()}/")
        )

    def test_the_purge_deletes_a_key_whose_asset_never_arrived(self):
        """A dead request left the row (key chosen) but no bytes."""
        quote = self.quote(expires_at=timezone.now() - timedelta(minutes=1))
        row = self.file(quote)
        kept_files.MEMORY.pop(row.storage_key)
        with self.captureOnCommitCallbacks(execute=True):
            result = quotes().purge_due()
        row.refresh_from_db()
        self.assertIsNotNone(row.purged_at)
        self.assertEqual(result["failed"], 0)


class Purge(Fixture):
    def run_purge(self, **kwargs):
        with self.captureOnCommitCallbacks(execute=True):
            return quotes().purge_due(**kwargs)

    def test_an_open_quote_past_expiry_is_expired_and_its_files_deleted(self):
        quote = self.quote(expires_at=timezone.now() - timedelta(seconds=1))
        row = self.file(quote)
        key = row.storage_key
        self.run_purge()
        quote.refresh_from_db()
        row.refresh_from_db()
        self.assertEqual(quote.state, models().UploadQuote.State.EXPIRED)
        self.assertIsNotNone(quote.closed_at)
        self.assertNotIn(key, kept_files.MEMORY)
        self.assertIsNotNone(row.purged_at)

    def test_an_open_quote_before_expiry_is_left_alone(self):
        quote = self.quote()
        row = self.file(quote)
        self.run_purge()
        quote.refresh_from_db()
        self.assertEqual(quote.state, models().UploadQuote.State.OPEN)
        self.assertIn(row.storage_key, kept_files.MEMORY)

    def test_a_cancelled_quote_s_files_are_deleted(self):
        quote = self.quote(state=models().UploadQuote.State.CANCELLED)
        row = self.file(quote)
        self.run_purge()
        row.refresh_from_db()
        self.assertIsNotNone(row.purged_at)
        self.assertNotIn(row.storage_key, kept_files.MEMORY)

    def test_a_confirmed_quote_keeps_what_the_handover_gave_away(self):
        """Confirmed rows have a null key: the purge finds nothing to delete
        and never touches the KeptUpload's asset."""
        quote = self.quote(state=models().UploadQuote.State.CONFIRMED)
        key = kept_files.store(b"handed over").storage_key
        models().UploadQuoteFile.objects.create(
            quote=quote,
            position=0,
            state=models().UploadQuoteFile.State.MEASURED,
            storage_key=None,
            original_name="a.pdf",
            size_bytes=3,
            sha256="a" * 64,
        )
        self.run_purge()
        self.assertIn(key, kept_files.MEMORY)

    def test_ended_quote_rows_go_after_the_keep_days(self):
        old = timezone.now() - timedelta(
            days=settings.UPLOAD_QUOTE_CONFIRMED_KEEP_DAYS + 1
        )
        confirmed = self.quote(
            state=models().UploadQuote.State.CONFIRMED, confirmed_at=old
        )
        recent = self.quote(
            state=models().UploadQuote.State.CONFIRMED,
            confirmed_at=timezone.now() - timedelta(days=1),
        )
        expired = self.quote(state=models().UploadQuote.State.EXPIRED, closed_at=old)
        self.run_purge()
        left = set(models().UploadQuote.objects.values_list("pk", flat=True))
        self.assertEqual(left, {recent.pk})
        self.assertNotIn(confirmed.pk, left)
        self.assertNotIn(expired.pk, left)

    def test_a_failed_delete_is_left_for_the_next_purge(self):
        quote = self.quote(expires_at=timezone.now() - timedelta(minutes=1))
        row = self.file(quote)
        with patch.object(
            kept_files, "delete", side_effect=kept_files.KeptFileUnavailable("x")
        ):
            result = self.run_purge()
        row.refresh_from_db()
        self.assertIsNone(row.purged_at)
        self.assertEqual(result["failed"], 1)
        self.run_purge()
        row.refresh_from_db()
        self.assertIsNotNone(row.purged_at)

    def test_expiry_counts_a_metric_and_is_not_a_failure_rate(self):
        quote = self.quote(expires_at=timezone.now() - timedelta(minutes=1))
        self.file(quote)
        with patch("students.upload_quotes.audit_metrics.count") as count:
            self.run_purge()
        names = [call.args[0] for call in count.call_args_list]
        self.assertIn("upload_quote_expired", names)

    def test_the_task_runs_one_at_a_time_and_is_scheduled_every_five_minutes(self):
        task = importlib.import_module("students.tasks").purge_upload_quotes
        self.assertIsNotNone(declared_lock(task))
        entry = settings.CELERY_BEAT_SCHEDULE["purge-upload-quotes"]
        self.assertEqual(entry["task"], "students.tasks.purge_upload_quotes")
        self.assertEqual(entry["schedule"], crontab(minute="*/5"))

    def test_the_task_purges(self):
        quote = self.quote(expires_at=timezone.now() - timedelta(minutes=1))
        self.file(quote)
        task = importlib.import_module("students.tasks").purge_upload_quotes
        with self.captureOnCommitCallbacks(execute=True):
            task.run()
        quote.refresh_from_db()
        self.assertEqual(quote.state, models().UploadQuote.State.EXPIRED)


class OpenBytes(Fixture):
    def test_open_bytes_of_a_teacher_counts_open_quotes_only(self):
        open_quote = self.quote()
        self.file(open_quote, 0, content=b"x" * 10)
        self.file(open_quote, 1, content=b"y" * 5)
        done = self.quote(state=models().UploadQuote.State.CANCELLED)
        self.file(done, 0, content=b"z" * 100)
        self.assertEqual(quotes().open_bytes(self.teacher), 15)

    def test_global_open_bytes(self):
        other = other_teacher("quote-s2-g@example.com")
        self.file(self.quote(), 0, content=b"x" * 10)
        self.file(self.quote(teacher=other), 0, content=b"y" * 7)
        self.assertEqual(quotes().open_bytes(), 17)

    def test_open_quote_count_of_a_teacher(self):
        self.quote()
        self.quote()
        self.quote(state=models().UploadQuote.State.EXPIRED)
        self.assertEqual(quotes().open_count(self.teacher), 2)


class DataMap(TestCase):
    def test_both_tables_are_listed_and_a_student_erasure_leaves_them(self):
        from students.student_data_map import STUDENT_DATA_MAP, Role

        entry = STUDENT_DATA_MAP[("students.UploadQuote", "teacher")]
        # A teacher-uploaded answer paper is not linked to a student until
        # extraction, so a STUDENT's export or erasure cannot find it: the
        # column is a teacher's (NOT_A_STUDENT), removed with the teacher or
        # the course (cascade) and by the 24-hour expiry.
        self.assertEqual(entry.role, Role.NOT_A_STUDENT)
