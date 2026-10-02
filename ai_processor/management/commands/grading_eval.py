"""
grading_eval — the accuracy scoreboard for the AI grading system.

Read-only. Walks graded submissions in a time window and computes the
metrics that say whether the second-opinion system is improving grading
accuracy or merely generating AI calls:

1. Coverage & trigger mix — how often a second opinion ran and why.
2. Disagreement rates — overall and segmented (question type, severity
   tier, confidence band, course, model pair).
3. Teacher alignment — when teachers resolved a disagreement, whose
   grade were they closer to: grader A's or grader B's?
4. Trigger calibration — is low self-reported confidence actually
   predictive of disagreement, and what hidden-error rate does the
   random QA sample find on "easy" cases?
5. Regrade baseline — the pre-existing human-correction signal.

Every input was already persisted by the grading pipeline: `graded_by`
provenance, `feedback["second_opinion"]` blocks, trigger reasons,
`review_reasons` resolutions ("confirmed"/"overridden"), and
score-vs-ai_score regrade deltas.

Usage:
    python manage.py grading_eval [--days 90] [--course <id>] [--json]
"""

import json as json_module
from collections import Counter, defaultdict
from datetime import timedelta

from django.core.management.base import BaseCommand
from django.utils import timezone

from ai_processor.services import AIProcessor
from students.models import StudentSubmission

KEY_FN = AIProcessor._question_number_key


def _rate(numerator, denominator):
    """A ratio that renders as n/a instead of dividing by zero."""
    if not denominator:
        return None
    return round(numerator / denominator, 4)


def _confidence_band(value):
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        return "unknown"
    if confidence < 60:
        return "<60"
    if confidence < 80:
        return "60-79"
    return "80+"


def _score_band(score, max_points):
    try:
        fraction = float(score) / float(max_points)
    except (TypeError, ValueError, ZeroDivisionError):
        return "unknown"
    if fraction < 0.5:
        return "0-49%"
    if fraction < 0.8:
        return "50-79%"
    return "80-100%"


