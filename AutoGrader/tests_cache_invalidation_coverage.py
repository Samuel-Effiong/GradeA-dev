"""H-1: does every cached response get invalidated by something?

The companion to `tests_cache_collateral_damage.py`. That file proves we do
not invalidate too MUCH (non-cache keys survive). This one proves the other
half of the contract, which matters just as much:

  * over-invalidation  -> a performance bug (measured: a 25-row import wiped
    10,000 keys);
  * under-invalidation -> a CORRECTNESS bug. A cached response that nothing
    invalidates serves stale data until its TTL expires, and if the stale
    data crosses a tenant boundary it is a disclosure bug, not a freshness
    one.

INVERTED in H-1 step 4. This file used to ask Redis which legacy wildcard
PATTERNS matched which key formats, and pinned the four school dashboards
no pattern reached. The wildcards are gone, so the question is now: is every
cache family reachable by its GENERATION SCOPE? Three layers:

1. **The key map is the code.** `FAMILIES` names every response-cache
   family and the scopes its key embeds. It is compared, both ways, with
   every `versioned_key(...)` call in non-test code (read by AST), so a new
   family, a dropped scope or a stale map entry fails here.
2. **Nothing is cached under a raw key.** Every `cache.set`/`add`/
   `get_or_set`/`set_many` in non-test code must write a key built by
   `versioned_key` (directly, or through `UserCacheMixin.get_cache_key`),
   or be one of the listed non-response caches, each with its reason. A raw
   response key is exactly what the status-summary family was until step 4:
   reachable only by a wildcard, so silently stale once the wildcard went.
3. **Each scope really invalidates its family, and only it** - against real
   Redis: bumping any one of a family's scopes makes the entry unreachable,
   and bumping the same scope for another entity does not.

Tracked as H-1 in docs/HARDENING_BACKLOG.md; evidence in
docs/evidence/H1_STEP4_WILDCARD_REMOVAL_EVIDENCE.md.
"""

import ast
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.cache import cache
from django.test import SimpleTestCase, override_settings

from AutoGrader.cache_generation import (
    SCOPE_ANY_SCHOOL,
    SCOPE_ANY_USER,
    SCOPE_COURSE,
    SCOPE_GLOBAL,
    SCOPE_SCHOOL,
    SCOPE_USER,
    SINGLETON_SCOPES,
    bump_generation,
    versioned_key,
)
from AutoGrader.test_cache import real_redis_caches
from AutoGrader.tests_cache_generation import redis_commands_sent_by_this_process

REDIS_CACHE = real_redis_caches("redis://127.0.0.1:6379/11")

REPO = Path(settings.BASE_DIR)

U1 = "11111111-1111-1111-1111-111111111111"
U2 = "22222222-2222-2222-2222-222222222222"
OBJ = "44444444-4444-4444-4444-444444444444"

#: Every response-cache family: its key template as the code writes it
#: (`<>` marks an interpolated value) and the generation scopes its key
#: embeds, in order. The scope names are the `AutoGrader.cache_generation`
#: constant names, as they appear in the code.
FAMILIES = {
    # classrooms/views.py - my_courses (family 11)
    "courses:user_id__<>": ("SCOPE_USER", "SCOPE_GLOBAL"),
    # Superadmin dashboards, families 15-22.
    "superadmins:user_id__<>:view__adoption": ("SCOPE_GLOBAL",),
    "superadmins:user_id__<>:view__usage": ("SCOPE_GLOBAL",),
    "superadmins:user_id__<>:view__ai_performance": ("SCOPE_GLOBAL",),
    "superadmins:user_id__<>:view__scaling_signals": ("SCOPE_GLOBAL",),
    "superadmins:user_id__<>:view__schools:<>:<>": ("SCOPE_ANY_SCHOOL",),
    "superadmins:user_id__<>:view__teachers:<>:<>": ("SCOPE_ANY_USER",),
    "superadmins:user_id__<>:view__students": ("SCOPE_GLOBAL",),
    # dashboard/views.py - school admin (23-25)
    "schooladmins:user_id__<>:view__summary:session__<>": (
        "SCOPE_USER",
        "SCOPE_SCHOOL",
    ),
    "schooladmins:user_id__<>:view__at_risk_trend:<>:<>": (
        "SCOPE_USER",
        "SCOPE_SCHOOL",
    ),
    "schooladmins:user_id__<>:view__students:session__<>": (
        "SCOPE_USER",
        "SCOPE_SCHOOL",
    ),
    # dashboard/views.py - school-scoped dashboards (30-33)
    "dashboards:school_id__<>:view__teacher_performance:<>:<>:session__<>": (
        "SCOPE_SCHOOL",
    ),
    "dashboards:school_id__<>:view__teacher_detail:<>:session__<>": (
        "SCOPE_SCHOOL",
        "SCOPE_USER",
    ),
    "dashboards:school_id__<>:view__assignment_activity:<>:session__<>": (
        "SCOPE_SCHOOL",
    ),
    "dashboards:school_id__<>:view__department_overview:session__<>": ("SCOPE_SCHOOL",),
    # Teacher dashboards, families 26-29.
    "teacheradmins:user_id__<>:instance__id__<>:view__overview": ("SCOPE_USER",),
    "teacheradmins:user_id__<>:instance_id__<>:view__courses": ("SCOPE_USER",),
    "teacheradmins:user_id__<>:instance_id__<>:view__assignments": ("SCOPE_USER",),
    "teacheradmins:user_id__<>:instance_id__<>:view__students:<>:<>": ("SCOPE_USER",),
    # dashboard/views.py - student (G2, and status-summary: H-1 step 4)
    "studentadmins:user_id__<>:instance_id__<>:view__summary": ("SCOPE_USER",),
    "studentadmins:user_id__<>:view__assignments:<>:<>": ("SCOPE_USER",),
    "studentadmins:user_id__<>:view__overview": ("SCOPE_USER",),
    "studentadmins:user_id__<>:view__status_summary:course__<>": ("SCOPE_USER",),
    "studentadmins:user_id__<>:view__status_summary:all": ("SCOPE_USER",),
    # students/views.py - submission retrieve (family 12)
    "studentsubmissions:user_id__<>:instance_id__<>": ("SCOPE_USER",),
    # users/views.py - me and my_settings (families 13-14)
    "user:user_id__<>": ("SCOPE_USER",),
    "settings:user_id__<>:view__my_settings": ("SCOPE_USER",),
    # users/mixins.py UserCacheMixin (families 1-10 and the assignment
    # viewsets): `<model>s:user_id__<>:{query__<md5>|instance_id__<pk>}`,
    # one versioned_key call serving every viewset that mixes it in.
    "<UserCacheMixin base>": ("SCOPE_USER",),
}

