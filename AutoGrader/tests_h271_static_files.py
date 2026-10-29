"""H-271: static files are collected and served in the deployed image.

Design: docs/evidence/h271-static-files/DESIGN.md. On staging the admin
login was a 500: production storage is ManifestStaticFilesStorage, the
Dockerfile never ran collectstatic, so {% static %} found no manifest. And
with a manifest, nothing served /static/ (DEBUG is off, no middleware).

Red as written against the call-site revert (Dockerfile and settings.py at
the base): the Dockerfile, collectstatic-command, production-settings and
served-CSS tests fail (the last in setUpClass, on cloudinary_storage's
collectstatic);
the guard (no collectstatic -> the manifest error) passes either way.

The production settings are read in a fresh process, as in
tests_school_admin_frontend_domain.py: settings resolve once at start, so
ENVIRONMENT=prod cannot be switched on in this process.
"""

import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import tempfile

from django.conf import settings
from django.core.management import call_command, get_commands
from django.test import SimpleTestCase, TestCase, override_settings

REPO = pathlib.Path(__file__).resolve().parent.parent

PROD_STATIC_BACKEND = "whitenoise.storage.CompressedManifestStaticFilesStorage"
WHITENOISE = "whitenoise.middleware.WhiteNoiseMiddleware"
SECURITY = "django.middleware.security.SecurityMiddleware"

# Every value ENVIRONMENT=prod reads at import, all fake: the process only
# imports settings.py and prints three values.
PROD_ENV = {
    "ENVIRONMENT": "prod",
    "SECRET_KEY": "h271-not-a-real-key",  # pragma: allowlist secret
    "FRONTEND_DOMAIN": "https://app.example.test",
    "DATABASE_URI": "postgres://u:p@localhost:5432/h271",  # pragma: allowlist secret
    "REDIS_PROD_URL": "redis://localhost:6379/0",
    "STRIPE_PUBLIC_KEY": "pk_test_h271",
    "STRIPE_SECRET_KEY": "sk_test_h271",  # pragma: allowlist secret
    "STRIPE_WEBHOOK_SECRET": "whsec_h271",  # pragma: allowlist secret
    "SENTRY_DSN": "",
}


class DockerfileCollectsStaticTests(SimpleTestCase):
    def test_the_container_collects_static_files_before_gunicorn_starts(self):
        text = (REPO / "Dockerfile").read_text()
        cmds = [line for line in text.splitlines() if line.startswith("CMD ")]
        self.assertEqual(len(cmds), 1, cmds)
        cmd = cmds[0]
        collect = cmd.find("python manage.py collectstatic --noinput")
        self.assertNotEqual(collect, -1, cmd)
        self.assertLess(collect, cmd.find("gunicorn "), cmd)
        self.assertTrue(cmd.startswith("CMD set -xe;"), cmd)


class CollectstaticIsDjangosOwnTests(SimpleTestCase):
    def test_collectstatic_is_the_staticfiles_command(self):
        # cloudinary_storage's collectstatic reads the STATICFILES_STORAGE
        # setting Django 5.1 removed, and crashes before copying anything.
        self.assertEqual(get_commands()["collectstatic"], "django.contrib.staticfiles")


class ProductionStaticSettingsTests(SimpleTestCase):
    prod: dict

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        env = os.environ.copy()
        env.update(PROD_ENV)
        settings_path = str(settings.BASE_DIR / "AutoGrader" / "settings.py")
        script = (
            "import importlib.util, json; "
            f"spec = importlib.util.spec_from_file_location('isolated_settings', {settings_path!r}); "
            "mod = importlib.util.module_from_spec(spec); "
            "spec.loader.exec_module(mod); "
            "print(json.dumps({'storages': mod.STORAGES, 'middleware': mod.MIDDLEWARE, "
            "'static_root': str(mod.STATIC_ROOT)}))"
        )
        with tempfile.TemporaryDirectory() as tmp_dir:
            result = subprocess.run(
                [sys.executable, "-c", script],
                env=env,
                capture_output=True,
                text=True,
                cwd=tmp_dir,
            )
        if result.returncode != 0:
            raise AssertionError(f"stdout={result.stdout!r} stderr={result.stderr!r}")
        cls.prod = json.loads(result.stdout.strip().splitlines()[-1])

    def test_production_static_storage_is_whitenoise_manifest(self):
        self.assertEqual(
            self.prod["storages"]["staticfiles"]["BACKEND"], PROD_STATIC_BACKEND
        )

    def test_whitenoise_comes_directly_after_the_security_middleware(self):
        middleware = self.prod["middleware"]
        self.assertIn(WHITENOISE, middleware)
        self.assertEqual(
            middleware.index(WHITENOISE), middleware.index(SECURITY) + 1, middleware
        )

    def test_collected_files_go_to_their_own_folder(self):
        # Not the tracked static/ folder: WhiteNoise would serve its leftovers.
        self.assertEqual(
            pathlib.Path(self.prod["static_root"]), settings.BASE_DIR / "staticfiles"
        )


def _prod_like(static_root):
    return override_settings(
        DEBUG=False,
        STATIC_ROOT=static_root,
        STORAGES={
            "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
            "staticfiles": {"BACKEND": PROD_STATIC_BACKEND},
        },
    )


class AdminIsServedAfterCollectstaticTests(TestCase):
    static_root: str
    _prod: override_settings

    @classmethod
    def setUpClass(cls):
        cls.static_root = tempfile.mkdtemp(prefix="h271_static_")
        cls._prod = _prod_like(cls.static_root)
        cls._prod.enable()
        try:
            super().setUpClass()
            call_command("collectstatic", "--noinput", verbosity=0)
        except BaseException:
            cls._prod.disable()
            raise

    @classmethod
    def tearDownClass(cls):
        try:
            super().tearDownClass()
        finally:
            cls._prod.disable()
            shutil.rmtree(cls.static_root, ignore_errors=True)

    def test_the_admin_login_page_and_its_stylesheet_are_served(self):
        page = self.client.get("/admin/login/")
        self.assertEqual(page.status_code, 200)
        match = re.search(
            r'href="(/static/admin/css/base\.[0-9a-f]{12}\.css)"', page.content.decode()
        )
        assert match is not None, "no hashed admin/css/base.css link on the page"

        css = self.client.get(match.group(1))
        self.assertEqual(css.status_code, 200, match.group(1))
        self.assertTrue(css["Content-Type"].startswith("text/css"), css["Content-Type"])


class WithoutCollectstaticTests(TestCase):
    """Guard: green before and after. This is the staging 500."""

    def test_the_admin_login_fails_on_the_missing_manifest(self):
        with tempfile.TemporaryDirectory(prefix="h271_empty_") as empty:
            with _prod_like(empty):
                with self.assertRaisesMessage(
                    ValueError, "Missing staticfiles manifest entry"
                ):
                    self.client.get("/admin/login/")
