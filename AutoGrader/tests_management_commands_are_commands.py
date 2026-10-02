"""H1: every module in a `management/commands/` directory is a command.

Django imports `<app>.management.commands.<name>` to run `manage.py <name>`.
A module there with no `Command` class is therefore a script that runs on
import: `billing/management/commands/backfill.py` was one, and it writes
live Stripe subscription schedules. It now lives in `scripts/`, which is
not a package.

The modules are parsed, never imported: importing one is the hazard.
"""

import ast
from pathlib import Path

from django.apps import apps
from django.conf import settings
from django.core.management import get_commands
from django.test import SimpleTestCase

BASE_DIR = Path(settings.BASE_DIR)
MOVED_SCRIPT = BASE_DIR / "scripts" / "one_off_backfill_stripe_schedules.py"


def project_command_modules():
    """Every non-private module in this project's management/commands dirs."""
    modules = []
    for config in apps.get_app_configs():
        app_dir = Path(config.path)
        if BASE_DIR not in app_dir.parents:
            continue
        commands_dir = app_dir / "management" / "commands"
        modules += [
            path
            for path in sorted(commands_dir.glob("*.py"))
            if not path.name.startswith("_")
        ]
    return modules


def defines_command_class(path):
    tree = ast.parse(path.read_text())
    return any(
        isinstance(node, ast.ClassDef) and node.name == "Command" for node in tree.body
    )


class ManagementCommandsAreCommandsTests(SimpleTestCase):
    def test_the_sweep_sees_the_projects_commands(self):
        """Guards the guard: an empty sweep would pass everything."""
        names = {path.stem for path in project_command_modules()}
        self.assertIn("grading_benchmark", names)
        self.assertIn("resolve_licence_stripe_intent", names)

    def test_every_command_module_defines_a_command_class(self):
        scripts = [
            str(path.relative_to(BASE_DIR))
            for path in project_command_modules()
            if not defines_command_class(path)
        ]
        self.assertEqual(
            scripts,
            [],
            "These run on import, so `manage.py <name>` executes them: move "
            "them to scripts/ or wrap them in a Command.",
        )

    def test_backfill_is_not_a_management_command(self):
        self.assertNotIn("backfill", get_commands())

    def test_the_moved_script_cannot_be_imported_as_a_module(self):
        self.assertTrue(MOVED_SCRIPT.is_file())
        self.assertFalse((MOVED_SCRIPT.parent / "__init__.py").exists())
        self.assertIn("NEVER IMPORTED", MOVED_SCRIPT.read_text())
