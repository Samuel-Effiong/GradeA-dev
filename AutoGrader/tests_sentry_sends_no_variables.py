"""
H-167: an error report carries no frame variables and no request body, and
a value written as `password=...` is replaced wherever text is scrubbed.

WHAT WAS WRONG (found by reading, 2026-10-07)
---------------------------------------------
`sentry_sdk.init` was called without `include_local_variables`, whose
default is True: every error event carried each frame's local variables as
text, to a third party. Those are whatever the code held: a name, an
address, an answer, a temporary password in a queued email's arguments, a
token. One instance, the one the row was opened for: when the queue cannot
be reached, a frame of the Redis client holds its connection pool, and the
pool prints itself with every connection keyword, `password=<value>`
among them. H-89's scrubber knows addresses and the user-and-password part
of a URL only, and its docstring named this form as a limit.

It was also called without `max_request_body_size`, whose default
("medium") puts the failing request's parsed body, up to 10,000 bytes, in
the event. That does not depend on `send_default_pii`, and H-89's hooks
left the event's `request` alone.

THE RULING (Senior Manager, 2026-10-07)
---------------------------------------
1.  `include_local_variables=False`.
1b. `max_request_body_size="never"`.
2.  The scrubber replaces what follows `password=`, `passwd=`, `secret=`
    and `token=`, as a second defence for text that reaches an event or a
    log line by another road.
3.  The event's `request`, `tags`, `user` and `contexts` join the parts
    the hooks scrub. (This reverses H-89's "left alone" for those parts;
    its test in tests_sentry_scrubbing.py is changed with this row.)

HOW THE SETTINGS ARE TESTED WITHOUT A NETWORK
---------------------------------------------
Sentry is initialised only where a DSN is set, which is never under the
test runner. So the init call's keywords are read from the settings'
source, and the SDK's own functions are then called with the SDK's default
options overlaid with those keywords: what the tests see is what the SDK
would do with OUR call. Each such test has a control with the bare
defaults, which must show the leak, so that it cannot pass on an SDK that
no longer behaves as read.

LIMITS, STATED
--------------
  * A value logged in another form (`password: x`, a dict's
    `'password': 'x'`, a bare value) is not recognised.
  * After `password=` everything to the end of that LINE is replaced: a
    value may hold a comma, a bracket or a space, so nothing shorter is
    safe. What follows the value on its line is lost with it.
  * A name or free text in a query string is recognised by no pattern.
  * Nothing here observes Sentry, a server, or an event that was sent.

A made value has three pieces, cut by a comma, a bracket and a space. The
tests look for each PIECE, not only for the whole value: a pattern that
stopped early and left a tail in print fails every test in which a made
value follows a name (Senior Manager, 2026-10-07, before any run).

Run with:
    python manage.py test AutoGrader.tests_sentry_sends_no_variables
"""

import ast
import importlib
import logging
import os
import re
import secrets
import sys
from types import SimpleNamespace
from typing import Any

import redis
from django.conf import settings
from django.test import SimpleTestCase, override_settings
from sentry_sdk.consts import DEFAULT_OPTIONS
from sentry_sdk.integrations._wsgi_common import request_body_within_bounds
from sentry_sdk.utils import event_from_exception

REPLACED = "[secret]"
ADDRESS = "someone.private@school-example.edu"


def made_value():
    """A value made at run time, so that no line of this file (which an
    event's source context quotes) and no log of a failing test holds it
    before the test builds it. It has a comma, a bracket and a space in it:
    the characters a shorter pattern would stop at."""
    return (
        f"Zq{secrets.token_hex(4)},Wv{secrets.token_hex(4)}) Yk{secrets.token_hex(4)}"
    )


def pieces_of(value):
    """A made value cut at its comma, bracket and space. A pattern that
    stopped at one of them would leave the later pieces in print, and a
    test that only looked for the WHOLE value would not see them."""
    return [piece for piece in re.split(r"[,) ]+", value) if piece]


def scrubbed_form_of(pool):
    """What must remain of a pool's printed form: its own text up to and
    with `password=`, then the mark. Computed from the pool, so that it
    does not depend on which keywords the library prints, or in what
    order."""
    before, name, _value_and_rest = repr(pool).partition("password=")
    assert name, "the pool's printed form has no password= in it"
    return before + name + REPLACED


def assert_nothing_of(test, value, text):
    """No piece of `value` is in `text` (and so not the whole of it)."""
    pieces = pieces_of(value)
    test.assertTrue(pieces)
    for piece in pieces:
        test.assertGreaterEqual(len(piece), 10)
        test.assertNotIn(piece, text)