#: Mixin viewsets whose key also carries scopes returned by
#: `extra_cache_scopes` (users/mixins.py appends them after the viewer's
#: own). The scan above reads the mixin's `*extra` as nothing, so these are
#: listed here, asserted against every override in the code, and exercised
#: in layer 3 as families of their own. CourseViewSet: a student's course
#: list and detail carry each course's `crs`, which an enrolment bumps in
#: place of every classmate's own generation (stage 3 rework, cc14bb0).
MIXIN_EXTRA_SCOPES = {
    "classrooms/views.py::CourseViewSet": ("SCOPE_COURSE",),
}

#: FAMILIES plus each MIXIN_EXTRA_SCOPES variant, for the Redis layer.
ALL_FAMILIES = {
    **FAMILIES,
    **{
        f"<UserCacheMixin base> + {site}": ("SCOPE_USER", *extra)
        for site, extra in MIXIN_EXTRA_SCOPES.items()
    },
}

#: The viewsets `UserCacheMixin` serves, as the key prefix each produces.
#: Asserted against the code so a new mixin user is noticed.
MIXIN_PREFIXES = {
    "assignments",
    "assignmentgenerationsessions",
    "schools",
    "courses",
    "sessions",
    "studentcourses",
    "coursecategorys",
    "topics",
    "studentsubmissions",
    "customusers",
    "settingss",
}

#: Cache writes that are NOT response caches, so are deliberately not
#: generation-versioned. (path, count of write calls, why).
NON_RESPONSE_CACHE_WRITES = {
    "assignments/pdf_cache.py": (
        1,
        "rendered PDFs: keyed by updated_at, own exact-prefix invalidation "
        "(plan §2; design family 34)",
    ),
    "ai_processor/grading_cache.py": (
        1,
        "content-addressed grading answers (design family 35)",
    ),
    "AutoGrader/cache_generation.py": (1, "the generation counter itself"),
    "AutoGrader/health.py": (1, "healthcheck probe"),
    "billing/overage_pricing.py": (1, "Stripe price lookup, TTL"),
    "billing/stripe_service.py": (3, "Stripe portal config id; lock"),
    "billing/views.py": (2, "idempotency locks"),
    "billing/license_service.py": (2, "idempotency locks"),
    # billing/tasks.py's own task lock (reconcile_stripe_prices) moved onto
    # AutoGrader/beat_locks.py (H-65), which writes through the raw Redis
    # client (SET NX PX, Lua compare-and-delete) under "beat-lock:" keys.
    "users/middleware.py": (1, "activity heartbeat throttle"),
    "users/throttling.py": (
        5,
        "register-student failure budget (H-47); /auth/verify per-address "
        "budget + lock (H-53)",
    ),
}

SCOPE_CONSTANTS = {
    "SCOPE_USER": SCOPE_USER,
    "SCOPE_SCHOOL": SCOPE_SCHOOL,
    "SCOPE_COURSE": SCOPE_COURSE,
    "SCOPE_GLOBAL": SCOPE_GLOBAL,
    "SCOPE_ANY_SCHOOL": SCOPE_ANY_SCHOOL,
    "SCOPE_ANY_USER": SCOPE_ANY_USER,
}

CACHE_WRITE_METHODS = {"set", "add", "get_or_set", "set_many"}


def production_python_files():
    for path in sorted(REPO.rglob("*.py")):
        rel = path.relative_to(REPO).as_posix()
        name = path.name
        if (
            rel.startswith(("docs/", "static/", "media/", "node_modules/"))
            or "/migrations/" in rel
            or "/tests/" in rel
            or name.startswith(("test_", "tests_", "tests."))
            or name == "tests.py"
            or rel.startswith(".")
            or "site-packages" in rel
        ):
            continue
        yield rel, path


def _template(node):
    if isinstance(node, ast.JoinedStr):
        return "".join(
            str(v.value) if isinstance(v, ast.Constant) else "<>" for v in node.values
        )
    if isinstance(node, ast.Constant):
        return str(node.value)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _template(node.left) + _template(node.right)
    return None


def _call_name(call):
    func = call.func
    return getattr(func, "id", None) or getattr(func, "attr", None)


