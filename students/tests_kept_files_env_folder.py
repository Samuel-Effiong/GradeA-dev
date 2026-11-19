"""Kept uploads: one Cloudinary folder per deployment (owner, 2026-11-19).

Every environment shares one Cloudinary cloud, and kept files all lived under
`kept_uploads/<hex>`, so nothing told a local test's files from beta's or
production's. New keys are `kept_uploads/<deployment>/<hex>`.

The deployment is the name Sentry files events under (H-246): the
SENTRY_ENVIRONMENT when it holds a name, else ENVIRONMENT. ENVIRONMENT alone
is not enough: beta runs with ENVIRONMENT=prod so that it behaves like
production, and only SENTRY_ENVIRONMENT=beta tells the two apart.

Existing rows keep their stored key: load and delete use it as is, so an
old-style `kept_uploads/<hex>` still loads, purges and deletes.
"""

import hashlib
import importlib
import re
from datetime import timedelta
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from students.models import BackgroundProcessingTask, KeptUpload
from users.models import CustomUser, UserTypes

HEX = "[0-9a-f]{32}"
OLD_KEY = "kept_uploads/" + "c" * 32


def kept_files():
    return importlib.import_module("students.kept_files")


@override_settings(ENVIRONMENT="local", SENTRY_ENVIRONMENT="")
class NewKeysNameTheDeployment(SimpleTestCase):
    def key(self):
        return kept_files().store(b"bytes").storage_key

    def test_a_local_file_is_under_local(self):
        self.assertRegex(self.key(), re.compile(rf"^kept_uploads/local/{HEX}$"))

    @override_settings(ENVIRONMENT="prod", SENTRY_ENVIRONMENT="beta")
    def test_beta_is_apart_from_production(self):
        self.assertRegex(self.key(), re.compile(rf"^kept_uploads/beta/{HEX}$"))

    @override_settings(ENVIRONMENT="prod", SENTRY_ENVIRONMENT="")
    def test_production_is_under_prod(self):
        self.assertRegex(self.key(), re.compile(rf"^kept_uploads/prod/{HEX}$"))

    @override_settings(ENVIRONMENT="dev", SENTRY_ENVIRONMENT="  ")
    def test_a_blank_sentry_name_falls_back_to_the_environment(self):
        self.assertRegex(self.key(), re.compile(rf"^kept_uploads/dev/{HEX}$"))

    def test_the_folder_is_a_safe_lowercase_slug(self):
        for raw, folder in (
            (" Staging EU ", "staging-eu"),
            ("../../etc", "etc"),
            ("Beta_2", "beta-2"),
            ("///", "unknown"),
        ):
            with self.subTest(raw=raw), override_settings(
                ENVIRONMENT="prod", SENTRY_ENVIRONMENT=raw
            ):
                self.assertRegex(
                    self.key(), re.compile(rf"^kept_uploads/{folder}/{HEX}$")
                )

    @override_settings(KEPT_UPLOADS_BACKEND="cloudinary")
    def test_cloudinary_gets_the_folder_in_the_public_id(self):
        with patch("students.kept_files.cloudinary.uploader.upload") as upload:
            stored = kept_files().store(b"pdf")
        self.assertEqual(upload.call_args.kwargs["public_id"], stored.storage_key)
        self.assertTrue(stored.storage_key.startswith("kept_uploads/local/"))


class OldKeysKeepWorking(TestCase):
    def setUp(self):
        kept_files().MEMORY[OLD_KEY] = b"old bytes"
        self.addCleanup(kept_files().MEMORY.pop, OLD_KEY, None)
        teacher = CustomUser.objects.create_user(
            email="kept-env@example.com",
            password="password123",  # pragma: allowlist secret
            user_type=UserTypes.TEACHER,
        )
        task = BackgroundProcessingTask.objects.create(
            requested_by=teacher, task_type="UPLOAD"
        )
        now = timezone.now()
        self.row = KeptUpload.objects.create(
            processing_task=task,
            storage_key=OLD_KEY,
            original_name="a.pdf",
            content_type="application/pdf",
            size_bytes=len(b"old bytes"),
            sha256=hashlib.sha256(b"old bytes").hexdigest(),
            kept_until=now + timedelta(days=30),
            created_at=now,
        )

    def test_an_old_key_loads(self):
        from students.kept_uploads import open_upload

        uploaded, _ = open_upload({"kept_upload_id": str(self.row.pk)})
        self.assertEqual(uploaded.read(), b"old bytes")

    def test_an_old_key_is_purged_and_deleted(self):
        from students.kept_uploads import purge_due

        result = purge_due(now=timezone.now() + timedelta(days=31))
        self.assertEqual(result["purged"], 1)
        self.assertNotIn(OLD_KEY, kept_files().MEMORY)
        self.row.refresh_from_db()
        self.assertIsNotNone(self.row.purged_at)

    @override_settings(KEPT_UPLOADS_BACKEND="cloudinary")
    def test_cloudinary_is_asked_for_the_old_key_as_stored(self):
        with patch("students.kept_files.cloudinary.uploader.destroy") as destroy:
            kept_files().delete(OLD_KEY)
        self.assertEqual(destroy.call_args.args[0], OLD_KEY)
