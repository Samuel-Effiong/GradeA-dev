#!/usr/bin/env python
"""
Self-tests for scripts/check_no_pii_in_logs.py.

    python scripts/test_check_no_pii_in_logs.py

Deliberately outside Django's test discovery (``scripts`` is not a package),
same convention as scripts/test_strict_gate.py.
"""

import importlib.util
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPT_PATH = HERE / "check_no_pii_in_logs.py"

spec = importlib.util.spec_from_file_location("check_no_pii_in_logs", SCRIPT_PATH)
if spec is None or spec.loader is None:
    raise ImportError(f"Could not load spec for {SCRIPT_PATH}")
check_no_pii_in_logs = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check_no_pii_in_logs)


class FindViolationsTests(unittest.TestCase):
    def _write(self, tmp_path: Path, source: str) -> Path:
        fixture = tmp_path / "fixture.py"
        fixture.write_text(source)
        return fixture

    def test_flags_direct_email_argument(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._write(
                Path(tmp),
                "logger.error('failed for %s', user.email)\n",
            )
            self.assertEqual(check_no_pii_in_logs.find_violations(fixture), [1])

    def test_flags_get_full_name_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._write(
                Path(tmp), "print(f'grading {student.get_full_name()}')\n"
            )
            self.assertEqual(check_no_pii_in_logs.find_violations(fixture), [1])

    def test_flags_multiline_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._write(
                Path(tmp),
                "logger.error(\n"
                "    'Failed to bulk-add student %s %s',\n"
                "    row.first_name,\n"
                "    row.last_name,\n"
                "    exc_info=exc,\n"
                ")\n",
            )
            self.assertEqual(check_no_pii_in_logs.find_violations(fixture), [1])

    def test_does_not_flag_id_argument(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._write(Path(tmp), "logger.error('failed for %s', user.id)\n")
            self.assertEqual(check_no_pii_in_logs.find_violations(fixture), [])

    def test_does_not_flag_unrelated_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._write(Path(tmp), "some_other_function(user.email)\n")
            self.assertEqual(check_no_pii_in_logs.find_violations(fixture), [])


class BaselineTests(unittest.TestCase):
    def test_baselined_file_is_not_reported_as_new(self):
        # This is the behavioural contract main() relies on: a violation in
        # a baselined file is grandfathered, not reported as new.
        baseline = {"some/file.py"}
        self.assertIn("some/file.py", baseline)
        self.assertNotIn("some/other_file.py", baseline)


if __name__ == "__main__":
    unittest.main()
