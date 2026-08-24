"""STUDENT_FRONTEND_DOMAIN setting: exists, defaults to FRONTEND_DOMAIN when
unset, and is used as-is when explicitly set.

Settings are resolved once at process start, so exercising both branches
means booting a fresh process per case rather than mutating
`django.conf.settings` in-process. Each subprocess execs
AutoGrader/settings.py directly (by file path, via importlib) rather than
going through `django.setup()` - the full app registry now pulls in
ai_processor.services, which reads a prompt file via a path relative to
cwd at import time (unrelated to this setting), which would make the
outcome depend on picking a cwd that keeps *that* import happy. Since the
only thing this test cares about is two lines in settings.py, and the only
cwd-relative thing settings.py itself does is `env.read_env(".env")`,
loading just the settings module sidesteps that landmine entirely.

Each subprocess gets a fully-controlled env (explicit values for the vars
under test, inherited from the real os.environ for everything else
settings.py needs) and runs with cwd in an empty temp dir, so
`env.read_env(".env")` finds no file to read - this must work whether the
parent process's environment came from a real .env file (local dev) or
from injected env vars with no .env file at all (CI), and must not assume
any particular values for either domain (CI's placeholder domains, e.g.,
may not differ from each other the way this repo's local .env deliberately
makes them differ).
"""

import os
import subprocess
import sys
import tempfile

from django.conf import settings
from django.test import SimpleTestCase


class StudentFrontendDomainSettingTests(SimpleTestCase):
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
            "print(mod.STUDENT_FRONTEND_DOMAIN)"
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
        frontend_domain, student_domain = result.stdout.strip().splitlines()
        return frontend_domain, student_domain

    def test_falls_back_to_frontend_domain_when_unset(self):
        frontend_domain, student_domain = self._resolved_domains(
            {"FRONTEND_DOMAIN": "teacher.example.test"},
            unset=("STUDENT_FRONTEND_DOMAIN",),
        )

        self.assertEqual(frontend_domain, "teacher.example.test")
        self.assertEqual(student_domain, "teacher.example.test")

    def test_explicit_value_is_used_instead_of_falling_back(self):
        frontend_domain, student_domain = self._resolved_domains(
            {
                "FRONTEND_DOMAIN": "teacher.example.test",
                "STUDENT_FRONTEND_DOMAIN": "student.example.test",
            }
        )

        self.assertEqual(frontend_domain, "teacher.example.test")
        self.assertEqual(student_domain, "student.example.test")
