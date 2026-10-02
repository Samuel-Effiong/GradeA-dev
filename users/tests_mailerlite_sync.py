"""
MailerLite sync: the service itself, and the Celery task around it.

billing/tests/test_mailerlite_sync.py covers the CALL SITES - which billing
operations trigger a sync, and that `queue_sync` skips inactive users - but
it patches the task's `.delay`, so none of it ever runs
`MailerLiteService.sync_user`, `_build_payload`, `_group_id_for`, or the
task body. Those were entirely unexercised.

What matters here is the three-way return contract, because the Celery task
branches on it:

    True  -> synced
    False -> request failed, SAFE TO RETRY
    None  -> skipped (no API key configured), NOT worth retrying

Getting None and False confused either retries forever on a deployment with
no API key, or silently drops real failures.
"""

from unittest.mock import MagicMock, patch

import requests
from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from kombu.exceptions import OperationalError

from users.mailerlite_service import (
    MAILERLITE_API_URL,
    REQUEST_TIMEOUT_SECONDS,
    MailerLiteService,
    queue_sync,
)
from users.models import UserTypes
from users.tasks import sync_user_to_mailerlite

User = get_user_model()


def make_user(**overrides):
    defaults = {
        "email": "mailerlite.user@gmail.com",
        "password": "password123",  # pragma: allowlist secret
        "first_name": "Mailer",
        "last_name": "Lite",
        "user_type": UserTypes.TEACHER,
        "is_active": True,
    }
    defaults.update(overrides)
    return User.objects.create_user(**defaults)


@override_settings(
    MAILERLITE_API_KEY="test-key",  # pragma: allowlist secret
    MAILERLITE_GROUP_ID_TEACHER="group-teacher",
    MAILERLITE_GROUP_ID_STUDENT="group-student",
    MAILERLITE_GROUP_ID_SCHOOL_ADMIN="group-admin",
)
class MailerLiteServiceTests(TestCase):
    def setUp(self):
        self.user = make_user()

    def _ok_response(self):
        response = MagicMock()
        response.raise_for_status.return_value = None
        return response

    def test_returns_true_on_success(self):
        with patch("users.mailerlite_service.requests.post") as mock_post:
            mock_post.return_value = self._ok_response()

            self.assertIs(MailerLiteService.sync_user(self.user), True)

    def test_returns_false_on_request_failure_so_the_task_can_retry(self):
        with patch("users.mailerlite_service.requests.post") as mock_post:
            mock_post.side_effect = requests.RequestException("connection reset")

            self.assertIs(MailerLiteService.sync_user(self.user), False)

    def test_returns_false_on_a_non_2xx_response(self):
        response = MagicMock()
        response.raise_for_status.side_effect = requests.HTTPError("500 Server Error")

        with patch("users.mailerlite_service.requests.post", return_value=response):
            self.assertIs(MailerLiteService.sync_user(self.user), False)

    @override_settings(MAILERLITE_API_KEY="")
    def test_returns_none_and_makes_no_call_without_an_api_key(self):
        """None, not False - a missing key is not a retryable failure."""
        with patch("users.mailerlite_service.requests.post") as mock_post:
            self.assertIsNone(MailerLiteService.sync_user(self.user))

        mock_post.assert_not_called()

    def test_request_carries_a_timeout(self):
        """
        No timeout means a hung MailerLite pins a Celery worker forever.
        """
        with patch("users.mailerlite_service.requests.post") as mock_post:
            mock_post.return_value = self._ok_response()
            MailerLiteService.sync_user(self.user)

        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["timeout"], REQUEST_TIMEOUT_SECONDS)

    def test_request_targets_the_api_with_a_bearer_token(self):
        with patch("users.mailerlite_service.requests.post") as mock_post:
            mock_post.return_value = self._ok_response()
            MailerLiteService.sync_user(self.user)

        args, kwargs = mock_post.call_args
        self.assertEqual(args[0], MAILERLITE_API_URL)
        self.assertEqual(kwargs["headers"]["Authorization"], "Bearer test-key")

    def test_each_user_type_is_sent_to_its_own_group(self):
        cases = [
            (UserTypes.TEACHER, "group-teacher"),
            (UserTypes.STUDENT, "group-student"),
            (UserTypes.SCHOOL_ADMIN, "group-admin"),
        ]
        for index, (user_type, expected_group) in enumerate(cases):
            with self.subTest(user_type=user_type):
                user = make_user(email=f"grouped{index}@gmail.com", user_type=user_type)

                with patch("users.mailerlite_service.requests.post") as mock_post:
                    mock_post.return_value = self._ok_response()
                    MailerLiteService.sync_user(user)

                payload = mock_post.call_args.kwargs["json"]
                self.assertEqual(payload["groups"], [expected_group])
                self.assertEqual(payload["email"], user.email)

    def test_super_admin_has_no_group_and_the_key_is_omitted(self):
        """
        SUPER_ADMIN is absent from GROUP_ID_BY_USER_TYPE. Sending
        `groups: [None]` would be a malformed request, so the key must be
        left out entirely.
        """
        admin = make_user(
            email="super.mailerlite@example.com", user_type=UserTypes.SUPER_ADMIN
        )

        with patch("users.mailerlite_service.requests.post") as mock_post:
            mock_post.return_value = self._ok_response()
            MailerLiteService.sync_user(admin)

        self.assertNotIn("groups", mock_post.call_args.kwargs["json"])

    @override_settings(MAILERLITE_GROUP_ID_TEACHER="")
    def test_an_unconfigured_group_id_is_omitted_rather_than_sent_empty(self):
        with patch("users.mailerlite_service.requests.post") as mock_post:
            mock_post.return_value = self._ok_response()
            MailerLiteService.sync_user(self.user)

        self.assertNotIn("groups", mock_post.call_args.kwargs["json"])

    def test_payload_carries_the_subscription_fields(self):
        with patch("users.mailerlite_service.requests.post") as mock_post:
            mock_post.return_value = self._ok_response()
            MailerLiteService.sync_user(self.user)

        fields = mock_post.call_args.kwargs["json"]["fields"]
        self.assertEqual(fields["name"], self.user.first_name)
        self.assertEqual(fields["last_name"], self.user.last_name)
        # No subscription on this fixture: the contract is empty strings and
        # the literal "false", never None (MailerLite rejects nulls here).
        self.assertEqual(fields["subscription_type"], "")
        self.assertEqual(fields["subscription_tier"], "")
        self.assertEqual(fields["subscription_active"], "false")


