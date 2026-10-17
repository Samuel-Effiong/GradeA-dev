"""H-19 against the REAL AI provider (Gate 7).

Opt-in: set RUN_REAL_AI=1. It costs money and needs network, so it is
skipped by default - the same pattern assignments/tests_real_extraction.py
uses.

The stubbed tests prove the branch logic. This proves the one thing a stub
cannot: after the both-flags change, a true superadmin's call still goes out
through the unmetered branch to the real provider, returns a real completion,
and writes no billing rows - while a type-only account is refused before any
network call. The spy wraps (does not replace) the provider call, so the
model, token usage and response are the provider's own; they are printed for
the evidence record.
"""

import os
import unittest
from unittest.mock import patch

from ai_processor.services import AIProcessor, ai_processor
from ai_processor.tests_superadmin_unmetered_both_flags import (
    PROVIDER,
    SuperadminShapesFixture,
)
from assignments.tests_security import enroll, make_student
from billing.access_control import AIFeatureNotAvailableError

RUN_REAL_AI = os.environ.get("RUN_REAL_AI") == "1"


@unittest.skipUnless(
    RUN_REAL_AI, "Real AI call is opt-in and billed: set RUN_REAL_AI=1"
)
class RealProviderSuperadminBothFlagsTest(SuperadminShapesFixture):
    def setUp(self):
        super().setUp()
        self.student = make_student("h19-real-student@example.com")
        enroll(self.student, self.promoted_course)

    def test_true_superadmin_real_call_is_unmetered_and_type_only_never_calls(self):
        real_provider = getattr(AIProcessor, PROVIDER)
        seen = []

        def spy(self_, *args, **kwargs):
            response = real_provider(self_, *args, **kwargs)
            seen.append(response)
            return response

        with patch.object(AIProcessor, PROVIDER, autospec=True, side_effect=spy):
            summary = ai_processor.generate_student_summary(
                self.superadmin, self.student, self.promoted_course
            )
            self.assertEqual(len(seen), 1, "exactly one real provider call")

            for user in self.type_only:
                with self.assertRaises(AIFeatureNotAvailableError):
                    ai_processor.generate_student_summary(
                        user, self.student, self.promoted_course
                    )
            self.assertEqual(len(seen), 1, "a type-only account reached the provider")

        response = seen[0]
        usage = getattr(response, "usage", None)
        self.assertTrue(summary)
        self.assertEqual(self.billing_rows(self.superadmin), (0, 0))
        for user in self.type_only:
            self.assertEqual(self.billing_rows(user), (0, 0))
        print(
            "\n[H19 real provider] "
            f"model={getattr(response, 'model', None)!r} "
            f"id={getattr(response, 'id', None)!r} "
            f"finish_reason={response.choices[0].finish_reason!r} "
            f"prompt_tokens={getattr(usage, 'prompt_tokens', None)} "
            f"completion_tokens={getattr(usage, 'completion_tokens', None)} "
            f"total_tokens={getattr(usage, 'total_tokens', None)} "
            f"summary_chars={len(summary)} billing_rows_superadmin=(0, 0)"
        )
