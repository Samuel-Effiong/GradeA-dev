"""H-206: with the switch on, every request to the AI provider asks for
zero-retention endpoints. The switch is OFF by default (the owner, 24 October
2026).

`provider: {"data_collection": "deny"}` filters suppliers that TRAIN on data;
none of ours do, so by itself it excludes nobody. Several of our suppliers
RETAIN prompts. Retention is filtered only by `zdr: true` in the provider
object (the provider's routing page, "Zero Data Retention Enforcement").

What these tests hold:

  * with the switch on, the provider block that reaches the SDK's `create`
    call (patched; the arguments are read) is
    `{"data_collection": "deny", "zdr": True}` on EVERY
    path of `AIProcessor.__ai_model`: without and with tools, with an
    override model, with and without a given fallback list, with a response
    schema, with ready-made messages; `zdr` is the boolean True (a string
    "true" may be ignored by the provider), and it sits INSIDE the provider
    object, not in the top-level body;
  * the setting `AI_PROVIDER_ZDR_ENABLED` (default OFF) switches it, is read
    at each call (not once at import), and with it off (or left at its
    default) the block is exactly `{"data_collection": "deny"}` and no `zdr`
    is sent anywhere in the request;
  * the helper returns a NEW dict each call;
  * static: the only `chat.completions.create` call in the repository's code
    is the one in `__ai_model`; every other file that builds a provider
    client or request is on a NAMED list as "sends the provider block" or
    "made-up data only, by statement"; a file added later fails until listed;
    no "data_collection" literal exists outside the helper;
  * the comment says what each field really does.

Run with:
    python manage.py test ai_processor.tests_provider_block
"""

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from ai_processor import services
from ai_processor.services import AIProcessor

BASE = Path(settings.BASE_DIR)
SKIP_DIRS = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    "staticfiles",
    "media",
}

#: Every file that creates a provider client or sends a request to the
#: provider, with its mark. "sends the provider block": the request is built
#: with `_provider_block()`. "made-up data only, by statement": a measurement
#: script that sends only invented papers (no real student work can reach it).
NAMED = {
    "ai_processor/services.py": (
        "sends the provider block",
        "__ai_model builds its one request with _provider_block()",
    ),
    "ai_processor/benchmark/isolation_run8/isolation_harness.py": (
        "sends the provider block",
        "it only replaces the client of ai_processor.services.ai_processor; its requests go "
        "through __ai_model, which sends the block",
    ),
}
#: Scripts that may exist in a working copy but are not tracked in git (the
#: live-timing benchmark). They send NO provider block today; they use invented
#: papers only.
OPTIONAL_NAMED = {
    "benchmark_artifacts/run_live_timing_benchmark.py": (
        "made-up data only, by statement",
        "sends only the model list, no provider block; invented papers (a sentence per answer, "
        "pretend students); never real student work",
    ),
}

CAPTURED = []


def capture(**kwargs):
    CAPTURED.append(kwargs)
    return SimpleNamespace(
        choices=[
            SimpleNamespace(message=SimpleNamespace(content="{}"), finish_reason="stop")
        ],
        usage=SimpleNamespace(total_tokens=1),
        model="x/y",
    )


def call(**arguments):
    """Run the real __ai_model with only the SDK's create replaced; return the arguments
    that reached it."""
    processor = AIProcessor()
    processor.client = SimpleNamespace(  # type: ignore[assignment]
        chat=SimpleNamespace(completions=SimpleNamespace(create=capture))
    )
    del CAPTURED[:]
    processor.get_ai_model_function()(**arguments)
    assert len(CAPTURED) == 1, CAPTURED
    return CAPTURED[0]


TOOLS = [
    {"type": "function", "function": {"name": "t", "parameters": {"type": "object"}}}
]
SCHEMA = {"name": "s", "strict": True, "schema": {"type": "object"}}
#: H-204: every request asks for the cheapest supplier first and never an fp4 endpoint,
#: with zdr on or off (ai_processor/tests_h204_provider_price_sort.py owns these keys).
ROUTING = {"sort": "price", "quantizations": ["fp8", "fp16", "bf16", "fp32", "unknown"]}
EXPECTED = {"data_collection": "deny", "zdr": True, **ROUTING}
OFF = {"data_collection": "deny", **ROUTING}

