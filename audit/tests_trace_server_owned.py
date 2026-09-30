"""S5 part 0 (X-5): the trace id is always the server's.

Before: `RequestIDMiddleware` adopted a well-formed inbound `X-Request-ID` as
the request id, and `audit.emitter._resolve_trace_id` uses that id as the
audit trace id. So a client sending another action's trace id as its
`X-Request-ID` placed its own events in that action's trail - what X-5 set
out to prevent. The unit test for it (`audit.tests_emitter.TraceIdTest`)
called the emitter directly, skipping the middleware, so it never saw this.

Now the server always mints the id. A client's UUID is kept only as
`client_request_id` / `client_correlation_id`, beside it. Every request here
goes through the real middleware stack.
"""

import logging
import uuid
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APITestCase

from audit.emitter import emit as real_emit
from audit.enums import AuditAction
from audit.models import AuditEvent
from AutoGrader.request_context import REQUEST_ID_HEADER, RequestIDLogFilter
from users.models import UserTypes

LOCMEM_CACHE = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}}


@override_settings(CACHES=LOCMEM_CACHE)
class TraceIdIsServerOwnedTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = get_user_model().objects.create_user(
            email="trace.owner@example.com",
            password="Correct-horse-1",  # pragma: allowlist secret
            first_name="Trace",
            last_name="Owner",
            user_type=UserTypes.TEACHER,
            is_active=True,
            email_verified_at=timezone.now(),
        )

    def failed_login(self, **headers):
        """One AUTH_LOGIN failure event, through the whole stack."""
        response = self.client.post(
            reverse("login"),
            {"email": self.user.email, "password": "wrong"},  # pragma: allowlist secret
            **headers,
        )
        event = AuditEvent.objects.filter(action=AuditAction.AUTH_LOGIN).latest(
            "occurred_at"
        )
        return response, event

    def test_a_client_cannot_join_another_actions_trail(self):
        _, victim = self.failed_login()

        response, event = self.failed_login(HTTP_X_REQUEST_ID=victim.trace_id.hex)

        self.assertNotEqual(event.trace_id, victim.trace_id)
        self.assertEqual(event.client_correlation_id, str(victim.trace_id))
        self.assertNotEqual(response[REQUEST_ID_HEADER], victim.trace_id.hex)

    def test_the_response_header_is_the_server_trace_id(self):
        response, event = self.failed_login(HTTP_X_REQUEST_ID=str(uuid.uuid4()))

        self.assertEqual(response[REQUEST_ID_HEADER], event.trace_id.hex)

    def test_an_inbound_id_that_is_not_a_uuid_is_dropped(self):
        response, event = self.failed_login(HTTP_X_REQUEST_ID="support-ticket-42")

        self.assertIsNone(event.client_correlation_id)
        self.assertEqual(response[REQUEST_ID_HEADER], event.trace_id.hex)

    def test_log_lines_carry_the_server_id_and_the_client_id_apart(self):
        """A line logged inside the request (here, by the login's own audit
        call) carries the server id as request_id and the client's UUID
        apart, as client_request_id."""
        client_id = str(uuid.uuid4())
        records = []

        class Capture(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = Capture()
        handler.addFilter(RequestIDLogFilter())
        probe = logging.getLogger("audit.tests.probe")
        probe.addHandler(handler)
        probe.setLevel(logging.INFO)
        self.addCleanup(probe.removeHandler, handler)

        def logging_emit(*args, **kwargs):
            probe.info("inside the request")
            return real_emit(*args, **kwargs)

        with patch("users.serializers.emit", side_effect=logging_emit):
            response, event = self.failed_login(HTTP_X_REQUEST_ID=client_id)

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].request_id, event.trace_id.hex)
        self.assertEqual(records[0].client_request_id, client_id)
