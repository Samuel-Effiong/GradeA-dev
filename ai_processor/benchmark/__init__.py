"""
Ground-truth grading benchmark.

Every one of the project's other grading tests mocks the model, so they
encode our beliefs about how grading behaves rather than how it actually
behaves. This package is the opposite: a fixed, hand-authored set of
assignments and student answers whose correct grades are known in
advance, run against the real model and scored against that ground truth.

Layout:
    dataset.py   canonical assignments, student answers, expected grades
                 (pure data — no Django, no services import, so it can be
                 validated without an API key)
    scoring.py   metric computation over a completed run
    runner.py    executes the dataset in live / record / replay mode
    render.py    Playwright + KaTeX PDF artifacts (on-demand only)

Entry point: `python manage.py grading_benchmark`.
"""