PATHS: dict[str, dict[str, Any]] = {
    "plain": {"system_prompt": "s", "user_prompt": "u"},
    "with tools": {"system_prompt": "s", "user_prompt": "u", "tool_schemas": TOOLS},
    "with an override model": {
        "system_prompt": "s",
        "user_prompt": "u",
        "override_model": "a/b",
    },
    "with a given fallback list": {
        "system_prompt": "s",
        "user_prompt": "u",
        "sub_models": ["c/d"],
    },
    "with tools and a given fallback list": {
        "system_prompt": "s",
        "user_prompt": "u",
        "tool_schemas": TOOLS,
        "sub_models": ["c/d"],
    },
    "with a response schema": {
        "system_prompt": "s",
        "user_prompt": "u",
        "response_schema": SCHEMA,
    },
    "with ready-made messages": {"messages": [{"role": "user", "content": "m"}]},
    "without a json response": {
        "system_prompt": "s",
        "user_prompt": "u",
        "respond_format": False,
    },
}


@override_settings(AI_PROVIDER_ZDR_ENABLED=True)
class TheRequestAsksForZeroRetentionOnEveryPath(SimpleTestCase):
    def test_the_provider_block_that_reaches_the_sdk_is_exact_on_every_path(self):
        for name, arguments in PATHS.items():
            with self.subTest(path=name):
                sent = call(**arguments)
                self.assertEqual(sent["extra_body"]["provider"], EXPECTED)

    def test_zdr_is_the_boolean_true_not_a_string(self):
        for name, arguments in PATHS.items():
            with self.subTest(path=name):
                value = call(**arguments)["extra_body"]["provider"].get("zdr")
                self.assertIs(value, True)

    def test_zdr_is_inside_the_provider_object_not_the_top_level_body(self):
        for name, arguments in PATHS.items():
            with self.subTest(path=name):
                sent = call(**arguments)
                self.assertIn("zdr", sent["extra_body"]["provider"])
                self.assertNotIn("zdr", sent)
                self.assertNotIn("provider", sent)
                self.assertNotIn("zdr", sent["extra_body"])

    def test_the_rest_of_the_request_is_unchanged(self):
        sent = call(
            system_prompt="s", user_prompt="u", sub_models=["c/d"], override_model="a/b"
        )
        self.assertEqual(sent["model"], "a/b")
        self.assertEqual(sent["extra_body"]["models"], ["c/d"])
        self.assertEqual(sent["temperature"], 0.0)

    def test_the_override_and_the_fallback_list_do_not_change_the_block(self):
        plain = call(system_prompt="s", user_prompt="u")["extra_body"]["provider"]
        pinned = call(
            system_prompt="s", user_prompt="u", override_model="a/b", sub_models=["c/d"]
        )["extra_body"]["provider"]
        self.assertEqual(plain, pinned)


