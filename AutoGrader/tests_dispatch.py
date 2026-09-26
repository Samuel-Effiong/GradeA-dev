from unittest.mock import Mock

from django.test import SimpleTestCase
from kombu.exceptions import OperationalError
from redis.exceptions import ConnectionError as RedisConnectionError
from redis.exceptions import TimeoutError as RedisTimeoutError

from AutoGrader.dispatch import (
    BROKER_UNAVAILABLE_ERRORS,
    ProcessingTemporarilyUnavailable,
    safe_delay,
)


class SafeDelayTests(SimpleTestCase):
    def test_returns_async_result_on_success(self):
        task = Mock()
        task.delay.return_value = "async-result-sentinel"

        result = safe_delay(task, "arg1", kwarg="value")

        self.assertEqual(result, "async-result-sentinel")
        task.delay.assert_called_once_with("arg1", kwarg="value")

    def test_swallows_redis_connection_error(self):
        task = Mock()
        task.delay.side_effect = RedisConnectionError("connection refused")

        result = safe_delay(task)

        self.assertIsNone(result)

    def test_swallows_redis_timeout_error(self):
        task = Mock()
        task.delay.side_effect = RedisTimeoutError("timed out")

        result = safe_delay(task)

        self.assertIsNone(result)

    def test_swallows_kombu_operational_error(self):
        task = Mock()
        task.delay.side_effect = OperationalError("broker error")

        result = safe_delay(task)

        self.assertIsNone(result)

    def test_swallows_builtin_connection_error(self):
        task = Mock()
        task.delay.side_effect = ConnectionError("refused")

        result = safe_delay(task)

        self.assertIsNone(result)

    def test_swallows_builtin_timeout_error(self):
        task = Mock()
        task.delay.side_effect = TimeoutError("timed out")

        result = safe_delay(task)

        self.assertIsNone(result)

    def test_does_not_swallow_unrelated_exceptions(self):
        # A bug in how the call was built (wrong kwarg, etc.) is not a
        # broker outage and must not be hidden behind a silent None.
        task = Mock()
        task.delay.side_effect = TypeError("unexpected keyword argument")

        with self.assertRaises(TypeError):
            safe_delay(task)


class ProcessingTemporarilyUnavailableTests(SimpleTestCase):
    def test_status_code_is_503(self):
        self.assertEqual(ProcessingTemporarilyUnavailable.status_code, 503)

    def test_default_detail_is_clear_and_actionable(self):
        exc = ProcessingTemporarilyUnavailable()
        message = str(exc.detail)
        self.assertIn("temporarily unavailable", message.lower())
        self.assertIn("try again", message.lower())


class BrokerUnavailableErrorsTests(SimpleTestCase):
    def test_covers_every_layer_a_broker_outage_can_raise_through(self):
        for exc_type in (
            RedisConnectionError,
            RedisTimeoutError,
            OperationalError,
            ConnectionError,
            TimeoutError,
        ):
            with self.subTest(exc_type=exc_type.__name__):
                self.assertTrue(issubclass(exc_type, BROKER_UNAVAILABLE_ERRORS))
