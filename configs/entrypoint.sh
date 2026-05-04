#!/bin/sh
# Launch both NINJAM server instances on a single Fly machine.
# Each server logs to stdout via stdbuf so Fly captures both streams.
# If either dies, the container exits so Fly can restart it.

set -e

cd /opt/ninjam

stdbuf -oL ninjamsrv server.cfg &
PID_2049=$!

stdbuf -oL ninjamsrv server-2050.cfg &
PID_2050=$!

trap 'kill $PID_2049 $PID_2050 2>/dev/null; exit 0' INT TERM

# Poll: if either child dies, kill the other and let Fly restart us.
while true; do
    if ! kill -0 $PID_2049 2>/dev/null; then
        kill $PID_2050 2>/dev/null || true
        wait
        exit 1
    fi
    if ! kill -0 $PID_2050 2>/dev/null; then
        kill $PID_2049 2>/dev/null || true
        wait
        exit 1
    fi
    sleep 2
done
