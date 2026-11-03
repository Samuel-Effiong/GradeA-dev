"""H-285: djoser and the social-auth packages it pulled in are removed.

djoser was in INSTALLED_APPS but its URLs were never wired; authentication is
served by users.views.AuthViewSet. Its only effect was installing
social-auth-core, which carried advisories. Red at 153cae74 (H-284 WIP).
"""

import pathlib
import re

import tomllib
from django.conf import settings
from django.test import SimpleTestCase

REPO = pathlib.Path(__file__).resolve().parent.parent

REMOVED = {
    "djoser",
    "social-auth-app-django",
    "social-auth-core",
    "python3-openid",
    "requests-oauthlib",
    "oauthlib",
}


def _pinned(path):
    names = set()
    for line in (REPO / path).read_text().splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "-")):
            continue
        names.add(re.split(r"[=<>!;\[ ]", line)[0].strip().lower())
    return names


class DjoserRemovedTest(SimpleTestCase):

    def test_removed_packages_not_pinned(self):
        found = sorted(
            REMOVED & (_pinned("requirements.txt") | _pinned("requirements-dev.txt"))
        )
        self.assertEqual(found, [], f"still pinned: {found}")

    def test_djoser_not_in_installed_apps(self):
        self.assertNotIn("djoser", settings.INSTALLED_APPS)

    def test_no_djoser_settings_block(self):
        self.assertFalse(hasattr(settings, "DJOSER"), "settings.DJOSER still defined")

    def test_no_ignore_entries_for_removed_packages(self):
        # An ignore entry for a package we no longer install would silently
        # cover the advisory if the package ever came back.
        for name in (".pip-audit-ignore.toml", ".pip-audit-ignore-dev.toml"):
            data = tomllib.loads((REPO / name).read_text())
            stale = sorted(
                {e["package"].lower() for e in data.get("ignore", [])} & REMOVED
            )
            self.assertEqual(stale, [], f"{name} still ignores {stale}")
