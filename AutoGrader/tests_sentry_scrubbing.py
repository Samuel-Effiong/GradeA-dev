"""
H-89: an error report sent to Sentry carries no email address in its text.

Sentry's logging integration is not a handler. For every ERROR record it
builds an event from the record's parts: the message template, the raw
arguments, and the exception object's own text. So the log record factory
(AutoGrader/log_scrubbing.py), which scrubs what handlers print, does not
change what Sentry receives. AutoGrader/sentry_scrubbing.py does, through
three hooks passed to sentry_sdk.init: before_send (events),
before_breadcrumb (the log lines that led up to an event) and
before_send_log (the log stream).

The hooks touch text only: the log entry, the exception values, frame
variables, breadcrumbs and extras. Since H-167 the event's request, user
context, tags and contexts pass the same scrub (H-89 had left them alone).
"""

import ast
import importlib
import os

from django.conf import settings
from django.test import SimpleTestCase

ADDRESS = "someone.private@school-example.edu"
OTHER = "second.person@example.org"


def event_for_a_logged_error():
    """The shape sentry_sdk's EventHandler builds for
    logger.error("...", exc_info=True)."""
    return {
        "level": "error",
        "logger": "billing.license_service",
        "logentry": {
            "message": "Failed to renew credits for teacher %s (%s)",
            "formatted": f"Failed to renew credits for teacher 4821 ({ADDRESS})",
            "params": [4821, ADDRESS],
        },
        "exception": {
            "values": [
                {
                    "type": "IntegrityError",
                    "value": f"DETAIL:  Key (email)=({ADDRESS}) already exists.",
                    "mechanism": {"type": "logging", "handled": True},
                    "stacktrace": {
                        "frames": [
                            {
                                "function": "process_license_renewal",
                                "filename": "billing/license_service.py",
                                "vars": {
                                    "teacher": f"<CustomUser: {OTHER}>",
                                    "renewal_count": "3",
                                    "emails": [ADDRESS, OTHER],
                                },
                            }
                        ]
                    },
                },
                {"type": "ValueError", "value": f"Could not invite {OTHER}"},
            ]
        },
        "breadcrumbs": {
            "values": [
                {"category": "billing.tasks", "message": f"Renewing for {ADDRESS}"},
                {"category": "query", "message": "SELECT 1"},
            ]
        },
        "extra": {"note": f"contact {OTHER}", "attempt": 2},
        "user": {"id": "4821", "email": ADDRESS},
        "tags": {"school": "77", "owner": ADDRESS},
        "request": {"url": "https://api.example.com/licenses/77/"},
    }


class SentryScrubbingTestCase(SimpleTestCase):
    def setUp(self):
        self.hooks = importlib.import_module("AutoGrader.sentry_scrubbing")


