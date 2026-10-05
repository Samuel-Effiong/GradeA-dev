"""
H-118: no test module starts Playwright in the process that imports it.

`assignments/tests_pdf_renderer.py` asks, when it is imported, whether a
headless Chromium can be launched, so that the real-rendering tests can be
skipped where there is none. It used to ask by starting Playwright's
driver (Node) and a Chromium inside the importing process. In a parallel
run that process is the test runner's parent, at discovery, and Playwright
hands the driver the process's own stderr: for the length of the probe the
run's output pipe is in non-blocking mode for every process that holds it
(H-107, finding B). The driver exits by itself and the mode goes back, so
the window is short; H-107's result stream and rule 18 cover it. H-110
does not remove it, because the probe does not go through the renderer.

Now the question is asked in a child interpreter whose stdin, stdout and
stderr are the null device, so the driver never holds the run's output.

Three things are pinned here:
  * the defect itself, in a fresh interpreter: importing the module starts
    no driver in the importing process;
  * the probe gives its child nothing of the run's, and reads its answer
    from the exit code;
  * a repo-wide rule, by reading the source: no test module reaches
    Playwright's starters in code that runs at import.
"""

import ast
import asyncio
import importlib
import os
import subprocess
import sys
from unittest import mock

from django.conf import settings
from django.test import SimpleTestCase

STARTED = "DRIVERS STARTED IN THE IMPORTING PROCESS:"

#: Run in a fresh interpreter: the module is already imported in the test's
#: own process. Playwright starts its driver with
#: asyncio.create_subprocess_exec; here that is replaced by a recorder that
#: refuses, so the count is of starts attempted in THIS process, and no
#: browser is started by it.
IMPORT_PROBE = f"""
import asyncio

import django

django.setup()

started = []


async def refuse(*args, **kwargs):
    started.append(kwargs.get("stderr"))
    raise OSError("recorded, not started")


asyncio.create_subprocess_exec = refuse

import assignments.tests_pdf_renderer as module

assert isinstance(module._CHROMIUM_AVAILABLE, bool)
print({STARTED!r}, len(started), flush=True)
"""

#: The names that start Playwright's driver.
STARTERS = {"sync_playwright", "async_playwright"}

SKIPPED_DIRS = {"migrations", "node_modules", "venv"}


def is_test_module(path):
    parts = path.split(os.sep)
    name = parts[-1]
    return name.endswith(".py") and (
        name.startswith(("test_", "tests_"))
        or name == "tests.py"
        or "tests" in parts[:-1]
    )


def all_test_modules():
    for folder, subfolders, names in os.walk(settings.BASE_DIR):
        subfolders[:] = sorted(
            name
            for name in subfolders
            if name not in SKIPPED_DIRS and not name.startswith(".")
        )
        for name in sorted(names):
            path = os.path.relpath(os.path.join(folder, name), settings.BASE_DIR)
            if is_test_module(path):
                yield path


FUNCTIONS = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)


def nodes_run_at_import(tree):
    """Every node that is evaluated when the module is imported: the module
    body and class bodies, with the decorators, default values and
    annotations of functions, but not the bodies of functions."""
    pending = list(ast.iter_child_nodes(tree))
    while pending:
        node = pending.pop()
        yield node
        if isinstance(node, FUNCTIONS):
            pending.extend(getattr(node, "decorator_list", []))
            pending.extend(node.args.defaults)
            pending.extend(default for default in node.args.kw_defaults if default)
        else:
            pending.extend(ast.iter_child_nodes(node))


def names_in(node):
    for inner in ast.walk(node):
        if isinstance(inner, ast.Name):
            yield inner.id
        elif isinstance(inner, ast.Attribute):
            yield inner.attr


def module_functions(tree):
    """The module's own functions (not methods, not nested functions)."""
    pending = list(tree.body)
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            yield node
        elif not isinstance(node, ast.ClassDef):
            pending.extend(
                child
                for child in ast.iter_child_nodes(node)
                if isinstance(child, ast.stmt)
            )


def playwright_started_at_import(source):
    """Line numbers of code that runs at import and reaches one of the
    starters: by name, or by calling a function of the same module whose
    body reaches one (through any number of such functions)."""
    tree = ast.parse(source)
    reaching = {
        function.name: {
            name for statement in function.body for name in names_in(statement)
        }
        for function in module_functions(tree)
    }
    starters = set(STARTERS)
    grew = True
    while grew:
        grew = False
        for name, used in reaching.items():
            if name not in starters and used & starters:
                starters.add(name)
                grew = True
    found = set()
    for node in nodes_run_at_import(tree):
        if isinstance(node, ast.Call) and set(names_in(node.func)) & starters:
            found.add(node.lineno)
    return sorted(found)


class ImportingTheRendererTestsStartsNoDriverHereTests(SimpleTestCase):
    def test_no_driver_is_started_in_the_importing_process(self):
        probe = subprocess.run(
            [sys.executable, "-c", IMPORT_PROBE],
            cwd=settings.BASE_DIR,
            env={
                **os.environ,
                "DJANGO_SETTINGS_MODULE": os.environ["DJANGO_SETTINGS_MODULE"],
            },
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=180,
        )

        shown = f"stdout:\n{probe.stdout}\nstderr:\n{probe.stderr[-3000:]}"
        self.assertEqual(probe.returncode, 0, shown)
        self.assertIn(f"{STARTED} 0", probe.stdout, shown)


