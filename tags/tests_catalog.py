"""Epic C1: the shared registries (plan 11 §6 item 1, D6; SM rulings 1-3).

* The five owner-approved reason codes, word for word, with no
  remediation text (SM ruling 3: each message already says what to do).
* The audit allow-lists: ids, counts and labels only, never a tag name
  (audit/metadata.py's rule: no text a person wrote).
* S2: while dark, the tag write routes answer an anonymous caller with a
  bare 404, so they are CONCEALED_ROUTES in the route-coverage sweep.
"""

from django.test import SimpleTestCase
from rest_framework import status

from audit.enums import AuditAction, ErrorClass, ReasonCode
from audit.metadata import ALLOWED_KEYS, BEFORE_AFTER_ALLOWLIST, METADATA_ALLOWLIST
from audit.tests_route_coverage import CONCEALED_ROUTES
from AutoGrader.reason_codes import REASON_CODES


def tags_set():
    # Looked up when a test runs, not at import: on the base this action
    # does not exist yet, and an import-time error would stop the whole run
    # instead of failing these tests (red first).
    return AuditAction("ASSIGNMENT_TAGS_SET")


APPROVED = {
    "TAG_NAME_TAKEN": (
        'You already have a {tag_type} tag named "{name}".',
        status.HTTP_400_BAD_REQUEST,
        False,
        {"tag_type", "name"},
    ),
    "TAG_CHANGED_SINCE_CHECK": (
        "This tag's assignments changed since you checked. Review the new "
        "count and try again.",
        status.HTTP_409_CONFLICT,
        True,
        set(),
    ),
    "TAG_NAME_MISSING": (
        "Enter a tag name.",
        status.HTTP_400_BAD_REQUEST,
        False,
        set(),
    ),
    "TAG_NAME_TOO_LONG": (
        "Tag names can be up to 100 characters.",
        status.HTTP_400_BAD_REQUEST,
        False,
        set(),
    ),
    "TOO_MANY_TAGS": (
        "An assignment can have up to 50 tags.",
        status.HTTP_400_BAD_REQUEST,
        False,
        set(),
    ),
}


class ReasonCodeTest(SimpleTestCase):
    def test_the_five_approved_codes(self):
        for code, (message, http_status, retryable, params) in APPROVED.items():
            with self.subTest(code=code):
                spec = REASON_CODES[ReasonCode(code)]
                self.assertEqual(spec.message, message)
                self.assertEqual(spec.http_status, http_status)
                self.assertEqual(spec.error_class, ErrorClass.VALIDATION)
                self.assertEqual(spec.remediation, "")
                self.assertIs(spec.retryable, retryable)
                self.assertEqual(set(spec.params), params)
                self.assertFalse(spec.alternative_messages)


class AuditRegistryTest(SimpleTestCase):
    def test_the_assignment_tags_action(self):
        self.assertEqual(tags_set().value, "ASSIGNMENT_TAGS_SET")
        self.assertEqual(tags_set().label, "Assignment tags changed")

    def test_metadata_allow_lists(self):
        self.assertEqual(METADATA_ALLOWLIST[AuditAction.TAG_CREATE], {"tag_type"})
        self.assertEqual(METADATA_ALLOWLIST[AuditAction.TAG_RENAME], {"tag_type"})
        self.assertEqual(
            METADATA_ALLOWLIST[AuditAction.TAG_DELETE], {"tag_type", "affected_count"}
        )
        self.assertEqual(METADATA_ALLOWLIST[tags_set()], {"course_id"})
        self.assertEqual(BEFORE_AFTER_ALLOWLIST[tags_set()], {"tag_ids"})
        self.assertTrue({"tag_type", "affected_count", "tag_ids"} <= ALLOWED_KEYS)

    def test_no_tag_key_carries_a_name(self):
        for action in (
            AuditAction.TAG_CREATE,
            AuditAction.TAG_RENAME,
            AuditAction.TAG_DELETE,
            tags_set(),
        ):
            keys = METADATA_ALLOWLIST[action] | BEFORE_AFTER_ALLOWLIST.get(
                action, frozenset()
            )
            for key in keys:
                with self.subTest(action=action, key=key):
                    self.assertNotIn("name", key)


class RouteCoverageRegistryTest(SimpleTestCase):
    def test_the_dark_write_routes_are_concealed(self):
        self.assertTrue(
            {"tag-list", "tag-detail", "assignment-tags"} <= CONCEALED_ROUTES
        )
