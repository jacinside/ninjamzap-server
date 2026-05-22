#!/bin/sh
# Launch all NINJAM server instances on a single Fly machine.
# Each server logs to stdout via stdbuf so Fly captures every stream.
# If any of them dies, the container exits so Fly can restart it.
#
# Port allocation (kept in sync with fly.toml):
#   2049 — public default
#   2050 — public secondary
#   2090 — private (invite-only, AnonymousUsers no)
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
        "$src" > "$dst"
    chmod 600 "$dst"
}

render_cfg server.cfg.tpl       server.cfg
render_cfg server-2050.cfg.tpl  server-2050.cfg
render_cfg private-2090.cfg.tpl private-2090.cfg

stdbuf -oL ninjamsrv server.cfg &
PID_2049=$!

stdbuf -oL ninjamsrv server-2050.cfg &
PID_2050=$!

stdbuf -oL ninjamsrv private-2090.cfg &
PID_2090=$!

trap 'kill $PID_2049 $PID_2050 $PID_2090 2>/dev/null; exit 0' INT TERM

# Poll: if any child dies, kill the others and let Fly restart us.
while true; do
    if ! kill -0 $PID_2049 2>/dev/null; then
        kill $PID_2050 $PID_2090 2>/dev/null || true
        wait
        exit 1
    fi
    if ! kill -0 $PID_2050 2>/dev/null; then
        kill $PID_2049 $PID_2090 2>/dev/null || true
        wait
        exit 1
    fi
    if ! kill -0 $PID_2090 2>/dev/null; then
        kill $PID_2049 $PID_2050 2>/dev/null || true
        wait
        exit 1
    fi
    sleep 2
done
