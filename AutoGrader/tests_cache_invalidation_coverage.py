"""H-1 Phase 1: does every cached response get invalidated by something?

The companion to `tests_cache_collateral_damage.py`. That file proves we do
not invalidate too MUCH (non-cache keys survive). This one proves the other
half of the contract, which matters just as much:

  * over-invalidation  -> a performance bug (measured: a 25-row import wiped
    10,000 keys);
  * under-invalidation -> a CORRECTNESS bug. A cached response that no
    signal clears serves stale data until its TTL expires, and if the stale
    data crosses a tenant boundary it is a disclosure bug, not a freshness
    one.

Method: take the real cache-key formats written by the application and the
real wildcard patterns fired by the signal receivers, and ask **Redis
itself** which patterns match which keys. Redis glob semantics are not
Python's `fnmatch` and not a regex, so the matching is done by writing the
keys to a real Redis and running the project's own `delete_pattern` against
them - no reimplementation of the matcher, no approximation.

Tracked as H-1 in docs/HARDENING_BACKLOG.md. These tests describe CURRENT
behaviour: the ones marked as documenting a gap are expected to CHANGE when
H-1 lands, and they say so.
"""

from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from AutoGrader.test_cache import real_redis_caches

REDIS_CACHE = real_redis_caches("redis://127.0.0.1:6379/11")

U1 = "11111111-1111-1111-1111-111111111111"
U2 = "22222222-2222-2222-2222-222222222222"
SCHOOL = "33333333-3333-3333-3333-333333333333"
OBJ = "44444444-4444-4444-4444-444444444444"

#: Every cache key format the application actually writes, gathered by
#: reading each `cache.set` / `cache.add` site. Grouped by the app that
#: owns the write.
CACHE_KEYS = {
    # dashboard/views.py - 24 write sites, the largest cache surface
    "dash superadmin adoption": f"superadmins:user_id__{U1}:view__adoption",
    "dash superadmin usage": f"superadmins:user_id__{U1}:view__usage",
    "dash superadmin ai_perf": f"superadmins:user_id__{U1}:view__ai_performance",
    "dash superadmin scaling": f"superadmins:user_id__{U1}:view__scaling_signals",
    "dash superadmin schools": f"superadmins:user_id__{U1}:view__schools:1:20",
    "dash superadmin teachers": f"superadmins:user_id__{U1}:view__teachers:1:20",
    "dash superadmin students": f"superadmins:user_id__{U1}:view__students",
    "dash superadmin concurrency": f"superadmins:user_id__{U1}:view__concurrency",
    "dash schooladmin summary": f"schooladmins:user_id__{U1}:view__summary",
    "dash schooladmin at_risk": (
        f"schooladmins:user_id__{U1}:view__at_risk_trend:2026-01-01:2026-02-01"
    ),
    "dash schooladmin students": f"schooladmins:user_id__{U1}:view__students",
    "dash teacher overview": (
        f"teacheradmins:user_id__{U1}:instance__id__{OBJ}:view__overview"
    ),
    "dash teacher courses": (
        f"teacheradmins:user_id__{U1}:instance_id__{OBJ}:view__courses"
    ),
    "dash teacher assignments": (
        f"teacheradmins:user_id__{U1}:instance_id__{OBJ}:view__assignments"
    ),
    "dash teacher students": (
        f"teacheradmins:user_id__{U1}:instance_id__{OBJ}:view__students:1:20"
    ),
    # dashboard - the four SCHOOL-scoped keys that break the naming convention
    "dash teacher_performance": f"teacher_performance_{SCHOOL}_1_20",
    "dash teacher_detail": f"teacher_detail_{SCHOOL}_{U2}",
    "dash assignment_activity": f"assignment_activity_{SCHOOL}_2026",
    "dash department_overview": f"department_overview_{SCHOOL}",
    # other apps
    "students submission": f"studentsubmissions:user_id__{U1}:instance_id__{OBJ}",
    "users profile": f"user:user_id__{U1}",
    "users settings": f"settings:user_id__{U1}:view__my_settings",
    "classrooms my_courses": f"courses:user_id__{U1}",
    # UserCacheMixin writes "<model_name>s:user_id__<id>:..." for every
    # viewset that mixes it in - classrooms (School, Course, Session,
    # StudentCourse, Topic, CourseCategory), students (StudentSubmission),
    # users (CustomUser, Settings). One entry per distinct prefix, because
    # the prefix is what the patterns match on.
    "mixin courses list": f"courses:user_id__{U1}:query__abc123",
    "mixin courses detail": f"courses:user_id__{U1}:instance_id__{OBJ}",
    "mixin studentcourses list": f"studentcourses:user_id__{U1}:query__abc123",
    "mixin sessions list": f"sessions:user_id__{U1}:query__abc123",
    "mixin topics list": f"topics:user_id__{U1}:query__abc123",
    "mixin schools list": f"schools:user_id__{U1}:query__abc123",
    "mixin coursecategorys list": f"coursecategorys:user_id__{U1}:query__abc123",
    "mixin studentsubmissions list": (
        f"studentsubmissions:user_id__{U1}:query__abc123"
    ),
    "mixin customusers list": f"customusers:user_id__{U1}:query__abc123",
    "mixin settings list": f"settingss:user_id__{U1}:query__abc123",
    "assignments pdf": f"assignmentpdf:1:{OBJ}:student:stamp",
    "ai grading answer": "grading_answer_cache:deadbeefcafe",
}