def queue_url(password):
    """A queue's URL with `password` in it. Built from parts: no line of
    this file holds a URL with a password part, not even a made one."""
    return "redis://:" + password + "@queue.invalid:6379/0"


def init_keywords():
    """The keywords of the one `sentry_sdk.init(...)` call in settings.py,
    as source text."""
    with open(os.path.join(settings.BASE_DIR, "AutoGrader", "settings.py")) as fh:
        tree = ast.parse(fh.read())
    [init] = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and ast.unparse(node.func) == "sentry_sdk.init"
    ]
    return {keyword.arg: keyword.value for keyword in init.keywords}


def options_as_our_call_sets_them():
    """The SDK's default options, overlaid with every keyword of our init
    call whose value is a plain literal (True, False, a string, a number)."""
    options = dict(DEFAULT_OPTIONS)
    for name, value in init_keywords().items():
        try:
            options[name] = ast.literal_eval(value)
        except ValueError:
            pass  # a name or a call (the DSN, the hooks): not needed here
    return options


def event_of_a_failure(options, held):
    """The event the SDK builds, with `options`, for an exception raised in
    a frame whose local variable is `held`."""

    def fails(a_local_variable):
        raise RuntimeError("made to fail")

    try:
        fails(held)
    except RuntimeError:
        event, _hint = event_from_exception(sys.exc_info(), client_options=options)
    return event


def frames_of(event):
    return [
        frame
        for value in event["exception"]["values"]
        for frame in value["stacktrace"]["frames"]
    ]


class TheInitCallTests(SimpleTestCase):
    def test_it_turns_frame_variables_off(self):
        keywords = init_keywords()

        self.assertIn("include_local_variables", keywords)
        self.assertIs(ast.literal_eval(keywords["include_local_variables"]), False)

    def test_it_sends_no_request_body(self):
        keywords = init_keywords()

        self.assertIn("max_request_body_size", keywords)
        self.assertEqual(ast.literal_eval(keywords["max_request_body_size"]), "never")

    def test_it_still_sends_no_default_pii(self):
        """Held by H-89's wiring test as well; here because the two new
        settings do not replace it (cookies, the Authorization header, the
        user and a task's arguments hang on it)."""
        self.assertIs(ast.literal_eval(init_keywords()["send_default_pii"]), False)


class WhatTheSdkDoesWithOurCallTests(SimpleTestCase):
    def test_control_with_the_bare_defaults_a_frames_variable_is_in_the_event(self):
        """The leak, shown: this is what was sent."""
        held = made_value()

        event = event_of_a_failure(dict(DEFAULT_OPTIONS), held)

        with_variables = [frame for frame in frames_of(event) if "vars" in frame]
        self.assertTrue(with_variables)
        self.assertIn(held, str(with_variables))

    def test_with_our_call_no_frame_has_variables(self):
        held = made_value()

        event = event_of_a_failure(options_as_our_call_sets_them(), held)

        frames = frames_of(event)
        self.assertTrue(frames)
        self.assertEqual([frame for frame in frames if "vars" in frame], [])
        self.assertNotIn(held, str(event))
        # What makes the report useful is still there.
        self.assertEqual(event["exception"]["values"][0]["type"], "RuntimeError")
        self.assertIn("fails", [frame["function"] for frame in frames])

    def test_control_with_the_bare_defaults_a_small_body_is_within_bounds(self):
        client: Any = SimpleNamespace(options=dict(DEFAULT_OPTIONS))

        self.assertTrue(request_body_within_bounds(client, 10))

    def test_with_our_call_no_body_is_within_bounds(self):
        client: Any = SimpleNamespace(options=options_as_our_call_sets_them())

        for length in (0, 1, 10, 10**3, 10**4, 10**6):
            with self.subTest(length=length):
                self.assertFalse(request_body_within_bounds(client, length))