class TheSwitch(SimpleTestCase):
    def test_the_default_is_off(self):
        self.assertIs(settings.AI_PROVIDER_ZDR_ENABLED, False)

    def test_the_settings_module_reads_the_variable_with_default_false(self):
        tree = ast.parse((BASE / "AutoGrader/settings.py").read_text(encoding="utf-8"))
        found = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "AI_PROVIDER_ZDR_ENABLED"
                for t in node.targets
            ):
                found.append(ast.unparse(node.value))
        # ast.unparse writes strings with single quotes.
        self.assertEqual(found, ["env.bool('AI_PROVIDER_ZDR_ENABLED', default=False)"])

    def test_at_the_default_no_request_carries_zdr_anywhere(self):
        self.assertEqual(services._provider_block(), OFF)
        for name, arguments in PATHS.items():
            with self.subTest(path=name):
                sent = call(**arguments)
                self.assertEqual(sent["extra_body"]["provider"], OFF)
                self.assertNotIn("zdr", sent["extra_body"])
                self.assertNotIn("zdr", sent)

    def test_off_sends_no_zdr_and_on_sends_zdr_true_on_every_path(self):
        for name, arguments in PATHS.items():
            with self.subTest(path=name):
                with override_settings(AI_PROVIDER_ZDR_ENABLED=False):
                    off = call(**arguments)["extra_body"]["provider"]
                with override_settings(AI_PROVIDER_ZDR_ENABLED=True):
                    on = call(**arguments)["extra_body"]["provider"]
                self.assertNotIn("zdr", off)
                self.assertEqual(off, OFF)
                self.assertIs(on.get("zdr"), True)
                self.assertEqual(on, EXPECTED)

    @override_settings(AI_PROVIDER_ZDR_ENABLED=False)
    def test_with_the_switch_off_the_block_is_exactly_data_collection_deny(self):
        self.assertEqual(services._provider_block(), OFF)
        for name, arguments in PATHS.items():
            with self.subTest(path=name):
                sent = call(**arguments)
                self.assertEqual(sent["extra_body"]["provider"], OFF)

    def test_the_switch_is_read_at_each_call_not_once_at_import(self):
        off = call(system_prompt="s", user_prompt="u")["extra_body"]["provider"]
        with override_settings(AI_PROVIDER_ZDR_ENABLED=True):
            on = call(system_prompt="s", user_prompt="u")["extra_body"]["provider"]
        again = call(system_prompt="s", user_prompt="u")["extra_body"]["provider"]
        self.assertEqual((off, on, again), (OFF, EXPECTED, OFF))


@override_settings(AI_PROVIDER_ZDR_ENABLED=True)
class TheHelper(SimpleTestCase):
    def test_it_exists_and_returns_the_block(self):
        self.assertEqual(services._provider_block(), EXPECTED)

    def test_it_returns_a_new_dict_each_call(self):
        first = services._provider_block()
        first["zdr"] = False
        first["extra"] = 1
        self.assertEqual(services._provider_block(), EXPECTED)
        self.assertIsNot(services._provider_block(), services._provider_block())

    def test_a_change_to_a_sent_block_cannot_reach_the_next_request(self):
        """The change is to keys the helper does not reset (an added key, a deleted
        data_collection), so a block shared between calls would carry it forward."""
        sent = call(system_prompt="s", user_prompt="u")["extra_body"]["provider"]
        sent["extra"] = 1
        del sent["data_collection"]
        self.assertEqual(
            call(system_prompt="s", user_prompt="u")["extra_body"]["provider"], EXPECTED
        )


def _python_files():
    for path in BASE.rglob("*.py"):
        parts = set(path.relative_to(BASE).parts)
        if parts & SKIP_DIRS:
            continue
        relative = path.relative_to(BASE).as_posix()
        name = path.name
        if (
            name.startswith(("test_", "tests_"))
            or name in ("tests.py", "conftest.py")
            or "/tests/" in relative
            or "/migrations/" in relative
            or relative.startswith("docs/")
        ):
            continue
        yield relative, path


def _call_chain(node):
    parts = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    return ".".join(reversed(parts))


