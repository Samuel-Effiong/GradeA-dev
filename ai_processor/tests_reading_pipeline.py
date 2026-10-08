"""AI-call record, slice 2: what the real extraction puts into a reading's run.

`tests_reading_run` holds the rules that turn what a run kept into a label.
This module holds the other half: that the extraction keeps the right
things, on every route by which a paper is read, with only the provider call
replaced (the client's `create`), so the marks made where the call leaves
(`AIProcessor.__ai_model`) are made by the real code.

  * the model is the one the provider NAMED in its reply, never the one asked
    for; "unknown" when it named none;
  * only KEPT replies count: a try the completeness check threw away and a
    part whose answers lost the merge leave nothing behind;
  * the vote is over the answers as saved: a placeholder has none, an answer
    the check dropped has none;
  * the re-check has its own run and four cases, decided by whether the call
    LEFT (recorded at the call), never by the kind of exception that came
    later;
  * the scope: a call for an enforced task type with no run open fails
    loudly, and every reading site opens one.

Every provider reply here has a real string model, or None on purpose
(rule 14).
"""

import json
from contextlib import nullcontext
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from django.test import SimpleTestCase, TestCase, override_settings

from ai_processor import services
from ai_processor.extraction_schemas import ANSWERED, BLANK, NOT_FOUND_IN_DOCUMENT
from ai_processor.reading_run import ReadingRun
from ai_processor.step_run import StepRun, StepScopeMissingError
from students.exceptions import TaskCancelledError
from users.models import CustomUser, UserTypes

SUPER = CustomUser(user_type=UserTypes.SUPER_ADMIN, is_superuser=True)
PROMPT = services.ANSWERS_EXTRACTION_PROMPT.version
IMAGE = {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,PAGE"}}
TEXT = {"type": "text", "text": "the paper"}

QUESTIONS = [
    {"question_number": n, "question_text": f"Q{n}", "points": 5} for n in (1, 2, 3)
]


def answer(number, html="work", status=ANSWERED, **extra):
    return {
        "question_number": number,
        "answer_html": html,
        "answer_status": status,
        "source_page": 1,
        **extra,
    }


def payload(*answers):
    return {"student_name": "Ada", "answers": list(answers)}


def reply(body, model="read/model", *, text=None):
    """A provider reply. `model=None` leaves the attribute off, as a provider
    that does not name its model does."""
    message = SimpleNamespace(content=text if text is not None else json.dumps(body))
    fields: dict[str, Any] = {"choices": [SimpleNamespace(message=message)]}
    fields["usage"] = SimpleNamespace(total_tokens=10)
    if model is not None:
        fields["model"] = model
    return SimpleNamespace(**fields)


def findings(*items):
    return {"findings": list(items)}


def finding(number, found=True):
    return {
        "question_number": number,
        "observed": "pencil marks beside the heading",
        "page": 1,
        "verbatim_fragment": "x = 5 plus carry",
        "content_found": found,
    }


class Provider:
    """The client's create(): `script(n, request)` gives the n-th reply, or an
    exception to raise."""

    def __init__(self, script):
        self.script = script
        self.requests = []
        self.client = SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=self.create))
        )

    def create(self, **request):
        self.requests.append(request)
        out = self.script(len(self.requests), request)
        if isinstance(out, BaseException):
            raise out
        return out


def in_order(*outs):
    return Provider(lambda n, request: outs[min(n, len(outs)) - 1])


def read(provider, content=None, *, user=SUPER, questions=QUESTIONS, **kwargs):
    """The real extraction. Returns (result, the run it was handed)."""
    run = ReadingRun()
    with patch.object(services.ai_processor, "client", provider.client):
        result = services.ai_processor.extract_answer_with_retry(
            user,
            content if content is not None else [TEXT],
            "ctx",
            assignment_model=SimpleNamespace(questions=questions),
            reading_run=run,
            **kwargs,
        )
    return result, run


def label(result, run):
    return run.label_fields(result["answers"])


