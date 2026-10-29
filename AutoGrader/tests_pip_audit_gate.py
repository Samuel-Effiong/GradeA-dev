"""
H-258: the dependency audit in CI (`.github/workflows/audit.yml`, which runs
`scripts/pip_audit_gate.py`).

The runner audits requirements.txt with a pinned pip-audit, strict, and
passes it the advisories the project has accepted for now. Each accepted
advisory sits in `.pip-audit-ignore.toml` with its package, a reason and a
review date; the runner refuses an entry without them, a duplicate, and an
entry whose review date has passed, so an exception cannot be left in place
silently.

Run with:
    python manage.py test AutoGrader.tests_pip_audit_gate
"""

import datetime
import importlib.util
import pathlib
import tempfile

import yaml
from django.conf import settings
from django.test import SimpleTestCase

ROOT = pathlib.Path(settings.BASE_DIR)
SCRIPT = ROOT / "scripts" / "pip_audit_gate.py"
WORKFLOW = ROOT / ".github" / "workflows" / "audit.yml"
IGNORE_FILE = ROOT / ".pip-audit-ignore.toml"
TODAY = datetime.date(2026, 10, 27)


def load_runner():
    spec = importlib.util.spec_from_file_location("pip_audit_gate", SCRIPT)
    assert spec is not None and spec.loader is not None, SCRIPT
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ENTRY = """
[[ignore]]
package = "gunicorn"
ids = ["PYSEC-2026-1434", "PYSEC-2026-1433"]
reason = "The upgrade to 22 is a production change the owner rules on."
review_by = 2026-11-30
"""


class IgnoreFileTests(SimpleTestCase):
    def setUp(self):
        self.runner = load_runner()

    def read(self, text, today=TODAY):
        with tempfile.NamedTemporaryFile("w", suffix=".toml", delete=False) as f:
            f.write(text)
        self.addCleanup(pathlib.Path(f.name).unlink)
        return self.runner.accepted_ids(pathlib.Path(f.name), today)

    def refused(self, text, today=TODAY):
        with self.assertRaises(self.runner.IgnoreFileError) as caught:
            self.read(text, today)
        return str(caught.exception)

    def test_an_entry_gives_its_ids(self):
        self.assertEqual(self.read(ENTRY), ["PYSEC-2026-1433", "PYSEC-2026-1434"])

    def test_an_empty_file_accepts_nothing(self):
        self.assertEqual(self.read(""), [])

    def test_an_entry_on_its_review_date_still_counts(self):
        self.assertEqual(len(self.read(ENTRY, datetime.date(2026, 11, 30))), 2)

    def test_an_entry_past_its_review_date_is_refused(self):
        message = self.refused(ENTRY, datetime.date(2026, 12, 1))
        self.assertIn("gunicorn", message)
        self.assertIn("review date 2026-11-30 has passed", message)

    def test_an_entry_without_a_reason_is_refused(self):
        for text in (
            ENTRY.replace(
                'reason = "The upgrade to 22 is a production change the owner rules on."\n',
                "",
            ),
            ENTRY.replace(
                '"The upgrade to 22 is a production change the owner rules on."',
                '"  "',
            ),
        ):
            with self.subTest(text=text):
                self.assertIn("gunicorn: no reason", self.refused(text))

    def test_an_entry_without_a_review_date_is_refused(self):
        for text in (
            ENTRY.replace("review_by = 2026-11-30\n", ""),
            ENTRY.replace("review_by = 2026-11-30", 'review_by = "2026-11-30"'),
        ):
            with self.subTest(text=text):
                self.assertIn("gunicorn: review_by must be a date", self.refused(text))

    def test_an_entry_without_ids_or_package_is_refused(self):
        self.assertIn(
            "gunicorn: no advisory ids",
            self.refused(ENTRY.replace('["PYSEC-2026-1434", "PYSEC-2026-1433"]', "[]")),
        )
        self.assertIn(
            "an entry has no package",
            self.refused(ENTRY.replace('package = "gunicorn"\n', "")),
        )

    def test_an_id_listed_twice_is_refused(self):
        twice = ENTRY + ENTRY.replace('"gunicorn"', '"other"').replace(
            '"PYSEC-2026-1434", ', ""
        )
        self.assertIn("PYSEC-2026-1433 is listed twice", self.refused(twice))


class CommandTests(SimpleTestCase):
    def setUp(self):
        self.runner = load_runner()

    def test_the_command_audits_the_pinned_requirements_strictly(self):
        for req_file in ("requirements.txt", "requirements-dev.txt"):
            with self.subTest(requirements=req_file):
                command = self.runner.audit_command(req_file, ["B-2", "A-1"])
                self.assertEqual(
                    command[:3], [self.runner.sys.executable, "-m", "pip_audit"]
                )
                for flag in ("--strict", "--no-deps", "--disable-pip"):
                    self.assertIn(flag, command)
                self.assertEqual(command[command.index("-r") + 1], req_file)
                self.assertEqual(
                    [
                        command[i + 1]
                        for i, a in enumerate(command)
                        if a == "--ignore-vuln"
                    ],
                    ["B-2", "A-1"],
                )

    def test_a_wrong_pip_audit_version_is_refused(self):
        with self.assertRaises(self.runner.IgnoreFileError) as caught:
            self.runner.check_version("2.8.0")
        self.assertIn("pip-audit 2.8.0 is not the pinned 2.9.0", str(caught.exception))
        self.runner.check_version(self.runner.PIP_AUDIT_VERSION)


class CheckedInFilesTests(SimpleTestCase):
    def test_the_checked_in_ignore_file_is_well_formed(self):
        # A day before any review date can fall: only the form is checked here;
        # the CI job itself fails once a date passes.
        load_runner().accepted_ids(IGNORE_FILE, datetime.date(2000, 1, 1))

    def test_the_workflow_runs_the_runner_with_the_pinned_version(self):
        workflow = yaml.safe_load(WORKFLOW.read_text())
        # PyYAML reads the bare key `on` as True.
        triggers = workflow[True]
        for event in ("push", "pull_request"):
            self.assertEqual(triggers[event]["branches"], ["main", "beta", "dev"])
        self.assertTrue(triggers["schedule"][0]["cron"])
        steps = [s.get("run", "") for s in workflow["jobs"]["pip-audit"]["steps"]]
        version = load_runner().PIP_AUDIT_VERSION
        self.assertIn(f'pip install "pip-audit=={version}"', "\n".join(steps))
        self.assertIn("python scripts/pip_audit_gate.py", "\n".join(steps))
