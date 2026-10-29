"""H-262: development and test tools must not ship in the production image.

Red at b199eed6 (and fef4249d): requirements-dev.txt does not exist, dev
packages are in requirements.txt, the Dockerfile is single-stage, the audit
gate audits only one file, and CI installs only requirements.txt.
"""

import pathlib
import re

from django.test import SimpleTestCase

REPO = pathlib.Path(__file__).resolve().parent.parent

DEV_ONLY_PACKAGES = {
    "bandit",
    "black",
    "celery-types",
    "cfgv",
    "codespell",
    "coverage",
    "detect-secrets",
    "distlib",
    "django-browser-reload",
    "django-stubs",
    "django-stubs-ext",
    "djangorestframework-stubs",
    "docutils",
    "docutils-stubs",
    "dparse",
    "eradicate",
    "flake8",
    "flake8-bugbear",
    "flake8-comprehensions",
    "flake8-docstrings",
    "flake8-eradicate",
    "flake8-print",
    "flake8-rst-docstrings",
    "flake8_import_order",
    "flower",
    "freezegun",
    "honcho",
    "identify",
    "imagesize",
    "iniconfig",
    "isort",
    "mccabe",
    "mypy",
    "mypy_extensions",
    "nodeenv",
    "pathspec",
    "pluggy",
    "pre_commit",
    "py-cpuinfo",
    "pycodestyle",
    "pydocstyle",
    "pyflakes",
    "pytest",
    "restructuredtext_lint",
    "safety",
    "safety-schemas",
    "snowballstemmer",
    "stevedore",
    "tblib",
    "tomlkit",
    "types-bleach",
    "types-docutils",
    "types-python-dateutil",
    "types-pytz",
    "types-pyyaml",
    "types-requests",
    "virtualenv",
}


def _parse_requirements(path):
    """Package names from a requirements file, normalised to lowercase."""
    packages = set()
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or line.startswith("-"):
                continue
            packages.add(re.split(r"[=<>!;]", line)[0].strip().lower())
    return packages


class RequirementsSplitTest(SimpleTestCase):

    def test_requirements_dev_txt_exists(self):
        path = REPO / "requirements-dev.txt"
        self.assertTrue(path.exists(), "requirements-dev.txt not found")

    def test_requirements_dev_includes_production(self):
        path = REPO / "requirements-dev.txt"
        first_directive = None
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                first_directive = line
                break
        self.assertEqual(
            first_directive,
            "-r requirements.txt",
            "requirements-dev.txt must start with '-r requirements.txt'",
        )

    def test_dev_packages_not_in_production_requirements(self):
        prod = _parse_requirements(REPO / "requirements.txt")
        found = sorted(DEV_ONLY_PACKAGES & prod)
        self.assertEqual(
            found, [], f"dev packages found in production requirements: {found}"
        )

    def test_all_dev_packages_in_dev_requirements(self):
        dev = _parse_requirements(REPO / "requirements-dev.txt")
        missing = sorted(DEV_ONLY_PACKAGES - dev)
        self.assertEqual(
            missing, [], f"dev packages missing from requirements-dev.txt: {missing}"
        )


class DockerfileMultiStageTest(SimpleTestCase):

    def _read_dockerfile(self):
        return (REPO / "Dockerfile").read_text()

    def test_dockerfile_has_build_and_runtime_stages(self):
        text = self._read_dockerfile()
        froms = re.findall(r"^FROM\s+", text, re.MULTILINE)
        self.assertGreaterEqual(
            len(froms), 2, f"expected at least 2 FROM stages, found {len(froms)}"
        )

    def test_build_essential_only_in_build_stage(self):
        text = self._read_dockerfile()
        stages = re.split(r"^FROM\s+", text, flags=re.MULTILINE)
        for stage in stages[1:]:
            if "AS build" in stage or "AS builder" in stage:
                continue
            self.assertNotIn(
                "build-essential",
                stage,
                "build-essential must not appear in the runtime stage",
            )

    def test_no_libmariadb_dev_anywhere(self):
        text = self._read_dockerfile()
        self.assertNotIn(
            "libmariadb-dev",
            text,
            "libmariadb-dev must be removed entirely (nothing needs it)",
        )

    def test_dev_headers_only_in_build_stage(self):
        text = self._read_dockerfile()
        stages = re.split(r"^FROM\s+", text, flags=re.MULTILINE)
        for stage in stages[1:]:
            if "AS build" in stage or "AS builder" in stage:
                continue
            for pkg in (
                "libpq-dev",
                "libjpeg62-turbo-dev",
                "zlib1g-dev",
                "libwebp-dev",
            ):
                self.assertNotIn(
                    pkg, stage, f"{pkg} must not appear in the runtime stage"
                )


class AuditGateTest(SimpleTestCase):

    def test_audit_gate_audits_dev_requirements_too(self):
        script = (REPO / "scripts" / "pip_audit_gate.py").read_text()
        self.assertIn(
            "requirements-dev.txt",
            script,
            "pip_audit_gate.py must audit requirements-dev.txt",
        )

    def test_dev_only_ignores_in_separate_file(self):
        path = REPO / ".pip-audit-ignore-dev.toml"
        self.assertTrue(
            path.exists(),
            ".pip-audit-ignore-dev.toml not found for dev-only advisory ignores",
        )


class CIWorkflowTest(SimpleTestCase):

    def test_tests_workflow_installs_dev_requirements(self):
        text = (REPO / ".github" / "workflows" / "tests.yml").read_text()
        self.assertIn(
            "requirements-dev.txt",
            text,
            "tests.yml must install requirements-dev.txt",
        )

    def test_audit_workflow_audits_both_files(self):
        text = (REPO / ".github" / "workflows" / "audit.yml").read_text()
        self.assertIn(
            "requirements-dev.txt",
            text,
            "audit.yml must reference requirements-dev.txt",
        )