class TheModelIsTheOneThatAnsweredTest(SimpleTestCase):
    def test_a_one_call_reading_is_labelled_with_the_named_model(self):
        provider = in_order(
            reply(payload(answer(1), answer(2), answer(3)), "served/model")
        )
        result, run = read(provider)
        fields = label(result, run)
        self.assertEqual(fields["answers_model"], "served/model")
        self.assertEqual(fields["answers_prompt_version"], PROMPT)
        self.assertRegex(fields["answers_config_version"], r"^rcfg:[0-9a-f]{12}$")
        self.assertEqual(fields["answers_recheck_result"], "not_run")
        self.assertEqual(fields["answers_recheck_model"], "not_run")

    def test_the_model_asked_for_is_not_the_label(self):
        provider = in_order(reply(payload(answer(1), answer(2), answer(3)), "served/x"))
        result, run = read(provider)
        asked = provider.requests[0]["model"]
        self.assertNotEqual(asked, "served/x")
        self.assertEqual(label(result, run)["answers_model"], "served/x")

    def test_a_provider_that_names_no_model_is_unknown_never_the_one_asked_for(self):
        provider = in_order(reply(payload(answer(1), answer(2), answer(3)), None))
        result, run = read(provider)
        self.assertEqual(label(result, run)["answers_model"], "unknown")

    def test_a_provider_name_equal_to_one_of_our_words_is_unknown(self):
        for word in ("not_run", "unlabelled", "unknown"):
            with self.subTest(word=word):
                provider = in_order(
                    reply(payload(answer(1), answer(2), answer(3)), word)
                )
                result, run = read(provider)
                self.assertEqual(label(result, run)["answers_model"], "unknown")

    def test_a_key_inside_the_replys_own_text_never_reaches_a_label(self):
        """A reply that claims a model, a version or a re-check result in its
        own JSON changes nothing."""
        lying = payload(answer(1), answer(2), answer(3))
        lying.update(
            answers_model="liar/model",
            model="liar/model",
            answers_recheck_result="found_writing",
            answers_config_version="rcfg:000000000000",
        )
        for entry in lying["answers"]:
            entry["answers_model"] = "liar/model"
        result, run = read(in_order(reply(lying, "true/model")))
        fields = label(result, run)
        self.assertEqual(fields["answers_model"], "true/model")
        self.assertEqual(fields["answers_recheck_result"], "not_run")
        self.assertNotIn("liar", json.dumps(fields))
        self.assertNotEqual(fields["answers_config_version"], "rcfg:000000000000")

    def test_the_reading_settings_version_and_release_are_the_runs(self):
        with override_settings(GRADING_RELEASE_ID="2026.10-rc"):
            result, run = read(
                in_order(reply(payload(answer(1), answer(2), answer(3))))
            )
            fields = label(result, run)
        self.assertEqual(fields["answers_release"], "2026.10-rc")
        self.assertEqual(fields["answers_config_version"], run.config.version)


