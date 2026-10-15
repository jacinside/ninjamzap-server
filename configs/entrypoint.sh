#!/bin/sh
# Launch all NINJAM server instances on a single Fly machine.
# Each server logs to stdout via stdbuf so Fly captures every stream.
# If any of them dies, the container exits so Fly can restart it.
#
# Port allocation (kept in sync with fly.toml):
#   2049 — public default
#   2050 — public secondary
#   2090 — private (invite-only, AnonymousUsers no)
#   2091 — shared private rooms (token-addressed, dynamically allocated;
#          retired codes polled from the backend, see below)
#
# All configs are rendered from committed templates + Fly secrets at
# startup. The rendered .cfg files with credentials never land in the
# public repo.

set -e

cd /opt/ninjam

# ─── Required secrets ───────────────────────────────────────────────────
: "${STATUS_USER:?STATUS_USER not set — run \`fly secrets set\` before deploy}"
: "${STATUS_PASS:?STATUS_PASS not set — run \`fly secrets set\` before deploy}"
: "${BOT_PASS:?BOT_PASS not set — run \`fly secrets set\` before deploy}"
: "${PRIVATE_ADMIN_USER:?PRIVATE_ADMIN_USER not set — run \`fly secrets set\` before deploy}"
: "${PRIVATE_ADMIN_PASS:?PRIVATE_ADMIN_PASS not set — run \`fly secrets set\` before deploy}"
: "${ROOM_INVITE_PASS:?ROOM_INVITE_PASS not set — run \`fly secrets set\` before deploy}"
: "${ROOM_TOKEN_SECRET:?ROOM_TOKEN_SECRET not set — run \`fly secrets set\` before deploy}"

# Strict substitution — only replace the variables we expect, so `$`
# characters anywhere else in the templates stay literal.
render_cfg() {
    src=$1
    dst=$2
    sed \
        -e "s|\${STATUS_USER}|${STATUS_USER}|g" \
        -e "s|\${STATUS_PASS}|${STATUS_PASS}|g" \
        -e "s|\${BOT_PASS}|${BOT_PASS}|g" \
        -e "s|\${PRIVATE_ADMIN_USER}|${PRIVATE_ADMIN_USER}|g" \
        -e "s|\${PRIVATE_ADMIN_PASS}|${PRIVATE_ADMIN_PASS}|g" \
        -e "s|\${ROOM_INVITE_PASS}|${ROOM_INVITE_PASS}|g" \
        -e "s|\${ROOM_TOKEN_SECRET}|${ROOM_TOKEN_SECRET}|g" \
        "$src" > "$dst"
    chmod 600 "$dst"
}

render_cfg server.cfg.tpl       server.cfg
render_cfg server-2050.cfg.tpl  server-2050.cfg
render_cfg private-2090.cfg.tpl private-2090.cfg
render_cfg rooms-2091.cfg.tpl   rooms-2091.cfg

# ─── Retired room codes (2091) ──────────────────────────────────────────
# The backend publishes every retired room code as a keyed fingerprint; 2091
# re-reads this file whenever it changes (PrivateGroupRevokedCodesFile) and
# refuses those codes. A body without the list's header line (an error page, a
# truncated download) is never swapped in, and a failed poll keeps the last good
# list — the server fails open only until the first successful poll.
REVOKED_URL="${REVOKED_CODES_URL:-https://api.ninjamzap.com/rooms/revoked-codes}"
REVOKED_FILE=/opt/ninjam/revoked-codes.txt
poll_revoked_codes() {
    while true; do
        if curl -fsS --max-time 10 "$REVOKED_URL" -o "$REVOKED_FILE.tmp" 2>/dev/null \
           && head -n 1 "$REVOKED_FILE.tmp" | grep -qx '# ninjamzap revoked room codes v1'; then
            cmp -s "$REVOKED_FILE.tmp" "$REVOKED_FILE" || mv "$REVOKED_FILE.tmp" "$REVOKED_FILE"
        else
            echo "[revoked-codes] poll failed, keeping the last list"
        fi
        sleep 30
    done
}
poll_revoked_codes &
PID_POLL=$!

stdbuf -oL ninjamsrv server.cfg &
PID_2049=$!

stdbuf -oL ninjamsrv server-2050.cfg &
PID_2050=$!

stdbuf -oL ninjamsrv private-2090.cfg &
PID_2090=$!

stdbuf -oL ninjamsrv rooms-2091.cfg &
PID_2091=$!

trap 'kill $PID_2049 $PID_2050 $PID_2090 $PID_2091 $PID_POLL 2>/dev/null; exit 0' INT TERM

# Poll: if any child dies, kill the others and let Fly restart us.
while true; do
    if ! kill -0 $PID_2049 2>/dev/null; then
        kill $PID_2050 $PID_2090 $PID_2091 2>/dev/null || true
        wait
        exit 1
    fi
    if ! kill -0 $PID_2050 2>/dev/null; then
        kill $PID_2049 $PID_2090 $PID_2091 2>/dev/null || true
        wait
        exit 1
    fi
    if ! kill -0 $PID_2090 2>/dev/null; then
        kill $PID_2049 $PID_2050 $PID_2091 2>/dev/null || true
        wait
        exit 1
    fi
    if ! kill -0 $PID_2091 2>/dev/null; then
        kill $PID_2049 $PID_2050 $PID_2090 2>/dev/null || true
        wait
        exit 1
    fi
    sleep 2
done
