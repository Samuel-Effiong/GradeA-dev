"""
H-246: Sentry's environment can be named apart from ENVIRONMENT.

Beta runs with ENVIRONMENT=prod, so every beta event reached Sentry as
"prod" (seen in the Enhanced Electronics project, 15 October 2026: the
Epic A `reason_code_rate` metric, which only beta's code sends, filed under
"prod"). SENTRY_ENVIRONMENT=beta on the beta server keeps them apart.

Run with:
    python manage.py test AutoGrader.tests_h246_sentry_environment
"""

import ast
import os

from django.conf import settings
from django.test import SimpleTestCase

from AutoGrader.sentry_environment import sentry_environment


def _settings_tree():
    with open(os.path.join(settings.BASE_DIR, "AutoGrader", "settings.py")) as fh:
        return ast.parse(fh.read())


def _init_call(tree):
    [init] = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "sentry_sdk.init"
    ]
    return init


class SentryEnvironmentTest(SimpleTestCase):
    def test_a_named_override_wins(self):
        self.assertEqual(sentry_environment("prod", "beta"), "beta")

    def test_no_override_keeps_the_apps_environment(self):
        for override in (None, "", "   ", "\n"):
            with self.subTest(override=override):
                self.assertEqual(sentry_environment("prod", override), "prod")

    def test_the_override_is_trimmed(self):
        self.assertEqual(sentry_environment("prod", "  beta \n"), "beta")

    def test_a_non_text_override_is_ignored(self):
        self.assertEqual(sentry_environment("dev", 5), "dev")


class WiringTest(SimpleTestCase):
    def test_sentry_is_given_the_override_with_environment_as_fallback(self):
        keywords = {
            keyword.arg: ast.unparse(keyword.value)
            for keyword in _init_call(_settings_tree()).keywords
        }
        self.assertEqual(
            keywords.get("environment"),
            "sentry_environment(ENVIRONMENT, env.str('SENTRY_ENVIRONMENT', default=''))",
        )

    def test_the_release_is_left_to_the_sdk(self):
        # sentry-sdk reads SENTRY_RELEASE itself; passing release= here would
        # override that, and None would blank it.
        keywords = {k.arg for k in _init_call(_settings_tree()).keywords}
        self.assertNotIn("release", keywords)

    def test_the_helper_is_imported_outside_the_missing_package_try(self):
        # Like the scrubbing hooks: a broken import must fail at start, not
        # turn Sentry off.
        tree = _settings_tree()
        under_a_try = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                for child in node.body:
                    under_a_try.update(id(inner) for inner in ast.walk(child))
        [imported] = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module == "AutoGrader.sentry_environment"
        ]
        self.assertNotIn(id(imported), under_a_try)


class SdkTest(SimpleTestCase):
    """The name reaches an event the SDK sends, and SENTRY_RELEASE reaches
    its release, through a client with a capturing transport (nothing is
    sent anywhere)."""

    def test_an_event_carries_the_environment_and_the_release(self):
        import sentry_sdk
        from sentry_sdk.transport import Transport

        sent = []

        class Capture(Transport):
            def capture_envelope(self, envelope):
                sent.append(envelope)

        previous = os.environ.get("SENTRY_RELEASE")
        os.environ["SENTRY_RELEASE"] = "beta-abc1234"
        try:
            client = sentry_sdk.Client(
                dsn="https://public@example.invalid/1",
                environment=sentry_environment("prod", "beta"),
                transport=Capture,
                default_integrations=False,
            )
            client.capture_event(
                {"message": "h246 probe"}, scope=sentry_sdk.Scope(client=client)
            )
            client.flush()
        finally:
            if previous is None:
                os.environ.pop("SENTRY_RELEASE", None)
            else:
                os.environ["SENTRY_RELEASE"] = previous

        events = [
            item.payload.json
            for env in sent
            for item in env.items
            if item.type == "event"
        ]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["environment"], "beta")
        self.assertEqual(events[0]["release"], "beta-abc1234")