class BeforeSendTests(SentryScrubbingTestCase):
    def test_no_address_is_left_in_the_events_text(self):
        event = self.hooks.scrub_event(event_for_a_logged_error(), {})

        text_parts = {
            key: event[key] for key in ("logentry", "exception", "breadcrumbs", "extra")
        }
        self.assertNotIn("@", repr(text_parts))
        self.assertEqual(
            event["logentry"],
            {
                "message": "Failed to renew credits for teacher %s (%s)",
                "formatted": "Failed to renew credits for teacher 4821 ([email])",
                "params": [4821, "[email]"],
            },
        )
        first, second = event["exception"]["values"]
        self.assertEqual(
            first["value"], "DETAIL:  Key (email)=([email]) already exists."
        )
        self.assertEqual(second["value"], "Could not invite [email]")

    def test_what_makes_the_report_useful_is_kept(self):
        event = self.hooks.scrub_event(event_for_a_logged_error(), {})

        first = event["exception"]["values"][0]
        self.assertEqual(first["type"], "IntegrityError")
        frame = first["stacktrace"]["frames"][0]
        self.assertEqual(frame["function"], "process_license_renewal")
        self.assertEqual(frame["filename"], "billing/license_service.py")
        self.assertEqual(
            frame["vars"],
            {
                "teacher": "<CustomUser: [email]>",
                "renewal_count": "3",
                "emails": ["[email]", "[email]"],
            },
        )
        self.assertEqual(event["level"], "error")
        self.assertEqual(event["logger"], "billing.license_service")
        self.assertEqual(event["extra"], {"note": "contact [email]", "attempt": 2})
        self.assertEqual(
            event["breadcrumbs"]["values"][1],
            {"category": "query", "message": "SELECT 1"},
        )

    def test_frame_variables_that_hold_no_address_are_unchanged(self):
        """The debugging value of locals is kept: ids, UUIDs, numbers,
        reprs and nested structures come through exactly as they were."""
        local_variables = {
            "user_id": "4821",
            "license_id": "'3f2b8c1e-9d4a-4c7b-8e21-5a6f0d9b7c13'",
            "allocation": "<SchoolCreditAllocation: SchoolCreditAllocation object (77)>",
            "amount": 20000,
            "ratio": 0.5,
            "active": True,
            "missing": None,
            "ids": ["4821", "4822"],
            "meta": {"allocation_id": "77", "refresh_month": "2026-10"},
            "decorator": "@transaction.atomic",
            "handle": "@grader_bot",
        }
        event = {
            "exception": {
                "values": [
                    {
                        "type": "ValueError",
                        "value": "bad amount",
                        "stacktrace": {
                            "frames": [{"function": "f", "vars": dict(local_variables)}]
                        },
                    }
                ]
            }
        }

        scrubbed = self.hooks.scrub_event(event, {})

        frame = scrubbed["exception"]["values"][0]["stacktrace"]["frames"][0]
        self.assertEqual(frame["vars"], local_variables)

    def test_user_context_tags_and_request_are_scrubbed_too(self):
        """H-167 reverses H-89's "left alone" for these parts (Senior
        Manager, 2026-10-07): they pass the same scrub as the rest. More
        in tests_sentry_sends_no_variables.py."""
        before = event_for_a_logged_error()
        self.assertEqual(before["user"]["email"], ADDRESS)
        self.assertEqual(before["tags"]["owner"], ADDRESS)

        event = self.hooks.scrub_event(event_for_a_logged_error(), {})

        self.assertEqual(event["user"], {"id": "4821", "email": "[email]"})
        self.assertEqual(event["tags"], {"school": "77", "owner": "[email]"})
        # Nothing to replace in it: unchanged.
        self.assertEqual(event["request"], before["request"])

    def test_an_event_with_none_of_these_parts_passes_through(self):
        for event in ({}, {"message": "plain"}, {"exception": None, "logentry": None}):
            with self.subTest(event=event):
                self.assertEqual(self.hooks.scrub_event(dict(event), {}), event)

    def test_the_threads_of_an_event(self):
        """An event can carry a stack per thread, with frame variables,
        beside (or instead of) an exception."""
        event = self.hooks.scrub_event(
            {
                "threads": {
                    "values": [
                        {
                            "id": 140213,
                            "name": f"worker for {ADDRESS}",
                            "stacktrace": {
                                "frames": [
                                    {
                                        "function": "send_invite",
                                        "vars": {"to": OTHER, "attempt": "2"},
                                    }
                                ]
                            },
                        }
                    ]
                }
            },
            {},
        )

        self.assertNotIn(ADDRESS, repr(event))
        self.assertNotIn(OTHER, repr(event))
        [thread] = event["threads"]["values"]
        self.assertEqual(thread["id"], 140213)
        self.assertEqual(thread["name"], "worker for [email]")
        self.assertEqual(
            thread["stacktrace"]["frames"][0]["vars"],
            {"to": "[email]", "attempt": "2"},
        )

    def test_the_spans_of_a_transaction(self):
        """A sampled transaction is an event of its own: it does not pass
        before_send, and its text is in its spans (a query, an outgoing
        request's URL)."""
        event = self.hooks.scrub_event(
            {
                "type": "transaction",
                "transaction": "/api/licenses/{id}/",
                "spans": [
                    {
                        "op": "http.client",
                        "description": f"GET https://mail.example.com/v1/check?to={ADDRESS}",
                        "data": {
                            "url": f"https://mail.example.com/v1/check?to={ADDRESS}"
                        },
                        "span_id": "a1b2c3d4e5f60718",
                    },
                    {
                        "op": "db",
                        "description": f"SELECT 1 FROM users /* invited by {OTHER} */",
                        "span_id": "0918f6e5d4c3b2a1",
                    },
                ],
            },
            {},
        )

        self.assertNotIn(ADDRESS, repr(event))
        self.assertNotIn(OTHER, repr(event))
        self.assertEqual(event["transaction"], "/api/licenses/{id}/")
        self.assertEqual(
            event["spans"][0]["description"],
            "GET https://mail.example.com/v1/check?to=[email]",
        )
        self.assertEqual(event["spans"][0]["span_id"], "a1b2c3d4e5f60718")
        self.assertEqual(event["spans"][1]["op"], "db")

    def test_a_plain_message_event(self):
        event = self.hooks.scrub_event(
            {"message": f"capture_message for {ADDRESS}"}, {}
        )

        self.assertEqual(event["message"], "capture_message for [email]")

    def test_a_dsn_in_an_exception_never_shows_its_password(self):
        password = "s3cret-pass"  # pragma: allowlist secret
        event = {
            "exception": {
                "values": [
                    {
                        "type": "ConnectionError",
                        # Built from parts: no line here, and no failing
                        # test's log, holds a URL with a password.
                        "value": "Error connecting to redis://:"
                        + password
                        + "@redis:6379/0",
                    }
                ]
            }
        }

        value = self.hooks.scrub_event(event, {})["exception"]["values"][0]["value"]

        self.assertEqual(
            value.replace(password, "[the password was here]"),
            "Error connecting to redis://[credentials]@redis:6379/0",
        )

    def test_an_argument_that_is_not_text_yet(self):
        """An object among the params whose text is an address."""

        class Person:
            def __str__(self):
                return ADDRESS

        event = {"logentry": {"message": "Enrolled %s", "params": (Person(), 3, None)}}

        params = self.hooks.scrub_event(event, {})["logentry"]["params"]

        self.assertEqual(list(params), ["[email]", 3, None])

    def test_it_fails_closed_and_never_raises(self):
        """A part that cannot be scrubbed is replaced by a marker; the event
        is still sent, without that part's text."""

        class Explodes:
            def __str__(self):
                raise RuntimeError("no text for you")

        event = event_for_a_logged_error()
        event["logentry"]["params"] = [Explodes(), ADDRESS]

        scrubbed = self.hooks.scrub_event(event, {})

        self.assertIsNotNone(scrubbed)
        self.assertEqual(scrubbed["logentry"], self.hooks.WITHHELD)
        self.assertNotIn("@", repr(scrubbed["exception"]))
        self.assertEqual(scrubbed["exception"]["values"][0]["type"], "IntegrityError")