def versioned_key_calls():
    """(path, line, template, scope names) for every versioned_key call."""
    found = []
    for rel, path in production_python_files():
        if rel == "AutoGrader/cache_generation.py":
            continue  # the definition and its docstring example
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and _call_name(node) == "versioned_key"):
                continue
            template = _template(node.args[0])
            if template is None and rel == "users/mixins.py":
                template = "<UserCacheMixin base>"
            scope_list = node.args[1]
            assert isinstance(scope_list, (ast.List, ast.Tuple)), (rel, node.lineno)
            scopes = tuple(
                elt.elts[0].id
                for elt in scope_list.elts
                if isinstance(elt, ast.Tuple) and isinstance(elt.elts[0], ast.Name)
            )
            found.append((rel, node.lineno, template, scopes))
    return found


def extra_cache_scope_overrides():
    """{"path::Class": scope names} for every `extra_cache_scopes` override
    outside the mixin's own default."""
    found = {}
    for rel, path in production_python_files():
        if rel == "users/mixins.py":
            continue
        for node in ast.walk(ast.parse(path.read_text())):
            if not isinstance(node, ast.ClassDef):
                continue
            for item in node.body:
                if isinstance(item, ast.FunctionDef) and item.name == (
                    "extra_cache_scopes"
                ):
                    found[f"{rel}::{node.name}"] = tuple(
                        sorted(
                            {
                                n.id
                                for n in ast.walk(item)
                                if isinstance(n, ast.Name) and n.id in SCOPE_CONSTANTS
                            }
                        )
                    )
    return found


def _enclosing_functions(tree):
    parents = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node
    return parents


def raw_cache_writes():
    """Every cache write whose key is NOT built by versioned_key (directly or
    via UserCacheMixin.get_cache_key), grouped by file."""
    raw = {}
    for rel, path in production_python_files():
        tree = ast.parse(path.read_text())
        parents = _enclosing_functions(tree)
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in CACHE_WRITE_METHODS
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "cache"
                and node.args
            ):
                continue
            key = node.args[0]
            versioned = False
            if isinstance(key, ast.Call) and _call_name(key) in (
                "versioned_key",
                "get_cache_key",
            ):
                versioned = True
            elif isinstance(key, ast.Name):
                scope = parents.get(node)
                while scope is not None and not isinstance(
                    scope, (ast.FunctionDef, ast.AsyncFunctionDef)
                ):
                    scope = parents.get(scope)
                nodes = ast.walk(scope) if scope is not None else []
                assigns = [
                    a
                    for a in nodes
                    if isinstance(a, ast.Assign)
                    and any(
                        isinstance(t, ast.Name) and t.id == key.id for t in a.targets
                    )
                ]
                versioned = bool(assigns) and all(
                    isinstance(a.value, ast.Call)
                    and _call_name(a.value) in ("versioned_key", "get_cache_key")
                    for a in assigns
                )
            if not versioned:
                raw.setdefault(rel, []).append(node.lineno)
    return raw