@override_settings(ANSWER_COMPLETENESS_ENFORCEMENT="strict")
class OnlyKeptRepliesCountTest(SimpleTestCase):
    def test_a_try_the_completeness_check_threw_away_leaves_nothing(self):
        """Try 1 (by aaa/bad) has a drifted number, so strict mode rejects it
        and asks again; try 2 (by good/model) is complete. Counted, the
        thrown-away try would tie and win by alphabetical order."""
        provider = in_order(
            reply(payload(answer(1), answer(2), answer(3), answer(9)), "aaa/bad"),
            reply(payload(answer(1), answer(2), answer(3)), "good/model"),
        )
        result, run = read(provider)
        self.assertEqual(len(provider.requests), 2)
        self.assertEqual(label(result, run)["answers_model"], "good/model")

    def test_the_last_try_degrades_and_still_counts_only_itself(self):
        """Tries 1 and 2 (by aaa/bad) supply every question but are rejected;
        try 3 (by zzz/last) supplies only question 1 and is repaired in log
        mode, with placeholders for questions 2 and 3, which have no vote.
        Were the thrown-away tries still counted, aaa/bad would hold the
        origins of questions 2 and 3 and win."""
        provider = in_order(
            reply(payload(answer(1), answer(2), answer(3), answer(9)), "aaa/bad"),
            reply(payload(answer(1), answer(2), answer(3), answer(9)), "aaa/bad"),
            reply(payload(answer(1)), "zzz/last"),
        )
        result, run = read(provider)
        self.assertEqual(len(provider.requests), 3)
        self.assertEqual(
            [a["answer_status"] for a in result["answers"]],
            [ANSWERED, NOT_FOUND_IN_DOCUMENT, NOT_FOUND_IN_DOCUMENT],
        )
        self.assertEqual(label(result, run)["answers_model"], "zzz/last")

    def test_a_placeholder_has_no_vote(self):
        """One real answer by an odd model, two placeholders. The placeholders
        must not be handed to the main model or to 'unknown'."""
        with override_settings(ANSWER_COMPLETENESS_ENFORCEMENT="log"):
            provider = in_order(reply(payload(answer(1)), "odd/model"))
            result, run = read(provider)
        self.assertEqual(
            [a["answer_status"] for a in result["answers"]],
            [ANSWERED, NOT_FOUND_IN_DOCUMENT, NOT_FOUND_IN_DOCUMENT],
        )
        self.assertEqual(label(result, run)["answers_model"], "odd/model")

    def test_an_answer_the_check_dropped_has_no_vote(self):
        """Log mode: the reply supplied questions 1 and 2 and two numbers the
        assignment does not have; the extras are dropped from what is saved."""
        with override_settings(ANSWER_COMPLETENESS_ENFORCEMENT="log"):
            provider = in_order(
                reply(
                    payload(answer(1), answer(2), answer(3), answer(8), answer(9)),
                    "same/model",
                )
            )
            result, run = read(provider, max_retries=1)
        self.assertEqual([a["question_number"] for a in result["answers"]], [1, 2, 3])
        self.assertEqual(run.reading_model(result["answers"]), "same/model")

    def test_a_label_written_as_the_assignment_spells_the_number_still_votes(self):
        questions = [
            {"question_number": f"Q{n}", "question_text": f"Q{n}", "points": 5}
            for n in (1, 2, 3)
        ]
        provider = in_order(
            reply(payload(answer(1), answer(2), answer(3)), "spelled/model")
        )
        result, run = read(provider, questions=questions)
        self.assertEqual(
            [a["question_number"] for a in result["answers"]], ["Q1", "Q2", "Q3"]
        )
        self.assertEqual(label(result, run)["answers_model"], "spelled/model")


def pages(count):
    return [TEXT] + [IMAGE] * count


class ALongPaperTest(SimpleTestCase):
    """Six pages are two parts of three."""

    def test_the_part_that_supplied_most_saved_answers_names_the_model(self):
        part1 = payload(
            answer(1, "a"), answer(2, "b"), answer(3, "", NOT_FOUND_IN_DOCUMENT)
        )
        part2 = payload(
            answer(1, "", NOT_FOUND_IN_DOCUMENT),
            answer(2, "", NOT_FOUND_IN_DOCUMENT),
            answer(3, "c"),
        )
        provider = in_order(reply(part1, "m/one"), reply(part2, "a/two"))
        result, run = read(provider, pages(6))
        self.assertEqual(len(provider.requests), 2)
        self.assertEqual([a["answer_html"] for a in result["answers"]], ["a", "b", "c"])
        # m/one supplied two saved answers, a/two one. Counted by arrival
        # (three each) the tie would go to a/two.
        self.assertEqual(label(result, run)["answers_model"], "m/one")

    def test_an_answer_joined_across_two_parts_counts_for_each_models_part(self):
        part1 = payload(
            answer(1, "first half"),
            answer(2, "", NOT_FOUND_IN_DOCUMENT),
            answer(3, "c"),
        )
        part2 = payload(
            answer(1, "second half"),
            answer(2, "b"),
            answer(3, "", NOT_FOUND_IN_DOCUMENT),
        )
        provider = in_order(reply(part1, "m/one"), reply(part2, "a/two"))
        result, run = read(provider, pages(6))
        self.assertIn("first half", result["answers"][0]["answer_html"])
        self.assertIn("second half", result["answers"][0]["answer_html"])
        # m/one: questions 1 and 3. a/two: questions 1 and 2. A tie, and
        # neither is the main model, so the first by alphabetical order.
        self.assertEqual(label(result, run)["answers_model"], "a/two")

    def test_a_part_read_again_after_unreadable_json_counts_the_good_reply(self):
        part1 = payload(answer(1, "a"), answer(2, "b"), answer(3, "c"))
        provider = in_order(
            reply(None, "aaa/broken", text="this is not json"),
            reply(part1, "good/model"),
            reply(payload(answer(1, "", NOT_FOUND_IN_DOCUMENT)), "good/model"),
        )
        result, run = read(provider, pages(6))
        self.assertEqual(label(result, run)["answers_model"], "good/model")

    def test_the_prompt_version_is_the_one_every_part_was_sent(self):
        part = payload(answer(1, "a"), answer(2, "b"), answer(3, "c"))
        provider = in_order(reply(part, "m/one"), reply(part, "m/one"))
        result, run = read(provider, pages(6))
        self.assertEqual(label(result, run)["answers_prompt_version"], PROMPT)


