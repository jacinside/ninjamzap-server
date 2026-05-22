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
# The private config is rendered from a committed template + Fly secrets
# at startup. The .cfg with credentials never lands in the public repo.

set -e

cd /opt/ninjam

# ─── Render private-2090.cfg from template + Fly secrets ───────────────
if [ -f private-2090.cfg.example ]; then
    : "${PRIVATE_ADMIN_USER:?PRIVATE_ADMIN_USER not set — run \`fly secrets set\` before deploy}"
    : "${PRIVATE_ADMIN_PASS:?PRIVATE_ADMIN_PASS not set — run \`fly secrets set\` before deploy}"
    : "${ROOM_INVITE_PASS:?ROOM_INVITE_PASS not set — run \`fly secrets set\` before deploy}"

    # Strict substitution — only replace the variables we expect, so
    # `$` characters anywhere else in the template stay literal.
    sed \
        -e "s|\${PRIVATE_ADMIN_USER}|${PRIVATE_ADMIN_USER}|g" \
        -e "s|\${PRIVATE_ADMIN_PASS}|${PRIVATE_ADMIN_PASS}|g" \
        -e "s|\${ROOM_INVITE_PASS}|${ROOM_INVITE_PASS}|g" \
        private-2090.cfg.example > private-2090.cfg
    chmod 600 private-2090.cfg
fi

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
