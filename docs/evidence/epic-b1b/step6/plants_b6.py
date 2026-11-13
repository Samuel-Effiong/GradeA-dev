# flake8: noqa  (evidence script: plant strings are kept verbatim)
"""B1b-6 plants (NOTES §6.6.5 / §6.6.10). Usage, from the worktree root:
plants_b6.py check            every old string occurs exactly once
plants_b6.py apply PB6-n      apply one plant (the runner restores by git checkout)
plants_b6.py label PB6-n      print the killer label
"""

import sys
import textwrap

BQ = "billing/batch_queue.py"
T = "billing.tests.test_batch_queue."

AUTO_CALL = """        _dispatch_tracked_grading(
            assignment.course.teacher,
            assignment,
            to_send,
            session,
            only_if_ungraded=True,
            first_index=first_index,
            adopt=adopt or (),
        )
"""
SCHED_CALL = """        _dispatch_tracked_grading(
            CustomUser.objects.get(id=user_id),
            Assignment.objects.get(id=assignment_id),
            submissions,
            (
                BatchUploadSession.objects.filter(id=batch_id).first()
                if batch_id
                else None
            ),
            only_if_ungraded=True,
        )
"""
VIEW_CALL = """        pairs = _dispatch_tracked_grading(
            request.user,
            assignment,
            ungraded_submissions,
            session,
            request=request,
            batch_id=session.id,
        )
"""


def wrapped(block):
    """The call run with the flag off: this caller bypasses the batch path."""
    return (
        "        from django.test import override_settings as _off\n\n"
        "        with _off(BILLING_HOLDS_ENABLED=False):\n"
        + textwrap.indent(block, "    ")
    )


PLANTS = {
    "PB6-1": (
        BQ,
        "            ids = [row.pk for row in claimed]\n",
        "            ids = [row.pk for row in waiting]\n            claimed = waiting\n",
        T + "PromotionTests.test_promotion_holds_only_the_papers_that_now_fit",
    ),
    "PB6-2": (
        BQ,
        "        for row in waiting:\n            error = _mid_batch_error(",
        "        for row in waiting:\n            row.delete()\n            continue\n            error = _mid_batch_error(",
        T + "ExpiryTests.test_expiry_fails_the_waiting_papers_as_retryable",
    ),
    "PB6-3": (
        BQ,
        "            moved = AIJob.objects.filter(\n                pk=job.pk, status=AIJobStatus.QUEUED_PENDING_CREDITS\n            ).update(status=AIJobStatus.RESERVED)\n",
        "            moved = AIJob.objects.filter(\n                pk=job.pk\n            ).update(status=AIJobStatus.RESERVED)\n",
        T
        + "PromotionTests.test_two_promoters_at_once_launch_each_paper_once "
        + T
        + "ExpiryTests.test_promotion_and_expiry_at_once_have_one_winner",
    ),
    "PB6-4": (
        "assignments/tasks.py",
        "        transaction.on_commit(_launch)\n",
        "        _launch()\n",
        T + "BatchStartTests.test_no_message_is_sent_before_the_batch_commits",
    ),
    "PB6-5": (
        "billing/holds.py",
        "        leftover = left - drawn\n",
        "        leftover = 0\n",
        T
        + "PromotionTests.test_a_settled_papers_leftover_promotes_a_queued_job_at_once",
    ),
    "PB6-6": (
        BQ,
        "        return\n    holds.fail_item(job, item=task)\n",
        "        return\n    return\n",
        T
        + "EndedBeforeItsUnitTests.test_a_skipped_paper_returns_its_hold_and_the_sweep_leaves_it",
    ),
    "PB6-7": (
        "billing/holds.py",
        "        count_it = item_row is None or item_row.hold_state == HoldState.HELD\n",
        "        count_it = True\n",
        T
        + "EndedBeforeItsUnitTests.test_a_paper_the_hook_released_is_not_counted_twice",
    ),
    "PB6-8": (
        BQ,
        "        return\n    holds.fail_item(job, item=task)\n",
        "        return\n    holds.fail_item(job, item=task)\n    holds._move_wallet(job.wallet_id, -task.hold_credits)\n",
        T
        + "EndedBeforeItsUnitTests.test_a_paper_the_hook_released_then_deleted_returns_its_hold_once",
    ),
    "PB6-9": (
        BQ,
        "            for session_id in {row.batch_session_id for row in claimed}:\n                if session_id:\n                    clear_batch_credit_stop(session_id)\n",
        "",
        T + "PromotionTests.test_a_promoted_paper_gets_past_the_batch_credit_stop",
    ),
    "PB6-10": (
        BQ,
        "        if job_row.completed_items + job_row.failed_items < claimed:\n",
        "        if job_row.completed_items < claimed:\n",
        T + "LastItemTests.test_a_job_whose_papers_all_failed_ends_failed",
    ),
    "PB6-11": (
        "assignments/views.py",
        VIEW_CALL,
        wrapped(VIEW_CALL),
        T + "GradeAllTests.test_a_waiting_paper_is_listed_with_no_task_id",
    ),
    "PB6-12": (
        "assignments/tasks.py",
        AUTO_CALL,
        wrapped(AUTO_CALL),
        T + "AutomaticPathTests.test_an_automatic_run_that_runs_out_queues_and_resumes",
    ),
    "PB6-13": (
        "assignments/tasks.py",
        SCHED_CALL,
        wrapped(SCHED_CALL),
        T + "AutomaticPathTests.test_a_scheduled_batch_is_one_job",
    ),
    "PB6-14": (
        "assignments/tasks.py",
        "            collect.append(detach_item(item))\n",
        "            detach_item(item)\n",
        T
        + "AutomaticPathTests.test_after_an_outage_and_retry_the_teacher_sees_one_line_per_paper",
    ),
}


def main():
    cmd = sys.argv[1]
    if cmd == "check":
        bad = 0
        for name, (path, old, _new, _label) in PLANTS.items():
            n = open(path).read().count(old)
            print(f"{name:7s} {path:24s} occurrences={n}")
            bad += n != 1
        sys.exit(1 if bad else 0)
    path, old, new, label = PLANTS[sys.argv[2]]
    if cmd == "label":
        print(label)
        return
    text = open(path).read()
    assert text.count(old) == 1, (sys.argv[2], "old string not unique")
    open(path, "w").write(text.replace(old, new))
    print(path)


main()