BLANK_FIRST = payload(answer(1, "", BLANK), answer(2), answer(3))


class TheRecheckTest(SimpleTestCase):
    """The four cases of ruling 3, then every way the re-check does not run.
    The paper has one page image and a blank first answer."""

    CONTENT = [TEXT, IMAGE]

    def _pair(self, provider, **kwargs):
        result, run = read(provider, self.CONTENT, **kwargs)
        fields = label(result, run)
        return (
            (fields["answers_recheck_result"], fields["answers_recheck_model"]),
            result,
            run,
            fields,
        )

    def test_a_finding_that_is_applied_is_found_writing(self):
        provider = in_order(
            reply(BLANK_FIRST, "read/model"),
            reply(findings(finding(1)), "check/model"),
        )
        pair, result, _, fields = self._pair(provider)
        self.assertEqual(pair, ("found_writing", "check/model"))
        self.assertEqual(result["answers"][0]["answer_status"], NOT_FOUND_IN_DOCUMENT)
        # The re-check's own model never becomes the reading's.
        self.assertEqual(fields["answers_model"], "read/model")

    def test_a_reply_read_and_no_finding_applied_is_confirmed_blank(self):
        provider = in_order(
            reply(BLANK_FIRST, "read/model"), reply(findings(), "check/model")
        )
        pair, result, _, _ = self._pair(provider)
        self.assertEqual(pair, ("confirmed_blank", "check/model"))
        self.assertEqual(result["answers"][0]["answer_status"], BLANK)

    def test_a_finding_for_a_question_that_was_not_blank_is_not_applied(self):
        provider = in_order(
            reply(BLANK_FIRST, "read/model"),
            reply(findings(finding(2)), "check/model"),
        )
        pair, _, _, _ = self._pair(provider)
        self.assertEqual(pair, ("confirmed_blank", "check/model"))

    def test_a_call_that_left_and_raised_is_failed_unknown(self):
        provider = in_order(
            reply(BLANK_FIRST, "read/model"), RuntimeError("provider down")
        )
        pair, result, _, _ = self._pair(provider)
        self.assertEqual(len(provider.requests), 2)
        self.assertEqual(pair, ("failed", "unknown"))
        self.assertEqual(result["answers"][0]["answer_status"], BLANK)

    def test_a_reply_that_could_not_be_read_is_failed_with_its_name(self):
        provider = in_order(
            reply(BLANK_FIRST, "read/model"),
            reply(None, "check/model", text="not json at all"),
        )
        pair, _, _, _ = self._pair(provider)
        self.assertEqual(pair, ("failed", "check/model"))

    def test_an_unnamed_reply_that_could_not_be_read_is_failed_unknown(self):
        provider = in_order(
            reply(BLANK_FIRST, "read/model"), reply(None, None, text="{")
        )
        pair, _, _, _ = self._pair(provider)
        self.assertEqual(pair, ("failed", "unknown"))

    def test_an_unnamed_reply_that_was_used_is_unknown(self):
        provider = in_order(reply(BLANK_FIRST, "read/model"), reply(findings(), None))
        pair, _, _, _ = self._pair(provider)
        self.assertEqual(pair, ("confirmed_blank", "unknown"))

    def test_the_rechecks_own_model_setting_is_the_model_it_is_asked_for(self):
        with override_settings(ANSWER_BLANK_VERIFICATION_MODEL="pinned/model"):
            provider = in_order(
                reply(BLANK_FIRST, "read/model"), reply(findings(), "pinned/model")
            )
            pair, _, _, _ = self._pair(provider)
        self.assertEqual(provider.requests[1]["model"], "pinned/model")
        self.assertEqual(pair, ("confirmed_blank", "pinned/model"))

    def test_what_the_recheck_observed_never_reaches_a_label(self):
        provider = in_order(
            reply(BLANK_FIRST, "read/model"),
            reply(findings(finding(1)), "check/model"),
        )
        _, _, _, fields = self._pair(provider)
        text = json.dumps(fields)
        for secret in ("pencil marks", "x = 5", "heading"):
            self.assertNotIn(secret, text)

    def test_a_cancellation_before_the_call_is_not_run_though_it_was_caught(self):
        """The cancel lands after the reading and before the re-check's call.
        The re-check's broad handler catches it and returns the answers; no
        call left, so not_run."""
        armed = []

        def script(n, request):
            armed.append(n)
            return reply(BLANK_FIRST, "read/model")

        def maybe_cancel(processing_task_id, **kwargs):
            if armed:
                raise TaskCancelledError("Task cancelled by user.")

        provider = Provider(script)
        with patch.object(services, "ensure_task_not_cancelled", maybe_cancel):
            pair, result, _, _ = self._pair(provider, processing_task_id="task-1")
        self.assertEqual(len(provider.requests), 1)
        self.assertEqual(pair, ("not_run", "not_run"))
        self.assertEqual(result["answers"][0]["answer_status"], BLANK)

    def test_every_way_the_recheck_does_not_run_is_not_run(self):
        cases = {
            "switched off": (
                {"ANSWER_BLANK_VERIFICATION_ENABLED": False},
                BLANK_FIRST,
                self.CONTENT,
            ),
            "nothing blank": (
                {},
                payload(answer(1), answer(2), answer(3)),
                self.CONTENT,
            ),
            "over the question cap": (
                {"ANSWER_BLANK_VERIFICATION_MAX_QUESTIONS": 0},
                BLANK_FIRST,
                self.CONTENT,
            ),
            "no page images": ({}, BLANK_FIRST, [TEXT]),
            "over the page cap": (
                {"ANSWER_BLANK_VERIFICATION_MAX_PAGES": 0},
                BLANK_FIRST,
                self.CONTENT,
            ),
        }
        for name, (changed, body, content) in cases.items():
            with self.subTest(case=name), override_settings(**changed):  # type: ignore[arg-type]
                provider = in_order(reply(body, "read/model"))
                result, run = read(provider, content)
                self.assertEqual(len(provider.requests), 1)
                fields = label(result, run)
                self.assertEqual(fields["answers_recheck_result"], "not_run")
                self.assertEqual(fields["answers_recheck_model"], "not_run")


