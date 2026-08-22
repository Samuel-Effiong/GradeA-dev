"""STUDENT_FRONTEND_DOMAIN setting: exists, defaults to FRONTEND_DOMAIN when
unset, and can be overridden independently of it.
"""

import os
import subprocess
import sys
import tempfile

from django.conf import settings
from django.test import SimpleTestCase


class StudentFrontendDomainSettingTests(SimpleTestCase):
    def test_current_env_sets_a_distinct_student_domain(self):
        """This repo's .env deliberately sets STUDENT_FRONTEND_DOMAIN to a
        different host than FRONTEND_DOMAIN - guard against someone
        collapsing them back to one value."""
        self.assertNotEqual(settings.STUDENT_FRONTEND_DOMAIN, settings.FRONTEND_DOMAIN)
        self.assertEqual(settings.FRONTEND_DOMAIN, "www.teacher.gradeautomator.com")
        self.assertEqual(
            settings.STUDENT_FRONTEND_DOMAIN, "www.student.gradeautomator.com"
        )

    def test_falls_back_to_frontend_domain_when_unset(self):
        """With STUDENT_FRONTEND_DOMAIN absent from the environment (and
        from .env), a fresh settings import must fall back to
        FRONTEND_DOMAIN rather than raising - existing deployments have no
        STUDENT_FRONTEND_DOMAIN yet.

        `env.read_env(".env")` in settings.py reads the file relative to
        the process's cwd, so a stripped-down copy of the real .env in a
        throwaway directory - used as the subprocess's cwd - is what
        actually exercises the "unset" path, rather than merely deleting
        the var from this test's own os.environ.
        """
        real_env_path = os.path.join(settings.BASE_DIR, ".env")
        with open(real_env_path) as f:
            lines = f.readlines()
        stripped = [
            line
            for line in lines
            if not line.strip().startswith("STUDENT_FRONTEND_DOMAIN")
        ]
        self.assertLess(
            len(stripped),
            len(lines),
            "fixture .env has no STUDENT_FRONTEND_DOMAIN line to strip",
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            with open(os.path.join(tmp_dir, ".env"), "w") as f:
                f.writelines(stripped)

            env = os.environ.copy()
            env.pop("STUDENT_FRONTEND_DOMAIN", None)
            env["PYTHONPATH"] = str(settings.BASE_DIR)
            # Force this, rather than setdefault - the outer test run may
            # itself be using an isolated settings module (e.g. a scratch
            # settings_iso shim) via this same env var, which would not be
            # importable from the subprocess's cwd/PYTHONPATH here.
            env["DJANGO_SETTINGS_MODULE"] = "AutoGrader.settings"

            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    (
                        "import django; "
                        "django.setup(); "
                        "from django.conf import settings; "
                        "assert settings.STUDENT_FRONTEND_DOMAIN == settings.FRONTEND_DOMAIN, ("
                        "settings.STUDENT_FRONTEND_DOMAIN, settings.FRONTEND_DOMAIN); "
                        "print('OK')"
                    ),
                ],
                env=env,
                capture_output=True,
                text=True,
                cwd=tmp_dir,
            )

        self.assertEqual(
            result.returncode,
            0,
            f"stdout={result.stdout!r} stderr={result.stderr!r}",
        )
        self.assertIn("OK", result.stdout)