class APoolsPrintedFormTests(SimpleTestCase):
    """The instance the row was opened for. A real pool, built with a made
    password; building a pool opens no connection."""

    def setUp(self):
        self.hooks = importlib.import_module("AutoGrader.sentry_scrubbing")
        self.password = made_value()
        self.pool = redis.ConnectionPool(
            host="queue.invalid", port=6379, db=0, password=self.password
        )

    def test_guard_the_library_still_prints_the_password(self):
        """If this fails the library has changed how a pool prints itself,
        and the tests below no longer prove anything: read it again."""
        self.assertIn(f"password={self.password}", repr(self.pool))
        self.assertIn(
            f"password={self.password}", repr(redis.Redis(connection_pool=self.pool))
        )

    def test_as_a_frames_variable_text_it_is_replaced(self):
        """The second defence: an event that reaches the hook with a pool's
        text in it all the same (another SDK version, another road)."""
        event = {
            "exception": {
                "values": [
                    {
                        "type": "ConnectionError",
                        "value": "Error 111 connecting to queue.invalid:6379.",
                        "stacktrace": {
                            "frames": [
                                {
                                    "function": "_execute_command",
                                    "vars": {
                                        "pool": repr(self.pool),
                                        "self": repr(
                                            redis.Redis(connection_pool=self.pool)
                                        ),
                                        "command_name": "'PING'",
                                    },
                                }
                            ]
                        },
                    }
                ]
            }
        }

        scrubbed = self.hooks.scrub_event(event, {})

        variables = scrubbed["exception"]["values"][0]["stacktrace"]["frames"][0][
            "vars"
        ]
        assert_nothing_of(self, self.password, str(scrubbed))
        self.assertIn(f"password={REPLACED}", variables["pool"])
        # Exactly what remains: the pool's own text up to the name, then
        # the mark; the rest of that line went with the value.
        self.assertEqual(variables["pool"], scrubbed_form_of(self.pool))
        self.assertIn(f"password={REPLACED}", variables["self"])
        self.assertIn("host=queue.invalid", variables["pool"])
        self.assertEqual(variables["command_name"], "'PING'")

    def test_in_an_exceptions_text_and_a_message_it_is_replaced(self):
        event = {
            "message": f"could not use {self.pool!r}",
            "exception": {
                "values": [{"type": "X", "value": f"bad pool {self.pool!r}"}]
            },
            "extra": {"pool": repr(self.pool)},
            "breadcrumbs": {"values": [{"message": f"using {self.pool!r}"}]},
        }

        scrubbed = self.hooks.scrub_event(event, {})

        assert_nothing_of(self, self.password, str(scrubbed))
        self.assertEqual(str(scrubbed).count(f"password={REPLACED}"), 4)

    def test_in_a_breadcrumb_and_a_log_item_it_is_replaced(self):
        crumb = self.hooks.scrub_breadcrumb(
            {"message": f"using {self.pool!r}", "data": {"pool": repr(self.pool)}}, {}
        )
        log = self.hooks.scrub_log(
            {
                "body": f"using {self.pool!r}",
                "attributes": {"sentry.message.parameter.0": repr(self.pool)},
            },
            {},
        )

        for item in (crumb, log):
            with self.subTest(item=sorted(item)):
                assert_nothing_of(self, self.password, str(item))
                self.assertEqual(str(item).count(f"password={REPLACED}"), 2)


class TheNamedValuePatternTests(SimpleTestCase):
    def setUp(self):
        self.scrub = importlib.import_module("AutoGrader.log_scrubbing").scrub

    def test_each_of_the_four_names(self):
        for name in ("password", "passwd", "secret", "token"):
            value = made_value()
            with self.subTest(name=name):
                self.assertEqual(
                    self.scrub(f"connect(host=h,{name}={value}"),
                    f"connect(host=h,{name}={REPLACED}",
                )

    def test_a_text_with_no_at_sign_is_scrubbed_too(self):
        """scrub() returned a text with no "@" as it was, without running a
        pattern. A pool's printed form has none."""
        value = made_value().replace("@", "")
        text = f"pool(password={value}"

        self.assertNotIn("@", text)
        self.assertEqual(self.scrub(text), f"pool(password={REPLACED}")

    def test_the_names_case_and_a_longer_name_that_ends_in_one(self):
        for name in (
            "PASSWORD",
            "Token",
            "new_password",
            "activation_token",
            "client-secret",
        ):
            value = made_value()
            with self.subTest(name=name):
                scrubbed = self.scrub(f"{name}={value}")
                self.assertNotIn(value, scrubbed)
                self.assertEqual(scrubbed, f"{name}={REPLACED}")

    def test_the_value_is_taken_to_the_end_of_its_line_and_no_further(self):
        value = made_value()

        scrubbed = self.scrub(f"first line\nopts: token={value}, retries=3\nlast line")

        self.assertEqual(scrubbed, f"first line\nopts: token={REPLACED}\nlast line")

    def test_two_lines_each_with_one(self):
        first, second = made_value(), made_value()

        scrubbed = self.scrub(f"a password={first}\nb secret={second}")

        self.assertEqual(scrubbed, f"a password={REPLACED}\nb secret={REPLACED}")

    def test_an_address_and_a_url_password_are_still_replaced_beside_it(self):
        value, url_password = made_value(), secrets.token_hex(6)
        text = f"for {ADDRESS} at {queue_url(url_password)}\n" f"pool(password={value}"

        scrubbed = self.scrub(text)

        self.assertEqual(
            scrubbed,
            "for [email] at redis://[credentials]@queue.invalid:6379/0\n"
            f"pool(password={REPLACED}",
        )

    def test_text_that_only_looks_alike_is_unchanged(self):
        for text in (
            "the password was wrong",
            "token count: 3",
            "tokens=3 max_tokens=4096",
            "secrets=2",
            "password = unset",
            "password=",
            "retries=3, timeout=5",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.scrub(text), text)