class TheProbeAsksAChildTests(SimpleTestCase):
    def setUp(self):
        self.module = importlib.import_module("assignments.tests_pdf_renderer")

    def ask(self, **outcome):
        with mock.patch.object(subprocess, "run", **outcome) as run, mock.patch.object(
            asyncio,
            "create_subprocess_exec",
            side_effect=AssertionError("started here"),
        ):
            answer = self.module._chromium_available()
        return answer, run

    def test_the_child_gets_nothing_of_the_runs_output(self):
        _answer, run = self.ask(
            return_value=mock.Mock(spec=["returncode"], returncode=0)
        )

        run.assert_called_once()
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs.get("stdin"), subprocess.DEVNULL)
        self.assertEqual(kwargs.get("stdout"), subprocess.DEVNULL)
        self.assertEqual(kwargs.get("stderr"), subprocess.DEVNULL)
        self.assertNotIn("capture_output", kwargs)

    def test_the_child_is_this_interpreter_running_the_launch(self):
        _answer, run = self.ask(
            return_value=mock.Mock(spec=["returncode"], returncode=0)
        )

        command = run.call_args.args[0]
        self.assertEqual(command[:2], [sys.executable, "-c"])
        self.assertIn("sync_playwright", command[2])
        self.assertIn("chromium.launch", command[2])
        compile(command[2], "<the probe>", "exec")

    def test_the_child_is_given_a_time_limit(self):
        _answer, run = self.ask(
            return_value=mock.Mock(spec=["returncode"], returncode=0)
        )

        self.assertEqual(run.call_args.kwargs.get("timeout"), 120)

    def test_exit_code_zero_means_available(self):
        answer, _run = self.ask(
            return_value=mock.Mock(spec=["returncode"], returncode=0)
        )

        self.assertIs(answer, True)

    def test_any_other_exit_code_means_not_available(self):
        for code in (1, 2, -9):
            with self.subTest(code=code):
                answer, _run = self.ask(
                    return_value=mock.Mock(spec=["returncode"], returncode=code)
                )

                self.assertIs(answer, False)

    def test_a_child_that_cannot_be_run_or_does_not_end_means_not_available(self):
        for error in (
            OSError("no interpreter"),
            subprocess.TimeoutExpired(cmd="the probe", timeout=120),
        ):
            with self.subTest(error=type(error).__name__):
                answer, _run = self.ask(side_effect=error)

                self.assertIs(answer, False)


class NoTestModuleStartsPlaywrightAtImportTests(SimpleTestCase):
    def test_no_test_module_reaches_a_starter_at_import(self):
        offenders = []
        scanned = 0
        for path in all_test_modules():
            with open(
                os.path.join(settings.BASE_DIR, path), encoding="utf-8"
            ) as handle:
                source = handle.read()
            scanned += 1
            if not STARTERS & set(source.replace("(", " ").replace(".", " ").split()):
                continue
            offenders += [
                f"{path}:{line}" for line in playwright_started_at_import(source)
            ]

        self.assertGreater(
            scanned, 100, "the scan found too few test modules to mean anything"
        )
        self.assertEqual(
            offenders,
            [],
            "These lines run at import and start Playwright in the importing "
            "process, on its own stderr (H-118, H-107). Ask in a child process "
            "with its own stderr, as assignments/tests_pdf_renderer.py does.",
        )

    def test_the_scan_reads_the_renderer_tests(self):
        self.assertIn(
            os.path.join("assignments", "tests_pdf_renderer.py"),
            set(all_test_modules()),
        )

    def test_the_rule_sees_each_shape(self):
        shapes = {
            "a call at module level": "from playwright.sync_api import sync_playwright\n"
            "with sync_playwright() as p:\n    pass\n",
            "through the module": "import playwright.sync_api as api\n"
            "p = api.sync_playwright().start()\n",
            "through a function of the module (the old probe)": "def available():\n"
            "    from playwright.sync_api import sync_playwright\n"
            "    with sync_playwright() as p:\n        return True\n"
            "AVAILABLE = available()\n",
            "through two functions": "def inner():\n    return async_playwright()\n"
            "def outer():\n    return inner()\n"
            "X = outer()\n",
            "in a class body": "class T:\n    browser = sync_playwright().start()\n",
            "in a decorator": "import unittest\n"
            "def available():\n    return bool(sync_playwright())\n"
            "@unittest.skipUnless(available(), 'no browser')\n"
            "def test_it():\n    pass\n",
            "in a default value": "def available():\n    return bool(sync_playwright())\n"
            "def helper(ok=available()):\n    return ok\n",
            "under a condition": "import os\n"
            "if os.environ.get('X'):\n    p = sync_playwright().start()\n",
        }
        for shape, source in shapes.items():
            with self.subTest(shape=shape):
                self.assertTrue(playwright_started_at_import(source))

    def test_the_rule_allows_what_does_not_run_at_import(self):
        allowed = {
            "inside a test": "class T:\n    def test_it(self):\n"
            "        with sync_playwright() as p:\n            pass\n",
            "a function that is defined and not called": "def available():\n"
            "    with sync_playwright() as p:\n        return True\n",
            "the import alone": "from playwright.sync_api import sync_playwright\n",
            "a patch target named in a string": "from unittest import mock\n"
            "P = mock.patch('assignments.pdf_renderer.async_playwright')\n",
            "asked in a child (the new probe)": "import subprocess, sys\n"
            "PROBE = 'from playwright.sync_api import sync_playwright'\n"
            "def available():\n"
            "    return subprocess.run([sys.executable, '-c', PROBE]).returncode == 0\n"
            "AVAILABLE = available()\n",
        }
        for shape, source in allowed.items():
            with self.subTest(shape=shape):
                self.assertEqual(playwright_started_at_import(source), [])
