"""B1b-3 survey (NOTES §3): where the queries go. Measurement only.

Not under any app, so no near run collects it. Run it as a test module with
this directory on PYTHONPATH:

    PYTHONPATH=docs/evidence/epic-b1b/step3 python manage.py test b1b3_survey

1. PerCallQueries: one billed single call (provider stubbed) after a warm-up,
   flag on and flag off. Every statement is listed in order with its shape
   and the innermost repo function that issued it.
2. CascadeQueries: the 1,000-row assignment cascade of
   billing/tests/test_holds_fb1_cascade_timing.py's "one_job" shape (900
   RETURNED, 100 HELD), every statement grouped by (repo function, shape).

It asserts nothing about speed or counts.
"""

import os
import re
import traceback
import uuid
from collections import Counter
from unittest.mock import patch

from django.conf import settings
from django.db import connection
from django.test import TestCase, TransactionTestCase, override_settings

from ai_processor.services import AIProcessor
from assignments.models import Assignment, AssignmentStatus
from billing.models import AIJob, AIJobType, CreditWallet, HoldState
from billing.testing.holds import (
    ai_ready_teacher,
    ai_response,
    call_ai,
    estimate_for,
    funded_wallet,
)
from classrooms.models import Course, Session
from students.models import BackgroundProcessingTask

BASE = str(settings.BASE_DIR) + os.sep
PROVIDER = "_AIProcessor__ai_model"


def shape(sql):
    sql = re.sub(r'"s\d+_x\d+"', "SP", sql)
    sql = re.sub(r"'[^']*'", "?", sql)
    sql = re.sub(r"\b\d+\b", "?", sql)
    sql = re.sub(r"\(\?(, \?)+\)", "(?…)", sql)
    return " ".join(sql.split())[:150]


def site():
    """The innermost frame in this repo outside tests and this file."""
    for frame in reversed(traceback.extract_stack()[:-3]):
        name = frame.filename
        if not name.startswith(BASE) or "/site-packages/" in name:
            continue
        rel = name[len(BASE) :]
        if rel.startswith("docs/") or "/tests" in rel or "/testing/" in rel:
            continue
        return f"{rel}:{frame.lineno} {frame.name}"
    return "(test code)"


class Recorder:
    def __init__(self):
        self.rows = []

    def __call__(self, execute, sql, params, many, context):
        self.rows.append((site(), shape(sql)))
        return execute(sql, params, many, context)


class PerCallQueries(TestCase):
    def one_call(self, enabled, tag):
        with override_settings(BILLING_HOLDS_ENABLED=enabled):
            teacher = ai_ready_teacher(f"b1b3.{tag}@example.com", 50 * estimate_for())
            with patch.object(AIProcessor, PROVIDER, return_value=ai_response(150)):
                for _ in range(3):
                    call_ai(teacher)  # warm-up: caches, first-job paths
                recorder = Recorder()
                with connection.execute_wrapper(recorder):
                    call_ai(teacher)
        return recorder.rows

    def test_print_the_queries_of_one_call(self):
        for enabled, tag in ((False, "off"), (True, "on")):
            rows = self.one_call(enabled, tag)
            print(f"\nB1B3-CALL flag={'on' if enabled else 'off'} queries={len(rows)}")
            for index, (where, sql) in enumerate(rows, 1):
                print(f"  {index:3d} {where} | {sql}")


ITEMS = 1000
HELD_ITEMS = 100
HOLD = 50
DRAWN = 30


@override_settings(BILLING_HOLDS_ENABLED=True)
class CascadeQueries(TransactionTestCase):
    def build(self):
        wallet = funded_wallet("b1b3.cascade@example.com", 10**7)
        teacher = wallet.user
        session = Session.objects.create(name="S", teacher=teacher)
        course = Course.objects.create(name="C", teacher=teacher, session=session)
        assignment = Assignment.objects.create(
            title="A", course=course, status=AssignmentStatus.PUBLISHED, questions=[]
        )
        settled = ITEMS - HELD_ITEMS
        job = AIJob.objects.create(
            job_type=AIJobType.UPLOAD,
            wallet=wallet,
            requested_by=teacher,
            trace_id=uuid.uuid4(),
            total_items=ITEMS,
            reserved_credits=ITEMS * HOLD,
            hold_drawn=settled * DRAWN,
            released_credits=settled * (HOLD - DRAWN),
        )
        CreditWallet.objects.filter(pk=wallet.pk).update(
            reserved_credits=HELD_ITEMS * HOLD
        )
        rows = []
        for index in range(ITEMS):
            held = index < HELD_ITEMS
            rows.append(
                BackgroundProcessingTask(
                    requested_by=teacher,
                    task_type="answer_extraction",
                    assignment=assignment,
                    job=job,
                    hold_credits=HOLD,
                    hold_state=HoldState.HELD if held else HoldState.RETURNED,
                    hold_drawn=0 if held else DRAWN,
                    released_credits=0 if held else HOLD - DRAWN,
                )
            )
        BackgroundProcessingTask.objects.bulk_create(rows)
        return assignment

    def test_print_the_cascade_queries_by_purpose(self):
        assignment = self.build()
        recorder = Recorder()
        with connection.execute_wrapper(recorder):
            assignment.delete()
        counts = Counter(recorder.rows)
        print(
            f"\nB1B3-CASCADE items={ITEMS} held={HELD_ITEMS} queries={len(recorder.rows)}"
        )
        for (where, sql), count in counts.most_common():
            print(f"  {count:5d} {where} | {sql}")