class KeyMapIsTheCodeTests(SimpleTestCase):
    """Layers 1 and 2: static, no Redis needed."""

    def test_the_key_map_matches_every_versioned_key_call(self):
        in_code = {}
        for rel, line, template, scopes in versioned_key_calls():
            self.assertIsNotNone(template, f"{rel}:{line}: unreadable key template")
            in_code.setdefault(template, set()).add(scopes)

        unmapped = sorted(set(in_code) - set(FAMILIES))
        stale = sorted(set(FAMILIES) - set(in_code))
        self.assertEqual(unmapped, [], "cache families missing from FAMILIES")
        self.assertEqual(stale, [], "FAMILIES lists families the code no longer has")
        for template, scopes in in_code.items():
            self.assertEqual(
                scopes,
                {FAMILIES[template]},
                f"{template}: the code's scopes differ from the key map",
            )

    def test_every_extra_cache_scopes_override_is_listed(self):
        self.assertEqual(extra_cache_scope_overrides(), MIXIN_EXTRA_SCOPES)

    def test_the_mixin_appends_the_extra_scopes_to_its_key(self):
        """Otherwise MIXIN_EXTRA_SCOPES would describe keys nobody builds."""
        tree = ast.parse((REPO / "users/mixins.py").read_text())
        starred = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and _call_name(node) == "versioned_key"
            and any(
                isinstance(e, ast.Starred) for e in getattr(node.args[1], "elts", [])
            )
        ]
        self.assertEqual(len(starred), 1, "one mixin key must spread the extras")

    def test_every_family_embeds_at_least_one_generation(self):
        for template, scopes in FAMILIES.items():
            self.assertTrue(scopes, template)
            for name in scopes:
                self.assertIn(name, SCOPE_CONSTANTS, f"{template}: {name}")

    def test_the_status_summary_family_is_versioned_on_the_student(self):
        """H-1 step 4: the one family that was still raw is on generations."""
        for variant in ("course__<>", "all"):
            self.assertEqual(
                FAMILIES[f"studentadmins:user_id__<>:view__status_summary:{variant}"],
                ("SCOPE_USER",),
            )

    def test_every_mixin_viewset_is_listed(self):
        prefixes = set()
        for rel in (
            "assignments/views.py",
            "classrooms/views.py",
            "students/views.py",
            "users/views.py",
        ):
            tree = ast.parse((REPO / rel).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and any(
                    getattr(base, "id", None) == "UserCacheMixin" for base in node.bases
                ):
                    for stmt in node.body:
                        if isinstance(stmt, ast.Assign) and any(
                            getattr(t, "id", None) == "queryset" for t in stmt.targets
                        ):
                            # `Model.objects.all()`: the model is the first
                            # plain name in the expression.
                            model_name = next(
                                n.id
                                for n in ast.walk(stmt.value)
                                if isinstance(n, ast.Name)
                            )
                            prefixes.add(f"{model_name.lower()}s")
        self.assertEqual(prefixes, MIXIN_PREFIXES)

    def test_nothing_is_cached_under_a_raw_key(self):
        raw = raw_cache_writes()
        self.assertEqual(
            {rel: len(lines) for rel, lines in raw.items()},
            {rel: count for rel, (count, _) in NON_RESPONSE_CACHE_WRITES.items()},
            "a cache write uses a key not built by versioned_key. A raw "
            "response key is reachable by no generation bump - with the "
            "wildcards gone it is stale for its whole TTL. Version it, or, "
            "if it is not a response cache, list it with its reason in "
            "NON_RESPONSE_CACHE_WRITES.",
        )


# ---------------------------------------------------------------------------
# H-73: writes through the RAW Redis client.
#
# Layer 2 sees only calls on a name `cache` (cache.set/add/...). A write
# through the django-redis client itself - get_redis_connection(), or
# cache.client / cache.client.get_client() - is invisible to it: no
# versioned_key, no NON_RESPONSE_CACHE_WRITES entry, nothing. H-65's
# beat_locks helper was the first new such user. Every module that obtains
# a raw client, and every write it makes through one, is now counted here
# and must be listed with its reason; the list is compared both ways, so a
# new user fails and a stale entry fails.

#: Calls that hand back a raw Redis client (or connection pool).
RAW_CLIENT_SOURCES = {
    "get_redis_connection",
    "get_client",
    "Redis",
    "StrictRedis",
    "from_url",
    "ConnectionPool",
}

#: Redis commands that change data. In a module that obtains a raw client,
#: a call to any of these names counts as a write WHATEVER it is called on
#: (a pipeline, a lock, a client the data flow below can't follow). An
#: unrelated object's method of the same name (threading.Event.set) is
#: counted too: that only pins a larger number, and a change to it fails
#: loudly, which is the safe direction. APPEND and COPY are left out of this
#: by-name list only because list.append / dict.copy are everywhere; on a
#: client the data flow follows they still count (rule 2, the read
#: allow-list).
RAW_WRITE_METHODS = {
    "set",
    "setex",
    "setnx",
    "psetex",
    "getex",
    "mset",
    "msetnx",
    "getset",
    "getdel",
    "setrange",
    "setbit",
    "bitop",
    "bitfield",
    "incr",
    "incrby",
    "incrbyfloat",
    "decr",
    "decrby",
    "expire",
    "pexpire",
    "expireat",
    "pexpireat",
    "persist",
    "delete",
    "unlink",
    "rename",
    "renamenx",
    "move",
    "restore",
    "migrate",
    "eval",
    "evalsha",
    "fcall",
    "execute_command",
    "register_script",
    "script_load",
    "function_load",
    "lock",
    "hset",
    "hmset",
    "hsetnx",
    "hdel",
    "hincrby",
    "hincrbyfloat",
    "lpush",
    "rpush",
    "lpushx",
    "rpushx",
    "linsert",
    "lpop",
    "rpop",
    "blpop",
    "brpop",
    "lmove",
    "blmove",
    "rpoplpush",
    "brpoplpush",
    "lrem",
    "ltrim",
    "lset",
    "sadd",
    "srem",
    "spop",
    "smove",
    "sinterstore",
    "sunionstore",
    "sdiffstore",
    "zadd",
    "zrem",
    "zincrby",
    "zpopmin",
    "zpopmax",
    "bzpopmin",
    "bzpopmax",
    "zremrangebyscore",
    "zremrangebyrank",
    "zremrangebylex",
    "zunionstore",
    "zinterstore",
    "zrangestore",
    "pfadd",
    "pfmerge",
    "xadd",
    "xdel",
    "xtrim",
    "xack",
    "xclaim",
    "xgroup_create",
    "publish",
    "flushdb",
    "flushall",
}

#: What may be called on a client the data flow CAN follow without counting
#: as a write: the reads, and the plumbing (pipeline, execute - which only
#: sends what the counted calls queued - and the acquisition calls). Any
#: other method on such a client is a write, including a command redis-py
#: adds later.
RAW_READ_METHODS = {
    "get",
    "mget",
    "getrange",
    "strlen",
    "getbit",
    "bitcount",
    "exists",
    "ttl",
    "pttl",
    "type",
    "dump",
    "scan",
    "scan_iter",
    "keys",
    "dbsize",
    "hget",
    "hgetall",
    "hmget",
    "hkeys",
    "hvals",
    "hlen",
    "hexists",
    "lrange",
    "llen",
    "lindex",
    "smembers",
    "sismember",
    "scard",
    "srandmember",
    "zrange",
    "zrangebyscore",
    "zrevrange",
    "zscore",
    "zcard",
    "zcount",
    "zrank",
    "xrange",
    "xlen",
    "pfcount",
    "ping",
    "info",
    "time",
}
RAW_PLUMBING_METHODS = {"pipeline", "execute", "close", "reset"}

#: Modules that obtain a raw Redis client: (acquisitions, raw writes, why).
RAW_CLIENT_USERS = {
    "AutoGrader/beat_locks.py": (
        1,
        5,
        "H-65 Beat task locks: SET NX PX with a per-run token, Lua "
        "compare-and-delete/extend, and the fail-closed probe (4). Keys are "
        "under the cache's own prefix (lock_key -> cache.make_key); not a "
        "response cache. The 5th is the heartbeat's threading.Event.set(), "
        "counted by name",
    ),
    "AutoGrader/cache_generation.py": (
        1,
        4,
        "the pipelined generation bump (SET NX + INCR per counter): the "
        "generation counters themselves, not a response cache. The other 2 "
        "are cache-API cache.incr() calls, counted by name",
    ),
    "AutoGrader/redis_test_hygiene.py": (
        1,
        1,
        "test-only key hygiene (H-9 follow-up): unlinks dead and own "
        "gaplus-t<pid>: test prefixes across the test Redis's databases; "
        "never imported by production code",
    ),
    "AutoGrader/testing/beat_locks.py": (
        1,
        1,
        "test-only isolation: deletes this process's beat-lock keys before "
        "each test; never imported by production code",
    ),
}

_FUNCTION_NODES = (ast.FunctionDef, ast.AsyncFunctionDef)


def _callee(call):
    func = call.func
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _dotted(expr):
    """`a.b.c` for a Name/Attribute chain, else None."""
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        head = _dotted(expr.value)
        return f"{head}.{expr.attr}" if head else None
    return None


def _module_name(rel):
    return rel[: -len(".py")].replace("/", ".")


class _RawClientScan:
    """Where one module obtains a raw Redis client, and where it writes.

    Acquisitions: a call to a RAW_CLIENT_SOURCES function, or to a factory
    imported from another module (`from M import f`, then `f()`; or
    `import M` / `from P import M`, then `M.f()`).

    Writes, two rules (a call counted by both counts once):

    1. In a module with at least one acquisition, every call to a method
       named in RAW_WRITE_METHODS, whatever the receiver.
    2. Anywhere, every call on a CLIENT EXPRESSION whose method is not in
       RAW_READ_METHODS / RAW_PLUMBING_METHODS / RAW_CLIENT_SOURCES. A
       client expression is an acquisition, a local or imported factory
       call, `cache.client` / `cache._cache`, a pipeline of a client, or a
       name bound to one: by assignment (plain, annotated, walrus), `with
       ... as`, a `for` target, `self.<attr>`, or a parameter of a local
       function called with a client (positionally or by keyword). A name
       is looked up in its function, the functions enclosing it (a
       closure), then the module. A factory is a local function that
       returns or yields a client expression, or a tuple holding one.

    Documented limits (none is caught): a method reached through dynamic
    `getattr(client, name)` or dispatched from a string (except
    execute_command, which rule 1 counts); a client kept in a container
    (dict/list) or on an attribute of anything but `self` in a module with
    no acquisition; a client passed into another module as an argument; the
    django-redis wrapper reached under another name (`caches["x"].client`,
    `from django.core.cache import cache as c`); star-imports."""

    def __init__(self, source, imported_factories=(), module_factories=None):
        self.tree = ast.parse(source)
        self.parents = _enclosing_functions(self.tree)
        self.imported = set(imported_factories)
        self.module_factories = dict(module_factories or {})
        self.factories = set(self.imported)
        self.names = set()  # (scope, name); scope None = module
        self.attrs = set()  # "self.r"
        self.functions = {
            fn.name: fn for fn in ast.walk(self.tree) if isinstance(fn, _FUNCTION_NODES)
        }
        self._resolve()

    def scope_of(self, node):
        scope = self.parents.get(node)
        while scope is not None and not isinstance(scope, _FUNCTION_NODES):
            scope = self.parents.get(scope)
        return scope

    def _scopes(self, scope):
        while scope is not None:
            yield scope
            scope = self.scope_of(scope)
        yield None

    def is_factory_call(self, call):
        if isinstance(call.func, ast.Name):
            return call.func.id in self.factories
        if isinstance(call.func, ast.Attribute):
            owner = _dotted(call.func.value)
            return call.func.attr in self.module_factories.get(owner, ())
        return False

    def is_acquisition(self, call):
        return _callee(call) in RAW_CLIENT_SOURCES or (
            self.is_factory_call(call)
            and not (
                isinstance(call.func, ast.Name) and call.func.id not in self.imported
            )
        )

    def is_client(self, expr, scope):
        if isinstance(expr, ast.Call):
            if _callee(expr) in RAW_CLIENT_SOURCES or self.is_factory_call(expr):
                return True
            return (
                isinstance(expr.func, ast.Attribute)
                and expr.func.attr == "pipeline"
                and self.is_client(expr.func.value, scope)
            )
        if isinstance(expr, ast.Attribute):
            if _dotted(expr) in self.attrs:
                return True
            return (
                isinstance(expr.value, ast.Name)
                and expr.value.id == "cache"
                and expr.attr in ("client", "_cache")
            )
        if isinstance(expr, ast.Name):
            return any((s, expr.id) in self.names for s in self._scopes(scope))
        return False

    def _holds_client(self, expr, scope):
        if isinstance(expr, (ast.Tuple, ast.List)):
            return any(self._holds_client(e, scope) for e in expr.elts)
        return self.is_client(expr, scope)

    def _bind(self, scope, target):
        added = False
        for node in ast.walk(target):
            if isinstance(node, ast.Name) and (scope, node.id) not in self.names:
                self.names.add((scope, node.id))
                added = True
            elif (
                isinstance(node, ast.Attribute)
                and isinstance(node.value, ast.Name)
                and node.value.id == "self"
                and _dotted(node) not in self.attrs
            ):
                self.attrs.add(_dotted(node))
                added = True
        return added

    def _bind_call(self, call):
        """A local function called with a client binds that parameter."""
        fn = self.functions.get(call.func.id)
        added = False
        scope = self.scope_of(call)
        params = fn.args.posonlyargs + fn.args.args
        bound = list(zip(params, call.args, strict=False))
        bound += [
            (p, kw.value)
            for kw in call.keywords
            for p in params + fn.args.kwonlyargs
            if p.arg == kw.arg
        ]
        for param, value in bound:
            if self.is_client(value, scope) and (fn, param.arg) not in self.names:
                self.names.add((fn, param.arg))
                added = True
        return added

    def _step(self, node):
        scope = self.scope_of(node)
        if isinstance(node, ast.Assign) and self.is_client(node.value, scope):
            added = False
            for target in node.targets:  # every target binds: no short-circuit
                added |= self._bind(scope, target)
            return added
        if isinstance(node, ast.AnnAssign) and node.value is not None:
            if self.is_client(node.value, scope):
                return self._bind(scope, node.target)
        if isinstance(node, ast.NamedExpr) and self.is_client(node.value, scope):
            return self._bind(scope, node.target)
        if isinstance(node, (ast.With, ast.AsyncWith)):
            added = False
            for item in node.items:
                if item.optional_vars is not None and self.is_client(
                    item.context_expr, scope
                ):
                    added |= self._bind(scope, item.optional_vars)
            return added
        if isinstance(node, (ast.For, ast.AsyncFor)) and self.is_client(
            node.iter, scope
        ):
            return self._bind(scope, node.target)
        if isinstance(node, (ast.Return, ast.Yield)) and node.value is not None:
            fn = scope
            if fn is not None and fn.name not in self.factories:
                if self._holds_client(node.value, scope) or any(
                    isinstance(c, ast.Call) and _callee(c) in RAW_CLIENT_SOURCES
                    for c in ast.walk(node.value)
                ):
                    self.factories.add(fn.name)
                    return True
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id in self.functions
        ):
            return self._bind_call(node)
        return False

    def _resolve(self):
        changed = True
        while changed:
            changed = False
            for node in ast.walk(self.tree):  # every node steps: no short-circuit
                changed |= self._step(node)

    def local_factories(self):
        return self.factories - self.imported

    def counts(self):
        calls = [n for n in ast.walk(self.tree) if isinstance(n, ast.Call)]
        acquisitions = sorted(c.lineno for c in calls if self.is_acquisition(c))
        writes = []
        for call in calls:
            if not isinstance(call.func, ast.Attribute):
                continue
            method = call.func.attr
            by_name = bool(acquisitions) and method in RAW_WRITE_METHODS
            on_client = method not in (
                RAW_READ_METHODS | RAW_PLUMBING_METHODS | RAW_CLIENT_SOURCES
            ) and self.is_client(call.func.value, self.scope_of(call))
            if by_name or on_client:
                writes.append(call.lineno)
        return acquisitions, sorted(writes)


