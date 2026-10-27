#!/bin/bash
# H-264 a numbered paper for another assignment is split back to its own questions (beta line). Usage: bash run_h264_gate.sh <tip> 1
# One grant, inside ONE outer inhibit (memory cap = available - 2G, min 6G, read at start; rule 13), and the exit
# code captured by the start command:
#   systemd-inhibit --what=idle:sleep:handle-lid-switch --who=GAP --why=GAP-test-run --mode=block bash <script> <tip> 1 > out; echo "gate exit=$?" >> out
# 0 the red run: the H-264 + blank-verification test modules as at the tip against the code as at the base (three
#   code files taken back, prompt 4 restored, prompt 5 and the archive removed; own DB). RULE 22: each red test is red
#   for its WRITTEN reason (a fragment looked for in every one of its own FAIL/ERROR blocks), and exactly 29 blocks
#   appear (22 FAIL + 6 ERROR in h264, 1 FAIL in blank_verification);
# 1a makemigrations --check; 1b the seam by program (three code files + prompt files are the only code changed; by
#   their syntax trees only the named units changed, only the named statements); 1 the own modules + near (all
#   ai_processor tests) + guards;
#   2 the 16 mutants (rule 17; own DB), each judged by its expected tests AND their written fragments.
# The regression is the batch's one full run on the frozen tip; it is not in this script.
# A trap on EXIT restores the tree. RULE 18: every run writes straight to a file, stdin from /dev/null, nothing piped.
set -u
# BASE = b199eed6, the beta tip on which this branch stacks; the red run takes the code files back to it.
TIP=$1; STEP=${2:-1}; BASE=b199eed6
cd /home/bond-servant-in-training/Documents/Projects/Grade-Automator-Plus-h264-numbered-offtopic-answers || exit 1
source /home/bond-servant-in-training/Documents/Virtualenvs/AutoGrader_env/bin/activate
E=docs/evidence/h264-numbered-offtopic-answers
AVAIL_G=$(awk '/^MemAvailable:/ {print int($2/1048576)}' /proc/meminfo)
MEMG=$((AVAIL_G - 2))
echo "available at start: ${AVAIL_G}G; cap ${MEMG}G"
[ "$MEMG" -ge 6 ] || { echo "STOP (exit 5): cap under 6G; wait and tell the SM"; exit 5; }
UNITP=gap-h264-$$
UPART=none
CAP="systemd-run --user --scope -q --unit=\${UNITP}-\${UPART} -p MemoryMax=${MEMG}G -p MemorySwapMax=0"
( while sleep 10; do
    A=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo)
    if [ "$A" -lt 1572864 ]; then
        for U in $UNITP-red $UNITP-modules $UNITP-mutants; do
            if systemctl --user is-active -q "$U.scope"; then
                echo "MEMORY STOP $(date +%T): available ${A} kB; stopping $U.scope" >> $E/memory_watchdog.txt
                systemctl --user stop "$U.scope"
            fi
        done
    fi
  done ) &
WATCHDOG=$!
export PYTHONFAULTHANDLER=1
export EXEMPT_EMAIL_DOMAINS=
[ "$(git rev-parse HEAD)" = "$(git rev-parse $TIP)" ] || { echo "not at $TIP"; exit 1; }
git diff --quiet HEAD || { echo "tree not clean"; exit 1; }
[ "$STEP" = 1 ] || { echo "only step 1 exists"; exit 1; }
mkdir -p $E

# Three code files to take back; plus the prompt rearrangement.
TAKE_BACK="ai_processor/services.py AutoGrader/settings.py ai_processor/extraction_schemas.py"
PROMPT_4_BASE="ai_processor/ANSWERS_EXTRACTION_PROMPT_HTML_4.txt"
PROMPT_5_TIP="ai_processor/ANSWERS_EXTRACTION_PROMPT_HTML_5.txt"
ARCHIVED_4_TIP="ai_processor/prompts_archive/ANSWERS_EXTRACTION_PROMPT_HTML_4.txt"
# Restore list: everything tracked at the tip (the prompt at its base location was deleted).
RESTORE="ai_processor/services.py AutoGrader/settings.py ai_processor/extraction_schemas.py $PROMPT_5_TIP $ARCHIVED_4_TIP"

restore_tree() {
    kill $WATCHDOG 2>/dev/null
    git checkout HEAD -- $RESTORE 2>/dev/null
    git reset HEAD -- $PROMPT_4_BASE 2>/dev/null
    rm -f $PROMPT_4_BASE 2>/dev/null
    find ai_processor AutoGrader -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null
}
trap restore_tree EXIT

summary() { grep -E "^(Ran |OK|FAILED \(|(FAIL|ERROR): )" "$1" | cut -c1-200 | head -${2:-12}; }