#: Every wildcard pattern fired by a signal receiver, across all four
#: signal modules (classrooms, users, students, assignments).
INVALIDATION_PATTERNS = [
    "*superadmin*",
    "*schooladmin*",
    "*teacheradmin*",
    "*studentadmin*",
    "*user*",
    "*school*",
    "*course*",
    "*studentcourse*",
    "*settings*",
    "schools:*",
    "sessions:*",
    "courses:*",
    "studentcourses:*",
    "topics:*",
    "assignments:*",
    "studentsubmissions:*",
]


@override_settings(CACHES=REDIS_CACHE)
class InvalidationCoverageTests(SimpleTestCase):
    """Which cache keys can any signal actually reach?"""

    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    def _patterns_matching(self, key):
        """Ask Redis which patterns match `key`, one pattern at a time."""
        matched = []
        for pattern in INVALIDATION_PATTERNS:
            cache.set(key, "x", 60)
            cache.delete_pattern(pattern)
            if cache.get(key) is None:
                matched.append(pattern)
        cache.delete(key)
        return matched

    def coverage(self):
        return {
            label: self._patterns_matching(key) for label, key in CACHE_KEYS.items()
        }

    def test_every_cache_key_is_reachable_by_some_invalidation(self):
        """DOCUMENTS A GAP - expected to change when H-1 lands.

        A key matched by no pattern is never invalidated by any mutation.
        It goes stale the moment its underlying data changes and stays stale
        until the TTL expires.
        """
        uncovered = {
            label: CACHE_KEYS[label]
            for label, matches in self.coverage().items()
            if not matches
        }

        self.assertEqual(
            uncovered,
            {
                "dash teacher_performance": f"teacher_performance_{SCHOOL}_1_20",
                "dash teacher_detail": f"teacher_detail_{SCHOOL}_{U2}",
                "dash assignment_activity": f"assignment_activity_{SCHOOL}_2026",
                "dash department_overview": f"department_overview_{SCHOOL}",
                "assignments pdf": f"assignmentpdf:1:{OBJ}:student:stamp",
                "ai grading answer": "grading_answer_cache:deadbeefcafe",
            },
            "the set of never-invalidated cache keys changed. If H-1 has "
            "landed, this test should now expect an EMPTY dict for the four "
            "dashboard keys. The two non-dashboard entries are invalidated "
            "by their own dedicated helpers, not by signals - see the test "
            "below.",
        )

    def test_the_four_dashboard_keys_are_ttl_only(self):
        """These are the real finding: school-scoped dashboard responses
        that no mutation clears.

        `teacher_performance_<school>`, `teacher_detail_<school>_<teacher>`,
        `assignment_activity_<school>_<year>` and
        `department_overview_<school>` do not use the
        `<entity>:user_id__<id>` convention every pattern is written
        against, so no signal reaches them. A teacher's assignments,
        grades or roster can change and these keep serving the previous
        numbers for their full TTL (300-900s).
        """
        for label in [
            "dash teacher_performance",
            "dash teacher_detail",
            "dash assignment_activity",
            "dash department_overview",
        ]:
            self.assertEqual(
                self._patterns_matching(CACHE_KEYS[label]),
                [],
                f"{label} is now reachable by a pattern - update H-1",
            )

    def test_the_pdf_and_grading_caches_have_their_own_invalidation(self):
        """Not a gap: these two are deliberately outside the signal scheme.

        `assignments/pdf_cache.py` clears
        `assignmentpdf:<version>:<assignment id>:*` itself, and the grading
        answer cache is content-addressed - its key is a digest of the
        input, so a change of input is a change of key and there is nothing
        to invalidate. Asserted so the distinction is recorded rather than
        assumed.
        """
        self.assertEqual(self._patterns_matching(CACHE_KEYS["assignments pdf"]), [])
        self.assertEqual(self._patterns_matching(CACHE_KEYS["ai grading answer"]), [])

        cache.set(CACHE_KEYS["assignments pdf"], "pdf", 60)
        cache.delete_pattern(f"assignmentpdf:1:{OBJ}:*")
        self.assertIsNone(
            cache.get(CACHE_KEYS["assignments pdf"]),
            "the PDF cache's own invalidation no longer matches its key",
        )

    def test_over_invalidation_one_users_key_is_cleared_by_another(self):
        """The over-invalidation half, stated as a property.

        User 1's cached page is destroyed by a pattern fired by activity on
        user 2's data, because "*user*" is not scoped to a user at all.
        This is what makes a 25-row import flush the whole keyspace.
        """
        key_u1 = f"courses:user_id__{U1}:query__abc"
        key_u2 = f"courses:user_id__{U2}:query__abc"
        cache.set(key_u1, "user one", 60)
        cache.set(key_u2, "user two", 60)

        cache.delete_pattern("*user*")

        self.assertIsNone(cache.get(key_u1))
        self.assertIsNone(
            cache.get(key_u2),
            "if this survives, invalidation has become user-scoped - H-1 "
            "has landed and this test should be rewritten to assert "
            "isolation instead",
        )

    def test_how_many_patterns_match_a_single_key(self):
        """Redundancy, measured. Each extra match is a separate full
        keyspace SCAN deleting a key an earlier pattern already deleted."""
        redundant = {
            label: matches
            for label, matches in self.coverage().items()
            if len(matches) > 1
        }
        worst = max((len(m) for m in redundant.values()), default=0)
        # Measured baseline: "studentcourses:user_id__<id>:query__<md5>" is
        # matched by *course*, *studentcourse*, *user* AND studentcourses:*
        # - four separate full keyspace SCANs, three of them deleting a key
        # an earlier one already deleted.
        self.assertGreaterEqual(
            worst,
            4,
            "expected at least one key matched by 4+ patterns; if this "
            "dropped, the pattern set was narrowed - re-measure H-1's "
            "baseline",
        )