class Command(BaseCommand):
    help = (
        "Grading-accuracy scoreboard: second-opinion coverage, "
        "disagreement rates (segmented), teacher-alignment, trigger "
        "calibration, and regrade baseline. Read-only."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=90,
            help="Window on graded_at (default 90 days).",
        )
        parser.add_argument(
            "--course", default=None, help="Optional course id to scope to."
        )
        parser.add_argument(
            "--json",
            action="store_true",
            dest="as_json",
            help="Emit the metrics dict as JSON instead of text sections.",
        )

    # ── Data collection ───────────────────────────────────────────────

    def _collect(self, days, course_id):
        since = timezone.now() - timedelta(days=days)
        submissions = (
            StudentSubmission.objects.filter(graded_at__gte=since)
            .select_related("assignment", "assignment__course")
            .only(
                "feedback",
                "review_reasons",
                "grading_confidence",
                "score",
                "ai_score",
                "max_points",
                "was_regraded",
                "graded_at",
                "assignment__questions",
                "assignment__course__name",
                "assignment__course_id",
            )
        )
        if course_id:
            submissions = submissions.filter(assignment__course_id=course_id)

        metrics = {
            "window_days": days,
            "graded_submissions": 0,
            "unparseable_feedback": 0,
            "coverage": {"ran": 0, "skipped": 0, "error": 0, "not_run": 0},
            "trigger_mix": Counter(),
            "questions_compared": 0,
            "questions_disagreed": 0,
            "submissions_flagged": 0,
            "disagreement_by": {
                "question_type": defaultdict(lambda: [0, 0]),  # [disagreed, compared]
                "tier": Counter(),
                "confidence_band": defaultdict(lambda: [0, 0]),
                "a_score_band": defaultdict(lambda: [0, 0]),
                "course": defaultdict(lambda: [0, 0]),
                "model_pair": defaultdict(lambda: [0, 0]),
            },
            "teacher_alignment": {
                "confirmed_a_vindicated": 0,
                "overridden": 0,
                "overridden_a_closer": 0,
                "overridden_b_closer": 0,
                "overridden_tie": 0,
            },
            "calibration": {
                "by_confidence_band": defaultdict(lambda: [0, 0]),
                "qa_sample": [0, 0],  # [disagreed, compared] on sampled-only runs
                "triggered": [0, 0],  # everything not purely qa_sample
            },
            "regrade": {"total": 0, "regraded": 0, "delta_fractions": []},
        }

        for submission in submissions.iterator():
            metrics["graded_submissions"] += 1
            self._collect_regrade(submission, metrics)

            feedback = submission.feedback
            if feedback is not None and not isinstance(feedback, dict):
                metrics["unparseable_feedback"] += 1
                continue
            feedback = feedback or {}

            block = feedback.get("second_opinion")
            if not isinstance(block, dict):
                metrics["coverage"]["not_run"] += 1
                continue
            if "error" in block:
                metrics["coverage"]["error"] += 1
                continue
            if "skipped" in block:
                metrics["coverage"]["skipped"] += 1
                continue
            metrics["coverage"]["ran"] += 1

            self._collect_run(submission, feedback, block, metrics)
            self._collect_resolutions(submission, block, metrics)

        return metrics

    def _collect_regrade(self, submission, metrics):
        metrics["regrade"]["total"] += 1
        if submission.was_regraded:
            metrics["regrade"]["regraded"] += 1
            try:
                delta = abs(float(submission.score) - float(submission.ai_score))
                metrics["regrade"]["delta_fractions"].append(
                    delta / float(submission.max_points)
                )
            except (TypeError, ValueError, ZeroDivisionError):
                pass

    def _collect_run(self, submission, feedback, block, metrics):
        selected = block.get("selected") or {}
        reasons_flat = [r for reasons in selected.values() for r in reasons]
        for reason in reasons_flat:
            metrics["trigger_mix"][reason] += 1

        agreements = block.get("agreements") or []
        disagreements = block.get("disagreements") or []
        compared = len(agreements) + len(disagreements)
        disagreed = len(disagreements)
        if not compared:
            return

        metrics["questions_compared"] += compared
        metrics["questions_disagreed"] += disagreed
        if disagreed:
            metrics["submissions_flagged"] += 1

        segments = metrics["disagreement_by"]

        confidence_band = _confidence_band(submission.grading_confidence)
        segments["confidence_band"][confidence_band][0] += disagreed
        segments["confidence_band"][confidence_band][1] += compared
        metrics["calibration"]["by_confidence_band"][confidence_band][0] += disagreed
        metrics["calibration"]["by_confidence_band"][confidence_band][1] += compared

        course = submission.assignment.course
        course_name = course.name if course else "unknown"
        segments["course"][course_name][0] += disagreed
        segments["course"][course_name][1] += compared

        pair = f"{feedback.get('grading_model', 'unknown')} × {block.get('model', 'unknown')}"
        segments["model_pair"][pair][0] += disagreed
        segments["model_pair"][pair][1] += compared

        # QA-sample calibration: a run whose EVERY trigger reason is
        # qa_sample is a random probe of "easy" cases — its disagreement
        # rate is the hidden-error rate the sample exists to measure.
        is_pure_sample = bool(reasons_flat) and all(
            reason == "qa_sample" for reason in reasons_flat
        )
        bucket = "qa_sample" if is_pure_sample else "triggered"
        metrics["calibration"][bucket][0] += disagreed
        metrics["calibration"][bucket][1] += compared

        # Per-question segments need the rubric for type/points.
        questions = submission.assignment.questions or []
        question_by_key = {
            KEY_FN(q.get("question_number")): q
            for q in questions
            if isinstance(q, dict)
        }
        evaluations = {
            KEY_FN(ev.get("question_number")): ev
            for ev in feedback.get("question_evaluations") or []
            if isinstance(ev, dict)
        }
        disagreed_keys = set()
        for disagreement in disagreements:
            key = KEY_FN(disagreement.get("question_number"))
            disagreed_keys.add(key)
            tier = (disagreement.get("severity") or {}).get("tier") or "untiered"
            segments["tier"][tier] += 1
        for number in list(agreements) + [
            d.get("question_number") for d in disagreements
        ]:
            key = KEY_FN(number)
            question = question_by_key.get(key) or {}
            question_type = question.get("question_type") or "unknown"
            hit = 1 if key in disagreed_keys else 0
            segments["question_type"][question_type][0] += hit
            segments["question_type"][question_type][1] += 1

            evaluation = evaluations.get(key) or {}
            band = _score_band(evaluation.get("score_awarded"), question.get("points"))
            segments["a_score_band"][band][0] += hit
            segments["a_score_band"][band][1] += 1

    def _collect_resolutions(self, submission, block, metrics):
        alignment = metrics["teacher_alignment"]
        resolutions = [
            entry
            for entry in (submission.review_reasons or [])
            if isinstance(entry, dict) and entry.get("resolved")
        ]
        if not resolutions:
            return
        resolution = resolutions[-1]["resolved"]
        if resolution == "confirmed":
            # Teacher looked at both graders and kept grader A's grade.
            alignment["confirmed_a_vindicated"] += 1
        elif resolution == "overridden":
            alignment["overridden"] += 1
            # Whose TOTAL sat closer to the teacher's final score?
            # B's implied total = A's total adjusted by the per-question
            # gaps on the disagreed questions.
            try:
                a_total = float(submission.ai_score)
                teacher_total = float(submission.score)
            except (TypeError, ValueError):
                return
            gap = sum(
                float((d.get("b") or {}).get("score_awarded") or 0)
                - float((d.get("a") or {}).get("score_awarded") or 0)
                for d in block.get("disagreements") or []
            )
            b_total = a_total + gap
            a_distance = abs(teacher_total - a_total)
            b_distance = abs(teacher_total - b_total)
            if a_distance < b_distance:
                alignment["overridden_a_closer"] += 1
            elif b_distance < a_distance:
                alignment["overridden_b_closer"] += 1
            else:
                alignment["overridden_tie"] += 1

    # ── Reporting ─────────────────────────────────────────────────────

    def _finalize(self, metrics):
        """Convert working structures into a JSON-friendly report dict."""

        def segment(table):
            return {
                key: {
                    "disagreed": pair[0],
                    "compared": pair[1],
                    "rate": _rate(pair[0], pair[1]),
                }
                for key, pair in sorted(table.items())
            }

        deltas = metrics["regrade"]["delta_fractions"]
        report = {
            "window_days": metrics["window_days"],
            "graded_submissions": metrics["graded_submissions"],
            "unparseable_feedback": metrics["unparseable_feedback"],
            "second_opinion_coverage": dict(metrics["coverage"]),
            "trigger_mix": dict(metrics["trigger_mix"]),
            "disagreement": {
                "questions_compared": metrics["questions_compared"],
                "questions_disagreed": metrics["questions_disagreed"],
                "question_rate": _rate(
                    metrics["questions_disagreed"], metrics["questions_compared"]
                ),
                "submissions_flagged": metrics["submissions_flagged"],
                "by_question_type": segment(
                    metrics["disagreement_by"]["question_type"]
                ),
                "by_tier": dict(metrics["disagreement_by"]["tier"]),
                "by_confidence_band": segment(
                    metrics["disagreement_by"]["confidence_band"]
                ),
                "by_a_score_band": segment(metrics["disagreement_by"]["a_score_band"]),
                "by_course": segment(metrics["disagreement_by"]["course"]),
                "by_model_pair": segment(metrics["disagreement_by"]["model_pair"]),
            },
            "teacher_alignment": dict(metrics["teacher_alignment"]),
            "calibration": {
                "by_confidence_band": segment(
                    metrics["calibration"]["by_confidence_band"]
                ),
                "qa_sample_rate": _rate(*metrics["calibration"]["qa_sample"]),
                "triggered_rate": _rate(*metrics["calibration"]["triggered"]),
            },
            "regrade_baseline": {
                "total": metrics["regrade"]["total"],
                "regraded": metrics["regrade"]["regraded"],
                "regrade_rate": _rate(
                    metrics["regrade"]["regraded"], metrics["regrade"]["total"]
                ),
                "mean_abs_delta_fraction": (
                    round(sum(deltas) / len(deltas), 4) if deltas else None
                ),
            },
        }
        return report

    def _render_text(self, report):
        def fmt(value):
            return "n/a" if value is None else value

        lines = [
            f"Grading evaluation — last {report['window_days']} day(s)",
            f"  graded submissions: {report['graded_submissions']} "
            f"(unparseable feedback: {report['unparseable_feedback']})",
            "",
            "1. Second-opinion coverage",
        ]
        for key, value in report["second_opinion_coverage"].items():
            lines.append(f"  {key}: {value}")
        lines.append("  trigger mix:")
        for reason, count in sorted(report["trigger_mix"].items()):
            lines.append(f"    {reason}: {count}")

        disagreement = report["disagreement"]
        lines += [
            "",
            "2. Disagreement",
            f"  question-level: {disagreement['questions_disagreed']}/"
            f"{disagreement['questions_compared']} "
            f"(rate: {fmt(disagreement['question_rate'])})",
            f"  submissions flagged: {disagreement['submissions_flagged']}",
        ]
        for name in (
            "by_tier",
            "by_question_type",
            "by_confidence_band",
            "by_a_score_band",
            "by_model_pair",
            "by_course",
        ):
            table = disagreement[name]
            if not table:
                continue
            lines.append(f"  {name}:")
            for key, value in sorted(table.items()):
                if isinstance(value, dict):
                    lines.append(
                        f"    {key}: {value['disagreed']}/{value['compared']} "
                        f"(rate: {fmt(value['rate'])})"
                    )
                else:
                    lines.append(f"    {key}: {value}")

        alignment = report["teacher_alignment"]
        lines += [
            "",
            "3. Teacher alignment",
            f"  confirmed (grader A vindicated): {alignment['confirmed_a_vindicated']}",
            f"  overridden: {alignment['overridden']} "
            f"(A closer: {alignment['overridden_a_closer']}, "
            f"B closer: {alignment['overridden_b_closer']}, "
            f"tie: {alignment['overridden_tie']})",
        ]

        calibration = report["calibration"]
        lines += [
            "",
            "4. Trigger calibration",
            f"  qa_sample disagreement rate (hidden errors on 'easy' cases): "
            f"{fmt(calibration['qa_sample_rate'])}",
            f"  triggered-case disagreement rate: {fmt(calibration['triggered_rate'])}",
        ]
        for band, value in sorted(calibration["by_confidence_band"].items()):
            lines.append(
                f"  confidence {band}: {value['disagreed']}/{value['compared']} "
                f"(rate: {fmt(value['rate'])})"
            )

        regrade = report["regrade_baseline"]
        lines += [
            "",
            "5. Regrade baseline",
            f"  regraded: {regrade['regraded']}/{regrade['total']} "
            f"(rate: {fmt(regrade['regrade_rate'])})",
            f"  mean |teacher − AI| as fraction of max points: "
            f"{fmt(regrade['mean_abs_delta_fraction'])}",
        ]
        return "\n".join(lines)

    def handle(self, *args, **options):
        metrics = self._collect(options["days"], options["course"])
        report = self._finalize(metrics)

        if options["as_json"]:
            self.stdout.write(json_module.dumps(report, indent=2, default=str))
            return

        if not report["graded_submissions"]:
            self.stdout.write(
                f"No graded submissions in the last {options['days']} day(s) — "
                "no data to evaluate."
            )
            return

        self.stdout.write(self._render_text(report))
