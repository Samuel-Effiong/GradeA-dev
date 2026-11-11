"""
The custom AI prompt's lead block (brief 5, the owner wants it fixed on beta
2026-11-11; SM -cf rulings on checkpoint ae8c9c11a,
docs/evidence/ai-prompt-leads-beta/DESIGN.md).

custom_ai_prompt fed the AI "[]" for TOP 10 HIGH-INTENT LEADS: it sliced the
leads endpoint's paginated Response, and a bare except swallowed the error.

The rest of the prompt must not move. billing/tests/data/custom_ai_prompt_template_pin.json
holds the template the AI received on the base (origin/beta 84948e0ac),
captured BEFORE any change by running this file once with
CUSTOM_AI_PROMPT_PIN_WRITE=1 (NOTES, step P0). The lead block's content is
cut out of it (replaced by "<LEADS>"), the clock is frozen, and each user's
random UUID is replaced by their email.
"""

import ast
import importlib
import json
import os
import re
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from pathlib import Path
from unittest.mock import patch

from django.core.cache import cache
from django.db import DatabaseError, connection
from django.test.utils import CaptureQueriesContext
from django.urls import reverse
from freezegun import freeze_time
from rest_framework.test import APITestCase

from billing.models import BetaProfile
from users.models import CustomUser, UserTypes

PIN = Path(__file__).parent / "data" / "custom_ai_prompt_template_pin.json"
NOW = datetime(2026, 6, 15, 12, 0, tzinfo=dt_timezone.utc)
PW = "Leads-pin-pass-1"  # pragma: allowlist secret
LEADS_HEADER = "### TOP 10 HIGH-INTENT LEADS (For specific outreach):\n"
#: From the lead header up to the next block's header.
LEADS_BLOCK = re.compile(re.escape(LEADS_HEADER) + r"(.*?)(\n\s*### )", re.S)