def raw_client_factories(tree):
    """Functions in `tree` that return or yield a raw client (beat_locks's
    `_redis`, redis_test_hygiene's `_clients` generator)."""
    return _RawClientScan(ast.unparse(tree)).local_factories()


def scan_raw_client(source, imported_factories=(), module_factories=None):
    """(acquisition lines, write lines) in `source`. See _RawClientScan."""
    return _RawClientScan(source, imported_factories, module_factories).counts()


def _imported_factories(tree, factories_by_module):
    """(names bound to an imported factory, {module alias: its factories})."""
    names, modules = set(), {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                bound = alias.asname or alias.name
                if alias.name in factories_by_module.get(node.module, ()):
                    names.add(bound)
                submodule = f"{node.module}.{alias.name}"
                if factories_by_module.get(submodule):
                    modules[bound] = factories_by_module[submodule]
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if factories_by_module.get(alias.name):
                    modules[alias.asname or alias.name] = factories_by_module[
                        alias.name
                    ]
    return names, modules


def raw_client_uses(files=None):
    """{file: (acquisitions, writes)} for every non-test module that obtains
    a raw Redis client or writes through one. `files` ((rel, path) pairs)
    defaults to every production module."""
    files = list(production_python_files() if files is None else files)
    trees = {rel: ast.parse(path.read_text()) for rel, path in files}
    factories_by_module = {
        _module_name(rel): raw_client_factories(tree) for rel, tree in trees.items()
    }
    uses = {}
    for rel, path in files:
        names, modules = _imported_factories(trees[rel], factories_by_module)
        acquisitions, writes = scan_raw_client(path.read_text(), names, modules)
        if acquisitions or writes:
            uses[rel] = (len(acquisitions), len(writes))
    return uses


#: v2's F1 (H-73 static review): appended to a listed module, these two
#: writes - a GETEX on a `with`-bound pipeline and a lock - left the count
#: unchanged at debd2ab.
F1_EXTRA_WRITES = (
    "\n\ndef _vf2_extra():\n"
    "    with _redis().pipeline() as p:\n"
    "        p.getex('k', ex=1)\n"
    "        p.execute()\n"
    "    _redis().lock('k2').acquire()\n"
)


class RawRedisClientTests(SimpleTestCase):
    """H-73: the raw client is the other door into Redis; layer 2 can't see
    through it, so it is counted here."""

    def test_every_raw_client_user_is_listed(self):
        self.assertEqual(
            raw_client_uses(),
            {rel: (acq, writes) for rel, (acq, writes, _) in RAW_CLIENT_USERS.items()},
            "a module obtains a raw Redis client or writes through one, and "
            "the count differs from RAW_CLIENT_USERS. The raw client "
            "bypasses the cache API, its key versioning and the "
            "raw-cache-write guard above. Use the cache API if you can; if "
            "not, list the module with its counts and the reason. An entry "
            "that no longer matches the code must be updated or removed.",
        )

    def test_every_entry_gives_a_reason(self):
        for rel, (_, _, why) in RAW_CLIENT_USERS.items():
            with self.subTest(rel=rel):
                self.assertGreater(len(why.split()), 3)

    def assert_counts(self, cases):
        for label, (source, acquisitions, writes) in cases.items():
            with self.subTest(label):
                found = scan_raw_client(source)
                self.assertEqual(
                    (len(found[0]), len(found[1])), (acquisitions, writes), found
                )

    def test_the_scanner_finds_every_shape_it_exists_to_catch(self):
        self.assert_counts(
            {
                "direct": ("get_redis_connection('default').set('k', 1)\n", 1, 1),
                "bound": (
                    "def f():\n    c = get_redis_connection()\n    c.setex('k', 5, 1)\n",
                    1,
                    1,
                ),
                "cache.client": (
                    "def f():\n    cache.client.get_client(write=True).eval('s', 0)\n",
                    1,
                    1,
                ),
                "cache.client wrapper": ("cache.client.set('k', 1)\n", 0, 1),
                "pipeline": (
                    "def f():\n"
                    "    c = cache.client.get_client(write=True)\n"
                    "    p = c.pipeline()\n"
                    "    p.set('k', 1)\n"
                    "    p.incr('k')\n"
                    "    p.execute()\n",
                    1,
                    2,
                ),
                "local factory": (
                    "def _r():\n    return get_redis_connection('default')\n"
                    "def g():\n    _r().delete('k')\n    x = _r()\n    x.expire('k', 1)\n",
                    1,
                    2,
                ),
                "redis-py": ("redis.Redis.from_url(u).hset('h', 'f', 1)\n", 1, 1),
                "yielded, looped and passed on": (
                    "def _clients():\n"
                    "    for db in (0, 1):\n"
                    "        yield db, redis.Redis.from_url(u, db=db)\n"
                    "def _unlink(client, keys):\n"
                    "    client.unlink(*keys)\n"
                    "def sweep():\n"
                    "    for _db, c in _clients():\n"
                    "        _unlink(c, ['k'])\n"
                    "        c.delete('j')\n",
                    1,
                    2,
                ),
            }
        )

    def test_a_write_name_counts_whatever_it_is_called_on(self):
        """Rule 1 (v2 F1): in a module that obtains a raw client, a write
        command counts even on a receiver the data flow can't follow."""
        self.assert_counts(
            {
                "in a container": (
                    "clients = {'a': get_redis_connection()}\n"
                    "clients['a'].set('k', 1)\n",
                    1,
                    1,
                ),
                "on a parameter of an unrelated function": (
                    "def f():\n    c = get_redis_connection()\n"
                    "def g(c):\n    c.set('k', 1)\n",
                    1,
                    1,
                ),
            }
        )

    def test_any_non_read_call_on_a_client_is_a_write(self):
        """Rule 2 (v2 F1): the read allow-list. `vf_cmd` is in no list, so
        only the data flow can count it, once per binding form."""
        self.assert_counts(
            {
                "unknown command": ("get_redis_connection().vf_cmd('k')\n", 1, 1),
                "with ... as": (
                    "def f():\n"
                    "    with get_redis_connection().pipeline() as p:\n"
                    "        p.vf_cmd('k')\n"
                    "        p.execute()\n",
                    1,
                    1,
                ),
                "annotated": (
                    "def f():\n    c: Redis = get_redis_connection()\n"
                    "    c.vf_cmd('k')\n",
                    1,
                    1,
                ),
                "walrus": (
                    "def f():\n    if (c := get_redis_connection()):\n"
                    "        c.vf_cmd('k')\n",
                    1,
                    1,
                ),
                "factory returning a bound name": (
                    "def _r():\n    c = get_redis_connection()\n    return c\n"
                    "def g():\n    _r().vf_cmd('k')\n",
                    1,
                    1,
                ),
                "self attribute": (
                    "class A:\n"
                    "    def __init__(self):\n"
                    "        self.r = get_redis_connection()\n"
                    "    def f(self):\n"
                    "        self.r.vf_cmd('k')\n",
                    1,
                    1,
                ),
                "keyword argument": (
                    "def h(*, client):\n    client.vf_cmd('k')\n"
                    "def f():\n    h(client=get_redis_connection())\n",
                    1,
                    1,
                ),
                "closure": (
                    "def f():\n    c = get_redis_connection()\n"
                    "    def g():\n        c.vf_cmd('k')\n    g()\n",
                    1,
                    1,
                ),
                "for target over a factory": (
                    "def _cs():\n    yield get_redis_connection()\n"
                    "def f():\n    for c in _cs():\n        c.vf_cmd('k')\n",
                    1,
                    1,
                ),
                "positional argument": (
                    "def h(client):\n    client.vf_cmd('k')\n"
                    "def f():\n    h(get_redis_connection())\n",
                    1,
                    1,
                ),
            }
        )

    def test_a_listed_module_gaining_raw_writes_changes_its_count(self):
        """v2's F1 on the real beat_locks: two new writes, two more."""
        source = (REPO / "AutoGrader/beat_locks.py").read_text()
        _, before = scan_raw_client(source)
        _, after = scan_raw_client(source + F1_EXTRA_WRITES)
        self.assertEqual(len(after), len(before) + 2, after)

    def test_an_imported_factory_counts_in_the_importing_module(self):
        source = "from AutoGrader.beat_locks import _redis\n_redis().set('k', 1)\n"
        self.assertEqual(scan_raw_client(source, {"_redis"}), ([2], [2]))

    def test_a_factory_imported_from_another_module_is_found_across_modules(self):
        """raw_client_uses resolves a factory imported from another module:
        by name (`from M import f [as g]`) and, v2's F2, through the module
        (`from P import M`, `import M as m`, `import P.M`). No live module
        does this yet, so the resolution is exercised on fixtures."""
        modules = {
            "a": "def _r():\n    return get_redis_connection()\n",
            "b": "from pkg.a import _r as conn\n\ndef f():\n    conn().set('k', 1)\n",
            # Same name, another module: not a factory.
            "c": "from pkg.other import _r\n\ndef f():\n    _r().set('k', 1)\n",
            "d": "from pkg import a\n\ndef f():\n    a._r().vf_cmd('k')\n",
            "e": "import pkg.a as m\n\ndef f():\n    m._r().vf_cmd('k')\n",
            "g": "import pkg.a\n\ndef f():\n    pkg.a._r().vf_cmd('k')\n",
        }
        with tempfile.TemporaryDirectory() as tmp:
            files = []
            for name, source in modules.items():
                path = Path(tmp) / f"{name}.py"
                path.write_text(source)
                files.append((f"pkg/{name}.py", path))
            uses = raw_client_uses(files)
        self.assertEqual(
            uses,
            {
                "pkg/a.py": (1, 0),
                "pkg/b.py": (1, 1),
                "pkg/d.py": (1, 1),
                "pkg/e.py": (1, 1),
                "pkg/g.py": (1, 1),
            },
        )

    def test_the_scanner_ignores_what_is_not_a_raw_write(self):
        for label, source in {
            "cache API": "cache.set('k', 1)\ncache.delete('k')\n",
            "a read": "get_redis_connection().get('k')\n",
            "a scan": "list(get_redis_connection().scan_iter(match='x'))\n",
            "reads and plumbing": (
                "def f():\n    c = get_redis_connection()\n"
                "    c.pttl('k')\n    p = c.pipeline()\n    p.execute()\n"
            ),
            "another client": "def f():\n    client = OpenAI()\n    client.set('x')\n",
            "no raw client in the module": "def g(c):\n    c.set('k', 1)\n",
        }.items():
            with self.subTest(label):
                self.assertEqual(scan_raw_client(source)[1], [], label)

    def test_the_live_modules_are_found_by_the_scan(self):
        """The table is not vacuous: each listed module really is found."""
        uses = raw_client_uses()
        for rel in RAW_CLIENT_USERS:
            with self.subTest(rel=rel):
                self.assertIn(rel, uses)


@override_settings(CACHES=REDIS_CACHE)
class EveryFamilyIsReachableByItsScopeTests(SimpleTestCase):
    """Layer 3, against real Redis."""

    def setUp(self):
        cache.clear()

    def tearDown(self):
        cache.clear()

    @staticmethod
    def _key(template, scopes, entity):
        base = template.replace("<UserCacheMixin base>", "courses:<>:query__x")
        base = base.replace("<>", OBJ)
        pairs = [
            (
                SCOPE_CONSTANTS[name],
                None if SCOPE_CONSTANTS[name] in SINGLETON_SCOPES else entity,
            )
            for name in scopes
        ]
        return versioned_key(base, pairs)

    def test_bumping_any_scope_of_a_family_makes_its_entry_unreachable(self):
        for template, scopes in ALL_FAMILIES.items():
            for name in scopes:
                with self.subTest(family=template, scope=name):
                    cache.clear()
                    key = self._key(template, scopes, U1)
                    cache.set(key, "cached", 300)
                    scope = SCOPE_CONSTANTS[name]
                    with redis_commands_sent_by_this_process() as sent:
                        bump_generation(
                            scope, None if scope in SINGLETON_SCOPES else U1
                        )
                    self.assertEqual(sent["SCAN"], 0)
                    self.assertEqual(sent["DEL"] + sent["UNLINK"], 0)
                    moved = self._key(template, scopes, U1)
                    self.assertNotEqual(moved, key)
                    self.assertIsNone(cache.get(moved))
                    # Unreachable, not deleted: invalidation removes nothing.
                    self.assertEqual(cache.get(key), "cached")

    def test_another_entitys_bump_leaves_the_entry_reachable(self):
        """The over-invalidation half, stated as a property. Under the
        wildcards, "*user*" fired by user 2's save destroyed user 1's page;
        a per-entity scope cannot."""
        for template, scopes in ALL_FAMILIES.items():
            per_entity = [
                n for n in scopes if SCOPE_CONSTANTS[n] not in SINGLETON_SCOPES
            ]
            for name in per_entity:
                with self.subTest(family=template, scope=name):
                    cache.clear()
                    key = self._key(template, scopes, U1)
                    cache.set(key, "cached", 300)
                    bump_generation(SCOPE_CONSTANTS[name], U2)
                    self.assertEqual(self._key(template, scopes, U1), key)
                    self.assertEqual(cache.get(key), "cached")

    def test_the_pdf_and_grading_caches_have_their_own_invalidation(self):
        """Not response caches in the generation scheme, deliberately.

        `assignments/pdf_cache.py` clears `assignmentpdf:<version>:<id>:*`
        itself - the one pattern delete H-1 kept - and the grading answer
        cache is content-addressed. Asserted so the distinction is recorded
        rather than assumed, and so the kept pattern is shown to reach
        nothing but that one assignment's PDFs.
        """
        pdf_key = f"assignmentpdf:v1:{OBJ}:student:stamp"
        other_pdf = f"assignmentpdf:v1:{U2}:student:stamp"
        response_keys = [
            self._key(template, scopes, U1) for template, scopes in ALL_FAMILIES.items()
        ]
        for key in [pdf_key, other_pdf, *response_keys]:
            cache.set(key, "x", 60)

        cache.delete_pattern(f"assignmentpdf:v1:{OBJ}:*")

        self.assertIsNone(cache.get(pdf_key))
        self.assertEqual(cache.get(other_pdf), "x")
        for key in response_keys:
            self.assertEqual(cache.get(key), "x", key)
