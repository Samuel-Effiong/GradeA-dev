from django.test import SimpleTestCase

from AutoGrader.sentry_scrubbing import scrub_pii_before_send


class ScrubPiiBeforeSendTests(SimpleTestCase):
    def test_scrubs_email_from_logentry_message(self):
        event = {
            "logentry": {
                "message": "MailerLite sync failed for %s",
                "formatted": "MailerLite sync failed for student@example.com",
                "params": ["student@example.com"],
            }
        }

        result = scrub_pii_before_send(event, {})

        self.assertNotIn("student@example.com", result["logentry"]["formatted"])
        self.assertNotIn("student@example.com", result["logentry"]["params"][0])
        self.assertIn("[redacted-email]", result["logentry"]["formatted"])

    def test_scrubs_email_from_exception_value_and_frame_vars(self):
        event = {
            "exception": {
                "values": [
                    {
                        "value": "Provider rejected request for teacher@example.org",
                        "stacktrace": {
                            "frames": [
                                {
                                    "vars": {
                                        "recipient": "teacher@example.org",
                                        "count": "3",
                                    }
                                }
                            ]
                        },
                    }
                ]
            }
        }

        result = scrub_pii_before_send(event, {})

        exc_value = result["exception"]["values"][0]
        self.assertNotIn("teacher@example.org", exc_value["value"])
        frame_vars = exc_value["stacktrace"]["frames"][0]["vars"]
        self.assertNotIn("teacher@example.org", frame_vars["recipient"])
        # Non-PII values are left untouched.
        self.assertEqual(frame_vars["count"], "3")

    def test_event_with_no_pii_is_unchanged(self):
        event = {
            "logentry": {
                "message": "Grading batch completed",
                "formatted": "Grading batch completed",
            },
            "exception": {"values": [{"value": "ValueError: bad input"}]},
        }

        result = scrub_pii_before_send(event, {})

        self.assertEqual(result["logentry"]["formatted"], "Grading batch completed")
        self.assertEqual(
            result["exception"]["values"][0]["value"], "ValueError: bad input"
        )

    def test_malformed_event_does_not_raise(self):
        # exception.values is a string, not a list - forces a TypeError path
        # in _scrub_exception. The hook must never raise: a bug here must
        # not block a legitimate error report from reaching Sentry.
        event = {"exception": {"values": "not-a-list"}}

        result = scrub_pii_before_send(event, {})

        self.assertIs(result, event)

    def test_missing_keys_are_handled(self):
        event = {}

        result = scrub_pii_before_send(event, {})

        self.assertEqual(result, {})