dropmut() { python manage.py shell --settings=settings_worktree_mut -c "
from django.conf import settings
from django.db import connection
name = settings.DATABASES['default']['TEST']['NAME']
with connection._nodb_cursor() as c:
    c.execute('DROP DATABASE IF EXISTS \"%s\"' % name)
print('dropped', name)" < /dev/null > $E/.dropmut.out 2>&1; tail -1 $E/.dropmut.out; rm -f $E/.dropmut.out; }

OWN="ai_processor.tests_h264_numbered_offtopic_answers ai_processor.tests_blank_verification"
NEAR="ai_processor"
GUARDS="AutoGrader.tests_cache_bespoke_1114 AutoGrader.tests_no_wildcard_invalidation AutoGrader.tests_cache_invalidation_coverage AutoGrader.tests_migration_rollback_defaults AutoGrader.tests_migration_safety_check AutoGrader.tests_redis_test_isolation AutoGrader.tests_beat_health AutoGrader.tests_beat_locks AutoGrader.tests_management_commands_are_commands AutoGrader.tests_no_pii_in_logs AutoGrader.tests_log_scrubbing AutoGrader.tests_sentry_scrubbing AutoGrader.tests_redis_hygiene_databases AutoGrader.tests_redis_hygiene AutoGrader.tests_patient_test_stream AutoGrader.tests_pool_worker_sigterm AutoGrader.tests_student_feedback_guard AutoGrader.tests_submission_audience_guard AutoGrader.tests_student_classmates_guard AutoGrader.tests_network_guard"
echo "load at start: $(cat /proc/loadavg)"

echo "=== 0. the red run: $OWN against the code as at $BASE (own DB) $(date +%T)"
# Take three code files back, restore prompt 4 to its base location, remove prompt 5 and the archive.
git checkout $BASE -- $TAKE_BACK $PROMPT_4_BASE
rm -f $PROMPT_5_TIP $ARCHIVED_4_TIP
find ai_processor AutoGrader -name __pycache__ -type d -prune -exec rm -rf {} +
UPART=red
eval "PYTHONDONTWRITEBYTECODE=1 $CAP nice -n 10 timeout -k 60 1800 python manage.py test --noinput --verbosity 2 --settings=settings_worktree_mut $OWN" < /dev/null > $E/red_run_code_as_at_base.txt 2>&1
RED_EXIT=$?
echo "red run exit=$RED_EXIT (non-zero expected)"
RF=$E/red_run_code_as_at_base.txt
RC=/home/bond-servant-in-training/Documents/Projects/GAP-builder-scripts/reason_check.py
RED_OK=1
[ "$RED_EXIT" -ne 0 ] || RED_OK=0
grep -qE "^OK" $RF && RED_OK=0
grep -qE "^Ran 61 tests in" $RF || { echo "red run: no 'Ran 61 tests' line"; RED_OK=0; }
grep -qE "unittest.loader._FailedTest" $RF && { echo "red run: a module failed to load"; RED_OK=0; }
# RULE 22: each test below is red for its WRITTEN reason, in every one of its blocks.
# At the base (b199eed6): pile-splitting code does not exist, so the H-264 tests either hit call_count==0,
# get AttributeError on None.kwargs, or see the old cap-skip behaviour. The prompt is version 4.
r() { python -I $RC $RF "$1" "$2" || RED_OK=0; }
# TheBetaPaperTest
r test_each_answer_goes_under_its_own_number "0 != 1"
r test_the_flags_are_six_off_topic_and_the_upload_mismatch "ANSWER_NOT_FOUND"
r test_only_the_pile_and_the_numbers_it_names_are_asked_about "'NoneType'"
r test_the_note_says_where_each_answer_was_put "'NoneType'"
# ThePileIsKeptUnlessEverySectionCameBackTest
r test_question_1_missing_from_the_reply "I studied the desert"
r test_some_sections_missing_from_the_reply "I studied the desert"
r test_a_failed_or_empty_second_reading_changes_nothing "0 != 1"
# WhatCountsAsAPileTest (9 subTests in the numbering test, each 0 != 1)
r test_each_way_of_numbering_at_the_start_of_a_paragraph_or_line "0 != 1"
r test_the_numbers_need_not_start_at_1 "0 != 1"
r test_a_pile_in_the_wrong_form_counts "0 != 1"
r test_an_outside_number_does_not_split_a_section "'NoneType'"
r test_a_pile_under_a_later_question "'NoneType'"
# TheRecheckCapTest
r test_the_beta_paper_has_its_first_12_blanks_re_checked "0 != 1"
r test_the_rest_are_logged_as_not_checked "skipping the re-read"
r test_the_lowest_numbered_whatever_the_order "'NoneType'"
r test_a_finding_for_a_question_not_asked_about_is_not_applied "blank_verification"
# TheWholeReadingTest
r test_the_beta_reply_ends_with_six_answers_and_the_upload_flag "0 != 2"
# ThePromptTest
r test_the_prompt_has_the_numbered_paper_example "not found"
r test_the_prompt_is_version_5_and_version_4_is_archived "False is not true"
# tests_blank_verification (the cap test that H-264 rewrote)
r test_too_many_blanks_re_reads_the_lowest_numbered_cap "0 != 1"
# Block count: 22 FAIL + 6 ERROR in h264, 1 FAIL in blank_verification = 29.
BLOCKS=$(grep -cE '^(FAIL|ERROR): ' $RF)
[ "$BLOCKS" = 29 ] || { echo "red run: not exactly 29 FAIL/ERROR blocks ($BLOCKS)"; RED_OK=0; }
if [ "$RED_OK" != 1 ]; then
    echo "STOP (exit 4): the red run is not what was written"
    git checkout HEAD -- $RESTORE 2>/dev/null; git reset HEAD -- $PROMPT_4_BASE 2>/dev/null; rm -f $PROMPT_4_BASE
    find ai_processor AutoGrader -name __pycache__ -type d -prune -exec rm -rf {} +
    exit 4
fi
echo "red run is as written: non-zero exit, no OK line, the module loaded, 29 blocks red for their written reasons and no other"
git checkout HEAD -- $RESTORE
git reset HEAD -- $PROMPT_4_BASE 2>/dev/null
rm -f $PROMPT_4_BASE
find ai_processor AutoGrader -name __pycache__ -type d -prune -exec rm -rf {} +
git diff --quiet HEAD -- . ":!$E" && [ -z "$(git status --porcelain -- . ":!$E" | grep -v settings_worktree_mut.py)" ] && echo "source restored" || { echo "STOP: not restored"; exit 3; }
grep -E "^(Ran |OK|FAILED \\()" $RF | cut -c1-200
dropmut

echo "=== 1a. makemigrations --check $(date +%T)"
python manage.py makemigrations --check --dry-run --settings=settings_worktree < /dev/null > $E/makemigrations_check.txt 2>&1; MM=$?; echo "makemigrations exit=$MM (0 = no changes)"; tail -1 $E/makemigrations_check.txt
[ "$MM" = 0 ] || { echo "STOP: makemigrations reports changes"; exit 2; }

echo "=== 1b. the seam, by program $(date +%T)"
git diff --name-only $BASE HEAD > $E/seam_changed_files.txt
cat $E/seam_changed_files.txt
# The secrets baseline moves with the line numbers it records (detect-secrets rewrites it at commit time): allowed
# only when every changed line is a line_number or generated_at.
if grep -qx '.secrets.baseline' $E/seam_changed_files.txt; then
    git diff $BASE HEAD -- .secrets.baseline | grep -E '^[+-] ' | grep -vE '^[+-] +"(line_number|generated_at)": ' > $E/.baseline_other
    [ -s $E/.baseline_other ] && { echo "STOP: .secrets.baseline changed beyond line numbers"; cat $E/.baseline_other; rm -f $E/.baseline_other; exit 2; }
    rm -f $E/.baseline_other
    echo ".secrets.baseline: line numbers and generated_at only"
fi
rm -rf $E/.base && mkdir -p $E/.base && git archive $BASE ai_processor AutoGrader | tar -x -C $E/.base
python -I $E/seam_check.py $E/.base . > $E/seam_code.txt 2>&1; SEAM=$?; rm -rf $E/.base
cat $E/seam_code.txt
[ "$SEAM" = 0 ] || { echo "STOP: the seam check failed"; exit 2; }

echo "=== 1. own modules + near (ai_processor) + guards at $(git rev-parse --short=8 HEAD) $(date +%T)"
UPART=modules
eval "$CAP nice -n 10 timeout -k 60 1800 python manage.py test --noinput --verbosity 2 --settings=settings_worktree $NEAR $GUARDS" < /dev/null > $E/modules_and_guards.txt 2>&1
echo "exit=$? $(date +%T)"; summary $E/modules_and_guards.txt
if grep -qE "^FAILED \\(" $E/modules_and_guards.txt || ! grep -qE "^OK" $E/modules_and_guards.txt; then echo "STOP: not green"; exit 2; fi

echo "=== 2. mutants: 16 (own DB, rules 17, 18 and 22) $(date +%T)"
UPART=mutants
( sleep 60; while systemctl --user is-active -q "$UNITP-mutants.scope"; do
    NEWEST=$(ls -t $E/mutant_logs/*.txt 2>/dev/null | head -1)
    if [ -n "$NEWEST" ] && [ $(( $(date +%s) - $(stat -c %Y "$NEWEST") )) -gt 300 ]; then
        echo "STALL STOP $(date +%T): $NEWEST silent over 300 s; stopping $UNITP-mutants.scope" >> $E/stall_watchdog.txt
        systemctl --user stop "$UNITP-mutants.scope"; break
    fi
    sleep 15
  done ) &
STALL=$!
eval "MUT_SETTINGS=settings_worktree_mut MUT_RESULTS=$E/mutation_results.json MUT_LOGS=$E/mutant_logs $CAP nice -n 10 timeout -k 60 3300 python $E/mutate.py" < /dev/null > $E/mutation_log.txt 2>&1
echo "mutants exit=$? $(date +%T)"; kill $STALL 2>/dev/null; cut -c1-300 $E/mutation_log.txt
git diff --quiet HEAD -- . ":!$E" && echo "source clean after mutants" || echo "STOP: source not clean after mutants"
dropmut
echo "load at end: $(cat /proc/loadavg)"
