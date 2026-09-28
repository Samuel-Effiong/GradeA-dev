"""SCHOOL_ADMIN_FRONTEND_DOMAIN setting: exists, defaults to FRONTEND_DOMAIN
when unset, and is used as-is when explicitly set.

Mirrors AutoGrader/tests_student_frontend_domain.py's approach exactly, for
exactly the same reason: settings are resolved once at process start, so
exercising both branches means booting a fresh process per case rather than
mutating `django.conf.settings` in-process. See that file's docstring for
the full explanation of why each subprocess execs settings.py directly by
file path instead of going through `django.setup()`.
"""

import os
import subprocess
import sys
import tempfile

from django.conf import settings
from django.test import SimpleTestCase


class SchoolAdminFrontendDomainSettingTests(SimpleTestCase):
    def _resolved_domains(self, overrides, unset=()):
        env = os.environ.copy()
        for key in unset:
            env.pop(key, None)
        env.update(overrides)

        settings_path = str(settings.BASE_DIR / "AutoGrader" / "settings.py")
        script = (
            "import importlib.util, sys; "
            f"spec = importlib.util.spec_from_file_location('isolated_settings', {settings_path!r}); "
            "mod = importlib.util.module_from_spec(spec); "
            "spec.loader.exec_module(mod); "
            "print(mod.FRONTEND_DOMAIN); "
            "print(mod.SCHOOL_ADMIN_FRONTEND_DOMAIN)"
        )

        with tempfile.TemporaryDirectory() as tmp_dir:
            result = subprocess.run(
                [sys.executable, "-c", script],
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
        frontend_domain, school_admin_domain = result.stdout.strip().splitlines()
        return frontend_domain, school_admin_domain

    def test_falls_back_to_frontend_domain_when_unset(self):
        frontend_domain, school_admin_domain = self._resolved_domains(
            {"FRONTEND_DOMAIN": "teacher.example.test"},
            unset=("SCHOOL_ADMIN_FRONTEND_DOMAIN",),
        )

        self.assertEqual(frontend_domain, "teacher.example.test")
        self.assertEqual(school_admin_domain, "teacher.example.test")

    def test_explicit_value_is_used_instead_of_falling_back(self):
        frontend_domain, school_admin_domain = self._resolved_domains(
            {
                "FRONTEND_DOMAIN": "teacher.example.test",
                "SCHOOL_ADMIN_FRONTEND_DOMAIN": "admin.example.test",
            }
        )

        self.assertEqual(frontend_domain, "teacher.example.test")
        self.assertEqual(school_admin_domain, "admin.example.test")