class PromptFixture(APITestCase):
    """Twelve teachers with distinct scores (so the top 10 is defined), one
    non-teacher profile (never a lead), a frozen clock and a mocked AI."""

    def setUp(self):
        # The prompt's throttle (10/min) counts in the cache.
        cache.clear()
        self.addCleanup(cache.clear)
        freezer = freeze_time(NOW)
        freezer.start()
        self.addCleanup(freezer.stop)
        retry = patch(
            "billing.views.ai_processor.custom_ai_prompt_retry", return_value="ok"
        )
        self.retry = retry.start()
        self.addCleanup(retry.stop)
        self.admin = CustomUser.objects.create_user(
            email="prompt.admin@x.example",
            password=PW,
            user_type=UserTypes.SUPER_ADMIN,
            is_active=True,
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_authenticate(user=self.admin)

    def person(self, n, user_type=UserTypes.TEACHER):
        return CustomUser.objects.create_user(
            email=f"lead{n:02d}.prompt@x.example",
            password=PW,
            user_type=user_type,
            is_active=True,
            first_name=f"First{n:02d}",
            last_name=f"Last{n:02d}",
        )

    def build(self):
        people = {}
        for n in range(12):
            user = self.person(n)
            profile, _ = BetaProfile.objects.get_or_create(user=user)
            BetaProfile.objects.filter(pk=profile.pk).update(
                conversion_probability=5.0 + 7.5 * n,
                total_credits_used=1_000 * n,
                initial_beta_credits=20_000,
                has_hit_80_percent=n % 3 == 0,
                distinct_login_days=n,
                last_active_at=NOW - timedelta(days=n),
                credits_used_grading=100 * (n % 4),
                credits_used_creation=150,
            )
            people[user.email] = user
        outsider = self.person(99, UserTypes.SCHOOL_ADMIN)
        profile, _ = BetaProfile.objects.get_or_create(user=outsider)
        BetaProfile.objects.filter(pk=profile.pk).update(conversion_probability=99.9)
        people[outsider.email] = outsider
        return people

    def prompt(self, **query):
        url = reverse("analytics-custom-ai-prompt")
        if query:
            url += "?" + "&".join(f"{k}={v}" for k, v in query.items())
        response = self.client.post(url, {"prompt": "Who should we contact?"})
        return response

    def template(self, people, **query):
        response = self.prompt(**query)
        self.assertEqual(response.status_code, 200, response.content[:300])
        text = self.retry.call_args.args[1]
        for email, user in people.items():
            text = text.replace(str(user.pk), f"<id:{email}>")
        return text

    def lead_block(self, template):
        found = LEADS_BLOCK.search(template)
        self.assertIsNotNone(found, "no lead block in the template")
        assert found is not None  # for mypy
        return found.group(1).strip()

    def without_leads(self, template):
        return LEADS_BLOCK.sub(
            lambda m: LEADS_HEADER + "<LEADS>" + m.group(2), template
        )


class TemplatePinTests(PromptFixture):
    def test_the_rest_of_the_template_is_what_it_was(self):
        rest = self.without_leads(self.template(self.build()))

        if os.environ.get("CUSTOM_AI_PROMPT_PIN_WRITE") == "1":
            PIN.parent.mkdir(exist_ok=True)
            PIN.write_text(json.dumps({"template": rest}, indent=2) + "\n")
            self.skipTest(f"pin written to {PIN}")

        self.assertEqual(rest, json.loads(PIN.read_text())["template"])

    def test_the_pin_is_not_vacuous(self):
        rest = json.loads(PIN.read_text())["template"]

        self.assertIn(LEADS_HEADER + "<LEADS>", rest)
        self.assertIn("### COHORT SUMMARY:", rest)
        self.assertIn("'total_beta_users': 13", rest)
        self.assertIn("### FEATURE MIX (Value Drivers):", rest)


def expected_row(n):
    """Lead `n` of the fixture, as the leads list shows it (owner's Q-L1
    (a): the full row, name and email included)."""
    grading, creation = 100 * (n % 4), 150
    return {
        "id": f"<id:lead{n:02d}.prompt@x.example>",
        "name": f"First{n:02d} Last{n:02d}",
        "email": f"lead{n:02d}.prompt@x.example",
        "score": round(5.0 + 7.5 * n, 1),
        "metrics": {
            "credit_usage": 1_000 * n,
            "usage_percentage": round(1_000 * n / 20_000 * 100, 1),
            "login_days": n,
            "last_active": (NOW - timedelta(days=n)).date().isoformat(),
            "primary_use_case": "Grading" if grading > creation else "Creation",
        },
        "flags": {
            "at_80_percent": n % 3 == 0,
            "active_last_week": n <= 7,
            "is_power_grader": grading > creation,
            "frequent_user": n >= 8,
        },
    }


#: The top 10 by score: leads 11 down to 2 (the non-teacher is never a lead).
EXPECTED_BLOCK = repr([expected_row(n) for n in range(11, 1, -1)])


class LeadBlockTests(PromptFixture):
    def test_the_block_is_exactly_the_top_ten_leads(self):
        # Q-L4: the exact string. On the base it was "[]".
        block = self.lead_block(self.template(self.build()))

        self.assertEqual(block, EXPECTED_BLOCK)

    def test_the_block_equals_the_leads_list_first_page(self):
        # One source: the block's rows are the leads list's rows.
        people = self.build()
        block = ast.literal_eval(self.lead_block(self.template(people)))
        page = self.client.get(
            reverse("analytics-intent-signals"), {"page_size": 10}
        ).json()["data"]["results"]
        text = json.dumps(page)
        for email, user in people.items():
            text = text.replace(str(user.pk), f"<id:{email}>")

        self.assertEqual(block, json.loads(text))

    def test_query_params_do_not_change_the_block(self):
        # Q-L3: a POST's query string never reaches the leads.
        people = self.build()
        plain = self.lead_block(self.template(people))
        self.assertNotEqual(plain, "[]")  # else the comparison proves nothing
        for query in (
            {"search": "lead02"},
            {"ordering": "signals"},
            {"signals_min": "4"},
            {"page_size": "2", "page": "2"},
        ):
            with self.subTest(**query):
                self.assertEqual(self.lead_block(self.template(people, **query)), plain)


class LeadBlockFailureTests(PromptFixture):
    def test_a_database_error_is_logged_without_lead_data_and_gives_an_empty_block(
        self,
    ):
        # Q-L2. The error's own text could carry row values (here an email);
        # the log must not.
        people = self.build()
        failure = DatabaseError("row lead05.prompt@x.example First05 Last05")
        with patch("billing.views.top_leads", side_effect=failure):
            with self.assertLogs("billing.views", level="ERROR") as logs:
                template = self.template(people)

        self.assertEqual(self.lead_block(template), "[]")
        logged = "\n".join(logs.output)
        self.assertIn("DatabaseError", logged)
        for leak in ("@x.example", "First05", "Last05", "lead05"):
            self.assertNotIn(leak, logged)

    def test_a_programming_error_is_a_500(self):
        # Q-L2: only DatabaseError is caught; today's KeyError would now
        # surface instead of hiding as "[]".
        self.build()
        self.client.raise_request_exception = False
        with patch("billing.views.top_leads", side_effect=KeyError("slice")):
            response = self.prompt()

        self.assertEqual(response.status_code, 500)
        self.retry.assert_not_called()


class LeadBlockCostTests(PromptFixture):
    def test_the_leads_cost_one_query_whatever_the_cohort(self):
        # By name: on the base (red) billing.views has neither function.
        views = importlib.import_module("billing.views")
        names = ("lead_rows", "top_leads")
        lead_rows, top_leads = (getattr(views, name) for name in names)

        counts = []
        for extra in (0, 9):
            for n in range(extra):
                user = self.person(50 + n + 10 * len(counts))
                BetaProfile.objects.get_or_create(user=user)
            if not counts:
                self.build()
            with CaptureQueriesContext(connection) as ctx:
                lead_rows(top_leads(NOW, 10))
            counts.append(len(ctx.captured_queries))

        self.assertEqual(counts, [1, 1])