class SyncUserToMailerliteTaskTests(TestCase):
    """The Celery wrapper's retry contract."""

    def setUp(self):
        self.user = make_user(email="task.mailerlite@gmail.com")

    @patch("users.mailerlite_service.MailerLiteService.sync_user")
    def test_a_failed_sync_is_retried(self, mock_sync):
        # False = retryable. Fail once, then succeed.
        mock_sync.side_effect = [False, True]

        sync_user_to_mailerlite.apply(args=(str(self.user.id),))

        self.assertEqual(mock_sync.call_count, 2)

    @patch("users.mailerlite_service.MailerLiteService.sync_user")
    def test_retries_stop_at_max_retries(self, mock_sync):
        """A permanently failing sync must not retry forever."""
        mock_sync.return_value = False

        sync_user_to_mailerlite.apply(args=(str(self.user.id),))

        # Initial attempt + max_retries=3.
        self.assertEqual(mock_sync.call_count, 4)

    @patch("users.mailerlite_service.MailerLiteService.sync_user")
    def test_a_skipped_sync_is_not_retried(self, mock_sync):
        """
        None = "no API key configured". Retrying that would mean every
        deployment without a key burns its whole retry budget on every
        signup, for a call that cannot succeed.
        """
        mock_sync.return_value = None

        sync_user_to_mailerlite.apply(args=(str(self.user.id),))

        self.assertEqual(mock_sync.call_count, 1)

    @patch("users.mailerlite_service.MailerLiteService.sync_user")
    def test_a_successful_sync_runs_once(self, mock_sync):
        mock_sync.return_value = True

        sync_user_to_mailerlite.apply(args=(str(self.user.id),))

        self.assertEqual(mock_sync.call_count, 1)

    @patch("users.mailerlite_service.MailerLiteService.sync_user")
    def test_a_deleted_user_is_logged_and_dropped_not_retried(self, mock_sync):
        """
        The task is dispatched by id, so the row can be gone by the time a
        worker picks it up. That is terminal, not retryable.
        """
        missing_id = str(self.user.id)
        self.user.delete()

        with self.assertLogs("users.tasks", level="WARNING"):
            result = sync_user_to_mailerlite.apply(args=(missing_id,))

        self.assertTrue(result.successful())
        mock_sync.assert_not_called()


class QueueSyncBrokerOutageTests(TestCase):
    """
    queue_sync dispatches through AutoGrader.dispatch.safe_delay, so a
    broker outage must not break the signup/activation that triggered it.
    """

    def setUp(self):
        self.user = make_user(email="queue.mailerlite@gmail.com")

    def test_broker_outage_does_not_propagate_to_the_caller(self):
        with patch("users.tasks.sync_user_to_mailerlite.delay") as mock_delay:
            mock_delay.side_effect = OperationalError("broker unavailable")

            with self.assertLogs("AutoGrader.dispatch", level="ERROR"):
                queue_sync(self.user)  # must not raise

    def test_an_inactive_user_is_never_queued(self):
        """Syncing a signup that never verified would leak it to marketing."""
        self.user.is_active = False
        self.user.save(update_fields=["is_active"])

        with patch("users.tasks.sync_user_to_mailerlite.delay") as mock_delay:
            queue_sync(self.user)

        mock_delay.assert_not_called()

    def test_an_active_user_is_queued(self):
        with patch("users.tasks.sync_user_to_mailerlite.delay") as mock_delay:
            queue_sync(self.user)

        mock_delay.assert_called_once_with(str(self.user.id))