class NoRequestCanBeSentWithoutTheBlock(SimpleTestCase):
    def _users_of_the_provider(self):
        found = {}
        for relative, path in _python_files():
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    chain = _call_chain(node.func)
                    if (
                        chain.endswith("completions.create")
                        or chain.split(".")[-1] == "OpenAI"
                    ):
                        found.setdefault(relative, []).append(chain)
        return found

    def test_the_helper_is_defined_exactly_once_at_module_level(self):
        tree = ast.parse(
            (BASE / "ai_processor/services.py").read_text(encoding="utf-8")
        )
        helpers = [
            n
            for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == "_provider_block"
        ]
        self.assertEqual(len(helpers), 1)

    def test_the_only_create_call_is_the_one_in_ai_model(self):
        tree = ast.parse(
            (BASE / "ai_processor/services.py").read_text(encoding="utf-8")
        )
        owners = []

        def visit(node, function):
            if isinstance(node, ast.FunctionDef):
                function = node.name
            if isinstance(node, ast.Call) and _call_chain(node.func).endswith(
                "completions.create"
            ):
                owners.append(function)
            for child in ast.iter_child_nodes(node):
                visit(child, function)

        visit(tree, None)
        self.assertEqual(owners, ["__ai_model"])

    def test_the_request_takes_the_block_from_the_helper(self):
        tree = ast.parse(
            (BASE / "ai_processor/services.py").read_text(encoding="utf-8")
        )
        uses = 0
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "__ai_model":
                uses = sum(
                    1
                    for n in ast.walk(node)
                    if isinstance(n, ast.Call)
                    and _call_chain(n.func) == "_provider_block"
                )
        self.assertEqual(uses, 1)

    def test_every_file_that_builds_a_client_or_a_request_is_on_the_named_list(self):
        found = self._users_of_the_provider()
        listed = set(NAMED) | set(OPTIONAL_NAMED)
        self.assertEqual(
            sorted(set(found) - listed), [], "list the new file and mark it"
        )

    def test_every_listed_file_that_exists_carries_a_mark_and_a_reason(self):
        for relative, (mark, reason) in {**NAMED, **OPTIONAL_NAMED}.items():
            with self.subTest(file=relative):
                self.assertIn(
                    mark,
                    ("sends the provider block", "made-up data only, by statement"),
                )
                self.assertTrue(reason.strip())
        for relative in NAMED:
            self.assertTrue((BASE / relative).exists(), relative)

    def test_every_listed_file_other_than_services_has_zero_create_calls(self):
        """A file marked "sends the provider block" other than services.py must go
        through __ai_model: it may build a client, never call create itself."""
        for relative in {**NAMED, **OPTIONAL_NAMED}:
            if relative == "ai_processor/services.py":
                continue
            path = BASE / relative
            if not path.exists():
                continue
            tree = ast.parse(path.read_text(encoding="utf-8"))
            creates = [
                n.lineno
                for n in ast.walk(tree)
                if isinstance(n, ast.Call)
                and _call_chain(n.func).endswith("completions.create")
            ]
            with self.subTest(file=relative):
                self.assertEqual(creates, [])

    def test_the_provider_host_appears_only_in_listed_files(self):
        """A request through requests/httpx/urllib would not show as completions.create;
        the host name gives it away."""
        outside = []
        listed = set(NAMED) | set(OPTIONAL_NAMED)
        for relative, path in _python_files():
            if relative in listed:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            if "openrouter.ai" in text:
                outside.append(relative)
        self.assertEqual(outside, [])

    def test_the_data_collection_literal_exists_only_inside_the_helper(self):
        outside = []
        for relative, path in _python_files():
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            helper_lines = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.FunctionDef) and node.name == "_provider_block":
                    helper_lines.update(
                        range(node.lineno, (node.end_lineno or node.lineno) + 1)
                    )
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Constant)
                    and isinstance(node.value, str)
                    and node.value == "data_collection"
                    and node.lineno not in helper_lines
                ):
                    outside.append(f"{relative}:{node.lineno}")
        self.assertEqual(outside, [])


class TheCommentSaysWhatEachFieldDoes(SimpleTestCase):
    def _text(self):
        source = (BASE / "ai_processor/services.py").read_text(encoding="utf-8")
        return " ".join(source.replace("# ", " ").split()).lower()

    def test_the_old_claim_that_deny_refuses_providers_that_retain_is_gone(self):
        self.assertNotIn(
            "refuse any upstream provider (e.g. deepseek) that may retain or train on this request",
            self._text(),
        )

    def test_the_comment_says_deny_filters_suppliers_that_train_and_zdr_those_that_retain(
        self,
    ):
        text = self._text()
        self.assertIn("suppliers that train on data", text)
        self.assertIn("suppliers that retain prompts", text)
        self.assertIn("zdr", text)
