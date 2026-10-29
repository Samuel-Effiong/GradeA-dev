#!/usr/bin/env sh
# A private, CI-matching Postgres + Redis pair for one worktree/session, so
# a full-suite test run never has to share a database or a set of API keys
# with anything else running on this machine.
#
#   ./scripts/isolated-test-env.sh start <name>
#   ./scripts/isolated-test-env.sh stop  <name>
#   ./scripts/isolated-test-env.sh status <name>
#
# WHY THIS EXISTS
# ---------------
# `scripts/task-worktree.sh` already gives every worktree its own test
# DATABASE NAME on the shared Postgres server, and AutoGrader/settings.py
# already gives every test PROCESS its own Redis key prefix (H-9). Neither
# of those solves two different problems, both hit for real on 2026-09-22:
#
#   1. CAPACITY. A differently-named database is still a connection on the
#      SAME Postgres server, with the SAME max_connections ceiling, and a
#      differently-prefixed Redis key is still a connection to the SAME
#      Redis process with the SAME client limit. Two full-suite runs at
#      once can still exhaust either, producing "connection to server was
#      lost" / "connection refused" errors that look like test failures
#      and are not. This script gives each caller its own Postgres and
#      Redis SERVER PROCESS, not just its own name inside a shared one.
#
#   2. CREDENTIALS. The repo's own `.env` (shared by every worktree via a
#      symlink) carries REAL Stripe test-mode keys, so a test that forgets
#      to mock an outbound Stripe call still quietly succeeds locally.
#      GitHub Actions (.github/workflows/tests.yml) deliberately uses FAKE
#      placeholder keys so it can never reach real Stripe by accident. That
#      gap is exactly what hid a real bug (an unmocked stripe.Customer.create
#      call) behind two passing local runs and two failing CI runs before
#      anyone found it. This script exports the SAME fake values the
#      workflow file uses, so a local run that would fail on CI fails
#      locally too, immediately, instead of surfacing on a push.
#
# This does not replace the per-worktree DB name or the H-9 Redis prefix —
# both still matter for two isolated environments accidentally sharing a
# port. It adds the resource- and credential-isolation layer neither of
# them provides.
set -eu

ROOT=$(git rev-parse --show-toplevel)
STATE_DIR="$ROOT/.isolated-test-envs"

usage() {
    sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'
    exit 64
}

[ $# -eq 2 ] || usage
CMD=$1
NAME=$2

case "$NAME" in
    *[!a-z0-9-]*|-*|*-|"") echo "name must be kebab-case, e.g. gate-runner" >&2; exit 64 ;;
esac

ENV_DIR="$STATE_DIR/$NAME"
PGDATA="$ENV_DIR/pgdata"
PG_PORT_FILE="$ENV_DIR/pg_port"
REDIS_PORT_FILE="$ENV_DIR/redis_port"
REDIS_PID_FILE="$ENV_DIR/redis.pid"
ENV_FILE="$ENV_DIR/env.sh"

# Pinned to 16, not "whatever's newest installed" - CI's postgres:16 image
# is what this is meant to match (.github/workflows/tests.yml), and a
# version mismatch defeats the whole point.
PG_BIN=/usr/lib/postgresql/16/bin
if [ ! -x "$PG_BIN/initdb" ]; then
    echo "No postgresql-16 server binaries found at $PG_BIN — install postgresql-16 first (apt install postgresql-16)." >&2
    exit 1
fi

# A free TCP port, found by asking the kernel for one and immediately
# releasing it. There's a small race between checking and using it, same
# as any "find a free port" approach - acceptable here since collisions
# just mean re-running start, not silent data corruption.
free_port() {
    python3 -c "import socket; s=socket.socket(); s.bind(('127.0.0.1',0)); print(s.getsockname()[1]); s.close()"
}

status() {
    if [ ! -f "$ENV_FILE" ]; then
        echo "no environment named '$NAME'"
        return 1
    fi
    . "$ENV_FILE"
    pg_running="down"
    "$PG_BIN/pg_ctl" status -D "$PGDATA" >/dev/null 2>&1 && pg_running="up"
    redis_running="down"
    [ -f "$REDIS_PID_FILE" ] && kill -0 "$(cat "$REDIS_PID_FILE")" 2>/dev/null && redis_running="up"
    echo "postgres (port $(cat "$PG_PORT_FILE" 2>/dev/null || echo '?')): $pg_running"
    echo "redis    (port $(cat "$REDIS_PORT_FILE" 2>/dev/null || echo '?')): $redis_running"
    echo
    echo "source $ENV_FILE"
}

stop_env() {
    if [ -d "$PGDATA" ]; then
        "$PG_BIN/pg_ctl" stop -D "$PGDATA" -m fast >/dev/null 2>&1 || true
    fi
    if [ -f "$REDIS_PID_FILE" ]; then
        kill "$(cat "$REDIS_PID_FILE")" 2>/dev/null || true
        rm -f "$REDIS_PID_FILE"
    fi
    rm -rf "/tmp/gap-isolated-$NAME"
    echo "stopped '$NAME' (data kept at $ENV_DIR — remove it by hand if you want a truly fresh DB next time)"
}

case "$CMD" in
status)
    status
    exit 0
    ;;
stop)
    stop_env
    exit 0
    ;;
start)
    ;;
