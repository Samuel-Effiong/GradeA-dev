"""Epic B1b-5 (08b §3.2, NOTES §5): `students.kept_files`, the storage of
kept upload files.

The module calls the Cloudinary SDK directly (B1b-0 spike, NOTES §0.4):
raw, PRIVATE assets under `kept_uploads/<random hex>`, never the file's name,
and never a URL handed to anyone outside the module. Under test the backend
is an in-memory dict (`KEPT_UPLOADS_BACKEND = "memory"`), so no test touches
the network; the Cloudinary backend's tests patch the three SDK calls and
`requests`.

The module is looked up by name, so this file loads before it exists.

    python manage.py test students.tests_kept_files
"""

import hashlib
import importlib
import re
from types import SimpleNamespace
from unittest.mock import patch

import requests
from django.conf import settings
from django.test import SimpleTestCase, override_settings

KEY = re.compile(r"^kept_uploads/[0-9a-f]{32}$")


def kept_files():
    return importlib.import_module("students.kept_files")


class TheBackendUnderTest(SimpleTestCase):
    def test_tests_use_the_memory_backend(self):
        self.assertEqual(getattr(settings, "KEPT_UPLOADS_BACKEND", None), "memory")


class MemoryBackendTests(SimpleTestCase):
    def test_a_stored_file_loads_back_byte_for_byte(self):
        module = kept_files()
        stored = module.store(b"answer bytes")
        self.assertEqual(module.load(stored.storage_key), b"answer bytes")
        self.assertEqual(stored.size_bytes, len(b"answer bytes"))
        self.assertEqual(stored.sha256, hashlib.sha256(b"answer bytes").hexdigest())

    def test_the_key_is_random_and_carries_no_file_name(self):
        module = kept_files()
        first = module.store(b"x").storage_key
        second = module.store(b"x").storage_key
        self.assertRegex(first, KEY)
        self.assertRegex(second, KEY)
        self.assertNotEqual(first, second)

    def test_a_deleted_file_is_missing(self):
        module = kept_files()
        stored = module.store(b"gone soon")
        module.delete(stored.storage_key)
        with self.assertRaises(module.KeptFileMissing):
            module.load(stored.storage_key)

    def test_deleting_a_missing_file_is_not_an_error(self):
        module = kept_files()
        module.delete("kept_uploads/" + "0" * 32)


@override_settings(KEPT_UPLOADS_BACKEND="cloudinary")
class CloudinaryBackendTests(SimpleTestCase):
    def test_store_uploads_a_private_raw_asset_under_the_random_key(self):
        module = kept_files()
        with patch("cloudinary.uploader.upload", return_value={}) as upload:
            stored = module.store(b"pdf bytes")
        upload.assert_called_once()
        kwargs = upload.call_args.kwargs
        self.assertEqual(kwargs["public_id"], stored.storage_key)
        self.assertRegex(kwargs["public_id"], KEY)
        self.assertEqual(kwargs["resource_type"], "raw")
        self.assertEqual(kwargs["type"], "private")
        self.assertIs(kwargs["overwrite"], False)
        self.assertNotIn("use_filename", kwargs)
        self.assertEqual(upload.call_args.args[0].read(), b"pdf bytes")

    def test_a_storage_error_on_store_is_unavailable(self):
        module = kept_files()
        with patch("cloudinary.uploader.upload", side_effect=Exception("down")):
            with self.assertRaises(module.KeptFileUnavailable):
                module.store(b"pdf bytes")

    def test_load_fetches_a_signed_private_download_server_side(self):
        module = kept_files()
        response = SimpleNamespace(status_code=200, content=b"pdf bytes")
        with patch(
            "cloudinary.utils.private_download_url", return_value="https://signed"
        ) as signed, patch("requests.get", return_value=response) as get:
            self.assertEqual(module.load("kept_uploads/" + "a" * 32), b"pdf bytes")
        self.assertEqual(signed.call_args.args[0], "kept_uploads/" + "a" * 32)
        self.assertEqual(signed.call_args.kwargs["resource_type"], "raw")
        self.assertEqual(signed.call_args.kwargs["type"], "private")
        self.assertIn("expires_at", signed.call_args.kwargs)
        self.assertEqual(get.call_args.args[0], "https://signed")
        self.assertIn("timeout", get.call_args.kwargs)

    def test_a_404_on_load_is_missing(self):
        module = kept_files()
        response = SimpleNamespace(status_code=404, content=b"")
        with patch(
            "cloudinary.utils.private_download_url", return_value="https://signed"
        ), patch("requests.get", return_value=response):
            with self.assertRaises(module.KeptFileMissing):
                module.load("kept_uploads/" + "a" * 32)

    def test_a_connection_error_on_load_is_unavailable(self):
        module = kept_files()
        with patch(
            "cloudinary.utils.private_download_url", return_value="https://signed"
        ), patch("requests.get", side_effect=requests.ConnectionError("down")):
            with self.assertRaises(module.KeptFileUnavailable):
                module.load("kept_uploads/" + "a" * 32)

    def test_a_server_error_on_load_is_unavailable(self):
        module = kept_files()
        response = SimpleNamespace(status_code=502, content=b"")
        with patch(
            "cloudinary.utils.private_download_url", return_value="https://signed"
        ), patch("requests.get", return_value=response):
            with self.assertRaises(module.KeptFileUnavailable):
                module.load("kept_uploads/" + "a" * 32)

    def test_delete_destroys_the_private_raw_asset(self):
        module = kept_files()
        with patch(
            "cloudinary.uploader.destroy", return_value={"result": "ok"}
        ) as destroy:
            module.delete("kept_uploads/" + "b" * 32)
        destroy.assert_called_once_with(
            "kept_uploads/" + "b" * 32,
            resource_type="raw",
            type="private",
            invalidate=True,
        )

    def test_delete_of_an_asset_already_gone_is_not_an_error(self):
        module = kept_files()
        with patch("cloudinary.uploader.destroy", return_value={"result": "not found"}):
            module.delete("kept_uploads/" + "b" * 32)

    def test_a_storage_error_on_delete_is_unavailable(self):
        module = kept_files()
        with patch("cloudinary.uploader.destroy", side_effect=Exception("down")):
            with self.assertRaises(module.KeptFileUnavailable):
                module.delete("kept_uploads/" + "b" * 32)


class NoUrlLeavesTheModuleTests(SimpleTestCase):
    def test_no_public_function_returns_or_names_a_url(self):
        module = kept_files()
        public = sorted(
            name
            for name in vars(module)
            if not name.startswith("_") and callable(getattr(module, name))
        )
        self.assertFalse([name for name in public if "url" in name.lower()], public)
