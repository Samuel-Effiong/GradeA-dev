#!/usr/bin/env bash
# H-14 mutation battery on dashboard/services.py; restores from the committed blob and verifies sha256.
set -u
# Hardening (2026-09-28, RESTORE_MISMATCH follow-up): every path below (F, the
# python heredoc's open(), `git checkout --`, `sha256sum`) is CWD-relative, so
# this script only behaves correctly when run from the worktree root. Pin the
# cwd explicitly instead of assuming the caller got it right.
cd "$(git rev-parse --show-toplevel)" || { echo "FATAL: not inside a git worktree"; exit 1; }
F=dashboard/services.py
GOOD=$(git show HEAD:$F | sha256sum | cut -d' ' -f1)
run() { python manage.py test dashboard.tests_h14_at_risk_equivalence --settings=settings_worktree --noinput --parallel 1 2>&1 | grep -E "^(FAIL|ERROR):|^(Ran|FAILED|OK)" | sed 's/ (dashboard.*//'; }
restore() { git checkout -- $F; [ "$(sha256sum $F | cut -d' ' -f1)" = "$GOOD" ] && echo "restore_ok sha256=${GOOD:0:16}" || echo "RESTORE_MISMATCH"; }
mut() { name=$1; old=$2; new=$3; echo; echo "== mutant $name"; OLD="$old" NEW="$new" python - <<'PY'
import os
s=open("dashboard/services.py").read()
old,new=os.environ["OLD"],os.environ["NEW"]
assert s.count(old)==1,("not unique",old)
open("dashboard/services.py","w").write(s.replace(old,new))
PY
  run; restore; }
echo "control (unmutated):"; run
mut a-ignore-enrolled-courses-filter 'if row[1] in student_course_ids' 'if True'
mut b-count-unpublished-scores 'if is_published and score_percentage is not None' 'if score_percentage is not None'
mut c-include-withdrawn 'enrollment_status=EnrollmentStatusType.ENROLLED,
            student__is_active=True,
            student__user_type=UserTypes.STUDENT,
        ).values_list' 'student__is_active=True,
            student__user_type=UserTypes.STUDENT,
        ).values_list'
mut d-include-inactive-students '            student__is_active=True,
            student__user_type=UserTypes.STUDENT,
        ).values_list' '            student__user_type=UserTypes.STUDENT,
        ).values_list'
mut e-drop-submission-ordering '.order_by("submission_date", "id")
        )
        for row in submissions:' '.order_by("id")
        )
        for row in submissions:'
mut f-count-future-assignments '.filter(Q(due_date__isnull=True) | Q(due_date__lte=timezone.now()))' ''
mut g-submitted-count-off-by-one 'submitted_count = len({row[0] for row in student_submissions})' 'submitted_count = len({row[0] for row in student_submissions}) + 1'