*)
    usage
    ;;
esac

# --- start ---------------------------------------------------------------
mkdir -p "$ENV_DIR"

if [ ! -d "$PGDATA" ]; then
    PG_PORT=$(free_port)
    echo "$PG_PORT" > "$PG_PORT_FILE"
    "$PG_BIN/initdb" -D "$PGDATA" -U postgres --auth=trust >/dev/null
    # trust auth: this instance is 127.0.0.1-only, unique per caller, and
    # torn down with the worktree - not a network-facing credential.
else
    PG_PORT=$(cat "$PG_PORT_FILE")
fi

if ! "$PG_BIN/pg_ctl" status -D "$PGDATA" >/dev/null 2>&1; then
    # unix_socket_directories defaults to /var/run/postgresql, which this
    # user can't write to - redirect the socket to /tmp instead of our own
    # env dir, since a Unix socket path is capped at 107 bytes and this
    # repo's path (under Documents/Projects/...) is long enough to blow
    # that limit on its own.
    SOCK_DIR="/tmp/gap-isolated-$NAME"
    mkdir -p "$SOCK_DIR"
    "$PG_BIN/pg_ctl" start -D "$PGDATA" -l "$ENV_DIR/postgres.log" \
        -o "-p $PG_PORT -c listen_addresses=127.0.0.1 -c unix_socket_directories=$SOCK_DIR" >/dev/null
    # pg_ctl start returns before Postgres finishes accepting connections.
    for _ in $(seq 1 30); do
        "$PG_BIN/pg_isready" -h 127.0.0.1 -p "$PG_PORT" >/dev/null 2>&1 && break
        sleep 0.5
    done
fi

"$PG_BIN/psql" -h 127.0.0.1 -p "$PG_PORT" -U postgres -d postgres -tAc \
    "SELECT 1 FROM pg_database WHERE datname='gradeaplus_ci'" | grep -q 1 || \
    "$PG_BIN/createdb" -h 127.0.0.1 -p "$PG_PORT" -U postgres gradeaplus_ci

if [ ! -f "$REDIS_PORT_FILE" ]; then
    REDIS_PORT=$(free_port)
    echo "$REDIS_PORT" > "$REDIS_PORT_FILE"
else
    REDIS_PORT=$(cat "$REDIS_PORT_FILE")
fi

if [ ! -f "$REDIS_PID_FILE" ] || ! kill -0 "$(cat "$REDIS_PID_FILE" 2>/dev/null)" 2>/dev/null; then
    redis-server --port "$REDIS_PORT" --bind 127.0.0.1 --daemonize no \
        --logfile "$ENV_DIR/redis.log" --dir "$ENV_DIR" &
    echo $! > "$REDIS_PID_FILE"
    for _ in $(seq 1 20); do
        redis-cli -h 127.0.0.1 -p "$REDIS_PORT" ping >/dev/null 2>&1 && break
        sleep 0.25
    done
fi

# Exact fake values from .github/workflows/tests.yml, so a local run under
# this environment fails on anything CI would fail on - never a real
# outbound call. Only the ports differ from the workflow file, because
# ours share a machine and can't both bind 5432/6379.
cat > "$ENV_FILE" <<EOF
export ENVIRONMENT=local
export SECRET_KEY=ci-secret-key-not-used-outside-ci
export FRONTEND_DOMAIN=http://localhost:3000
export STUDENT_FRONTEND_DOMAIN=http://localhost:3001
export DATABASE_URI_LOCAL=postgres://postgres@127.0.0.1:$PG_PORT/gradeaplus_ci
export REDIS_LOCAL_URL=redis://127.0.0.1:$REDIS_PORT/0
export LOCAL_STRIPE_PUBLIC_KEY=pk_test_ci
export LOCAL_STRIPE_SECRET_KEY=sk_test_ci
export LOCAL_STRIPE_WEBHOOK_SECRET=whsec_ci
export CLOUDINARY_CLOUD_NAME=ci
export CLOUDINARY_API_KEY=ci
export CLOUDINARY_API_SECRET=ci
export MAILSEND_API_KEY=ci
export GOOGLE_OAUTH_CLIENT_ID=ci
export GOOGLE_OAUTH_CLIENT_SECRET=ci
export GOOGLE_REDIRECT_URI=http://localhost:3000/auth/google
export OPENROUTER_API_KEY=sk-or-ci-not-a-real-key
export FIELD_ENCRYPTION_KEY=Y2ktdGVzdC1rZXktbm90LWZvci1wcm9kdWN0aW9uISE=
EOF

cat <<EOF

Isolated environment '$NAME' ready.
    postgres -> 127.0.0.1:$PG_PORT (gradeaplus_ci)
    redis    -> 127.0.0.1:$REDIS_PORT

Use it:
    . $ENV_FILE
    python manage.py test --settings=settings_worktree --noinput

Stop it:
    ./scripts/isolated-test-env.sh stop $NAME

This does NOT run migrations for you and starts from an empty database each
time the pgdata directory is first created (fresh, matching CI - no
--keepdb carried over from a prior task). Re-running 'start' on an existing
'$NAME' reuses the same data directory and ports rather than recreating
them, so a second 'python manage.py test' run without --keepdb will still
rebuild the test DB from scratch inside it, same as any normal test run.
EOF