class TheRecheckAfterThePaymentTest(TestCase):
    """The reply came and was named, then the credit step raised: the call
    left and a reply was received, so the name is kept and the result is
    failed. And a wallet that refuses before the call is not_run."""

    def setUp(self):
        from ai_processor.tests_real_chunked_extraction import make_teacher_with_credits

        self.teacher = make_teacher_with_credits("reading-recheck@example.com")
        patcher = patch.object(
            services.AIProcessor, "estimate_total_token", return_value=10
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_reply_received_and_then_lost_to_the_credit_step_is_failed_named(self):
        from billing.models import CreditWallet

        real = CreditWallet.consume_credits
        seen = []

        def consume(wallet, *args, **kwargs):
            seen.append(1)
            if len(seen) == 2:
                raise RuntimeError("the credit step failed")
            return real(wallet, *args, **kwargs)

        provider = in_order(
            reply(BLANK_FIRST, "read/model"), reply(findings(), "check/model")
        )
        with patch.object(CreditWallet, "consume_credits", consume):
            result, run = read(provider, [TEXT, IMAGE], user=self.teacher)
        self.assertEqual(len(provider.requests), 2)
        fields = label(result, run)
        self.assertEqual(fields["answers_recheck_result"], "failed")
        self.assertEqual(fields["answers_recheck_model"], "check/model")
        self.assertEqual(fields["answers_model"], "read/model")

    def test_a_wallet_that_refuses_before_the_call_is_not_run(self):
        """The reading is paid; the access check refuses the re-check for an
        empty wallet before any call is made."""
        from billing.access_control import NO_CREDITS_REMAINING_REASON

        real = services.can_user_access_ai
        asked = []

        def access(user, feature=None):
            asked.append(1)
            if len(asked) == 1:
                return real(user, feature=feature)
            return False, NO_CREDITS_REMAINING_REASON

        provider = in_order(reply(BLANK_FIRST, "read/model"))
        with patch.object(services, "can_user_access_ai", access):
            result, run = read(provider, [TEXT, IMAGE], user=self.teacher)
        self.assertEqual(len(asked), 2)
        self.assertEqual(len(provider.requests), 1)
        fields = label(result, run)
        self.assertEqual(fields["answers_recheck_result"], "not_run")
        self.assertEqual(fields["answers_recheck_model"], "not_run")


class TheMarksAreMadeAtTheProviderCallTest(SimpleTestCase):
    def _call(self, provider, run=None, task_type="extract_answer"):
        function = services.ai_processor.get_ai_model_function()
        scope = run.scope() if run is not None else nullcontext()
        with patch.object(services.ai_processor, "client", provider.client):
            with scope:
                return function(
                    system_prompt="s",
                    user_prompt="u",
                    prompt_version="v1",
                    task_type=task_type,
                )

    def test_a_named_reply_marks_the_call_and_the_reply(self):
        run = StepRun("answers")
        self._call(in_order(reply(payload(), "named/model")), run)
        self.assertTrue(run.call_left)
        self.assertTrue(run.reply_received)
        self.assertEqual(run.reply_model, "named/model")

    def test_an_unnamed_reply_is_unknown_not_the_model_asked_for(self):
        run = StepRun("answers")
        self._call(in_order(reply(payload(), None)), run)
        self.assertEqual(run.reply_model, "unknown")

    def test_a_call_that_raises_left_and_received_nothing(self):
        run = StepRun("answers")
        with self.assertRaises(RuntimeError):
            self._call(in_order(RuntimeError("down")), run)
        self.assertTrue(run.call_left)
        self.assertFalse(run.reply_received)

    def test_the_call_is_marked_before_it_leaves(self):
        run = StepRun("answers")
        seen = []

        def script(n, request):
            seen.append(run.call_left)
            return reply(payload(), "m/x")

        self._call(Provider(script), run)
        self.assertEqual(seen, [True])

    def test_the_reply_is_not_marked_before_it_came(self):
        run = StepRun("answers")
        seen = []

        def script(n, request):
            seen.append(run.reply_received)
            return reply(payload(), "m/x")

        self._call(Provider(script), run)
        self.assertEqual(seen, [False])

    def test_an_enforced_task_type_with_no_scope_fails_before_the_provider(self):
        provider = in_order(reply(payload(), "m/x"))
        with self.assertRaises(StepScopeMissingError):
            self._call(provider, None, task_type="extract_answer")
        self.assertEqual(provider.requests, [])

    def test_a_task_type_that_is_not_enforced_is_unchanged(self):
        provider = in_order(reply(payload(), "m/x"))
        response = self._call(provider, None, task_type="grade_assignment")
        self.assertEqual(response.model, "m/x")
        self.assertEqual(len(provider.requests), 1)

    def test_the_run_is_never_inside_what_the_provider_sends(self):
        run = StepRun("answers")
        provider = in_order(reply(payload(), "m/x"))
        self._call(provider, run)
        self.assertNotIn("StepRun", json.dumps(provider.requests[0], default=repr))


class AWholeRetryUsesOneRunTest(SimpleTestCase):
    def test_the_reading_and_the_recheck_are_two_separate_steps(self):
        run = ReadingRun()
        self.assertIsNot(run.reading, run.recheck)
        self.assertIsInstance(run.reading, StepRun)
        self.assertIsInstance(run.recheck, StepRun)

    def test_a_caller_that_passes_no_run_still_reads(self):
        """Callers that do not label (the benchmark) get a throwaway run."""
        provider = in_order(reply(payload(answer(1), answer(2), answer(3)), "m/x"))
        with patch.object(services.ai_processor, "client", provider.client):
            result = services.ai_processor.extract_answer_with_retry(
                SUPER,
                [TEXT],
                "ctx",
                assignment_model=SimpleNamespace(questions=QUESTIONS),
            )
        self.assertEqual(len(result["answers"]), 3)