class BreadcrumbAndLogTests(SentryScrubbingTestCase):
    def test_a_breadcrumbs_message_and_data(self):
        crumb = {
            "category": "billing.tasks",
            "message": f"Renewing for {ADDRESS}",
            "data": {"detail": f"owner {OTHER}", "count": 2},
        }

        self.assertEqual(
            self.hooks.scrub_breadcrumb(crumb, {}),
            {
                "category": "billing.tasks",
                "message": "Renewing for [email]",
                "data": {"detail": "owner [email]", "count": 2},
            },
        )

    def test_a_log_items_body_and_attributes(self):
        log = {
            "severity_text": "error",
            "body": f"Failed for {ADDRESS}",
            "attributes": {
                "sentry.message.template": "Failed for %s",
                "sentry.message.parameter.0": ADDRESS,
                "logger.name": "billing.tasks",
                "code.line.number": 12,
            },
        }

        self.assertEqual(
            self.hooks.scrub_log(log, {}),
            {
                "severity_text": "error",
                "body": "Failed for [email]",
                "attributes": {
                    "sentry.message.template": "Failed for %s",
                    "sentry.message.parameter.0": "[email]",
                    "logger.name": "billing.tasks",
                    "code.line.number": 12,
                },
            },
        )

    def test_the_small_hooks_never_raise_either(self):
        class Explodes:
            def __str__(self):
                raise RuntimeError("no text for you")

        crumb = self.hooks.scrub_breadcrumb(
            {"message": Explodes(), "level": "info"}, {}
        )
        log = self.hooks.scrub_log({"body": Explodes(), "severity_text": "info"}, {})

        self.assertEqual(crumb, {"message": self.hooks.WITHHELD, "level": "info"})
        self.assertEqual(log, {"body": self.hooks.WITHHELD, "severity_text": "info"})


class WiringTests(SimpleTestCase):
    def test_settings_pass_the_hooks_to_sentry(self):
        with open(os.path.join(settings.BASE_DIR, "AutoGrader", "settings.py")) as fh:
            tree = ast.parse(fh.read())
        [init] = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and ast.unparse(node.func) == "sentry_sdk.init"
        ]
        keywords = {
            keyword.arg: ast.unparse(keyword.value) for keyword in init.keywords
        }

        self.assertEqual(keywords.get("before_send"), "scrub_event")
        # A sampled transaction is sent without passing before_send.
        self.assertEqual(keywords.get("before_send_transaction"), "scrub_event")
        self.assertEqual(keywords.get("before_breadcrumb"), "scrub_breadcrumb")
        self.assertEqual(keywords.get("before_send_log"), "scrub_log")
        self.assertEqual(keywords.get("send_default_pii"), "False")

    def test_a_failure_to_import_the_hooks_is_not_swallowed(self):
        """settings.py has a try/except ImportError for a missing sentry-sdk
        package. Our own hooks module is imported outside it, and so is the
        init call: a deploy where the hooks cannot be imported must fail at
        start, not run with Sentry silently off."""
        with open(os.path.join(settings.BASE_DIR, "AutoGrader", "settings.py")) as fh:
            tree = ast.parse(fh.read())
        under_a_try = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Try):
                for child in node.body:
                    under_a_try.update(id(inner) for inner in ast.walk(child))
        hooks_imports = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module == "AutoGrader.sentry_scrubbing"
        ]
        init_calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and ast.unparse(node.func) == "sentry_sdk.init"
        ]

        self.assertEqual(len(hooks_imports), 1)
        self.assertEqual(len(init_calls), 1)
        self.assertNotIn(id(hooks_imports[0]), under_a_try)
        self.assertNotIn(id(init_calls[0]), under_a_try)