@override_settings(LOG_SCRUB_ADDRESSES=True)
class WhatALogLinePrintsTests(SimpleTestCase):
    """The printed log goes through the record factory, not the hooks."""

    def setUp(self):
        self.assertTrue(
            importlib.import_module("AutoGrader.log_scrubbing").is_installed()
        )
        self.value = made_value()
        self.pool = redis.ConnectionPool(host="queue.invalid", password=self.value)

    def record(self, *args, **kwargs):
        with self.assertLogs("h167.probe", level="ERROR") as logs:
            logging.getLogger("h167.probe").error(*args, **kwargs)
        [record] = logs.records
        return record

    def test_a_message_built_with_a_pool(self):
        record = self.record("Could not use %r", self.pool)

        assert_nothing_of(self, self.value, record.getMessage())
        self.assertEqual(
            record.getMessage(), "Could not use " + scrubbed_form_of(self.pool)
        )

    def test_an_exceptions_text(self):
        try:
            raise RuntimeError(f"bad pool {self.pool!r}")
        except RuntimeError:
            record = self.record("dispatch failed", exc_info=True)

        assert_nothing_of(self, self.value, record.exc_text)
        self.assertIn(f"password={REPLACED}", record.exc_text)
        self.assertIn("RuntimeError", record.exc_text)


class TheRestOfTheEventTests(SimpleTestCase):
    """`request`, `tags`, `user` and `contexts` are scrubbed like the other
    parts: an address, a URL's password, a named value."""

    def setUp(self):
        self.hooks = importlib.import_module("AutoGrader.sentry_scrubbing")
        self.value = made_value()
        self.url_password = secrets.token_hex(6)

    def event(self):
        return {
            "request": {
                "url": f"https://api.example.com/users/?email={ADDRESS}",
                "method": "POST",
                "query_string": f"email={ADDRESS}&token={self.value}",
                "headers": {"Referer": f"https://app.example.com/?secret={self.value}"},
                "data": {
                    "email": ADDRESS,
                    "note": f"password={self.value}",
                    "count": 2,
                },
                "env": {"SERVER_NAME": "api.example.com"},
            },
            "tags": {"school": "77", "owner": ADDRESS},
            "user": {"id": "4821", "email": ADDRESS},
            "contexts": {
                "queue": {"url": queue_url(self.url_password)},
                "runtime": {"name": "CPython", "version": "3.12.3"},
            },
        }

    def test_none_of_the_made_values_is_left(self):
        scrubbed = self.hooks.scrub_event(self.event(), {})

        text = str(scrubbed)
        for value in (ADDRESS, self.value, self.url_password):
            assert_nothing_of(self, value, text)

    def test_each_part_by_itself(self):
        """One part at a time, so that each part's own line is needed."""
        for part, held in (
            ("request", (ADDRESS, self.value)),
            ("tags", (ADDRESS,)),
            ("user", (ADDRESS,)),
            ("contexts", (self.url_password,)),
        ):
            with self.subTest(part=part):
                event = {part: self.event()[part]}
                for value in held:
                    self.assertIn(value, str(event))  # the deciding value is there

                scrubbed = self.hooks.scrub_event(event, {})

                for value in held:
                    assert_nothing_of(self, value, str(scrubbed[part]))

    def test_what_holds_none_of_them_is_kept(self):
        scrubbed = self.hooks.scrub_event(self.event(), {})

        self.assertEqual(scrubbed["request"]["method"], "POST")
        self.assertEqual(scrubbed["request"]["env"], {"SERVER_NAME": "api.example.com"})
        self.assertEqual(scrubbed["request"]["data"]["count"], 2)
        self.assertEqual(
            scrubbed["request"]["url"], "https://api.example.com/users/?email=[email]"
        )
        self.assertEqual(scrubbed["tags"]["school"], "77")
        self.assertEqual(scrubbed["user"]["id"], "4821")
        self.assertEqual(
            scrubbed["contexts"]["runtime"], {"name": "CPython", "version": "3.12.3"}
        )
        self.assertEqual(
            scrubbed["contexts"]["queue"]["url"],
            "redis://[credentials]@queue.invalid:6379/0",
        )
