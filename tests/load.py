#!/usr/bin/env python3
"""Load test for the private-room server (PrivateGroupMode) over the real wire protocol.

  load.py --port 2091 --rooms 8 --users 8 --audio-kbps 128 --video-kbps 1200 --fps 24 --seconds 60

Every room runs in its own process with `--users` clients. Each client logs in
anonymously, joins its room by sending a signed code from the lobby, subscribes
to everyone else and then uploads like the app does: one Vorbis-sized audio
interval stream per audio channel (writes every 125 ms) and, with --video-kbps, an
H.264-sized stream on the next channel (one write per frame, a keyframe 4x the average
at the start of each interval, split at the core's 9216-byte write limit).
Payloads are filler: the server relays bytes without decoding them.

Each write carries its send time, so receivers measure how long the server took
to relay it. Printed per run: what each client should have received versus what
it got (audio and video), relay delay percentiles, disconnects, and — with
--pid — the server's CPU use while the load ran.
"""
import argparse
import asyncio
import hashlib
import multiprocessing
import os
import struct
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from smoke import CROCKFORD, MSG_CHAT, MSG_CLIENT_AUTH_USER, PROTOCOL_VERSION, mint_code  # noqa: E402

MSG_SERVER_AUTH_CHALLENGE = 0x00
MSG_SERVER_AUTH_REPLY = 0x01
MSG_SERVER_USERINFO = 0x03
MSG_SERVER_DL_BEGIN = 0x04
MSG_SERVER_DL_WRITE = 0x05
MSG_CLIENT_SET_USERMASK = 0x81
MSG_CLIENT_SET_CHANNEL_INFO = 0x82
MSG_CLIENT_UL_BEGIN = 0x83
MSG_CLIENT_UL_WRITE = 0x84
MSG_KEEPALIVE = 0xFD

FOURCC_AUDIO = struct.unpack("<I", b"OGGv")[0]
FOURCC_VIDEO = struct.unpack("<I", b"H264")[0]
MAX_WRITE = 8192 + 1024  # the core's MAX_ENC_BLOCKSIZE
AUDIO_TICK = 0.125
INTERVAL_SECONDS = 8.0  # the room default: 120 BPM x 16 BPI


def msg(t, body):
    return bytes([t]) + struct.pack("<I", len(body)) + body


def room_code(i):
    payload = "".join(CROCKFORD[(i >> (5 * k)) & 31] for k in range(5))
    return mint_code(payload)


class Client:
    def __init__(self, args, room, idx, stats):
        self.args, self.room, self.idx, self.stats = args, room, idx, stats
        self.name = f"r{room}u{idx}"
        self.guid_kind = {}
        self.subscribed = set()
        self.joined = asyncio.Event()

    async def run(self, start_at, stop_at):
        # People arrive one by one, not all in the same millisecond (the lobby has a cap).
        await asyncio.sleep(self.args.stagger * (self.room * self.args.users + self.idx))
        await asyncio.wait_for(self.join(), 20)
        alive = asyncio.create_task(self.keepalive(stop_at))  # idle until the load starts
        await asyncio.sleep(max(0, start_at - time.time()))
        senders = [self.send_audio(c, stop_at) for c in range(self.args.audio_channels)]
        if self.args.video_kbps:
            senders.append(self.send_video(stop_at))
        await asyncio.gather(alive, *senders)
        await asyncio.sleep(self.args.drain)
        self.receiver.cancel()
        self.writer.close()

    async def join(self):
        a = self.args
        self.reader, self.writer = await asyncio.open_connection(a.host, a.port)
        if a.proxy:
            self.writer.write(f"PROXY TCP4 10.0.{self.room}.{self.idx + 1} 10.0.0.1 40000 {a.port}\r\n".encode())
        t, payload = await self.read()
        if t != MSG_SERVER_AUTH_CHALLENGE:
            raise RuntimeError("no auth challenge")
        user = "anonymous:" + self.name
        inner = hashlib.sha1(f"{user}:".encode()).digest()
        passhash = hashlib.sha1(inner + payload[:8]).digest()
        self.send(MSG_CLIENT_AUTH_USER, passhash + user.encode() + b"\0" + struct.pack("<II", 1, PROTOCOL_VERSION))
        t, payload = await self.read()
        if t != MSG_SERVER_AUTH_REPLY or payload[0] != 1:
            raise RuntimeError("login refused")
        code = room_code(self.room)
        self.send(MSG_CHAT, b"MSG\0" + code.encode() + b"\0")
        self.receiver = asyncio.create_task(self.receive())
        await self.joined.wait()

        chans = struct.pack("<H", 4) + b"".join(
            f"audio{c}".encode() + b"\0" + struct.pack("<hBB", 0, 0, 0) for c in range(a.audio_channels))
        if a.video_kbps:
            chans += b"video\0" + struct.pack("<hBB", 0, 0, 0)
        self.send(MSG_CLIENT_SET_CHANNEL_INFO, chans)

    def send(self, t, body):
        self.writer.write(msg(t, body))

    async def read(self):
        hdr = await self.reader.readexactly(5)
        n = struct.unpack("<I", hdr[1:5])[0]
        return hdr[0], await self.reader.readexactly(n)

    async def receive(self):
        try:
            while True:
                t, p = await self.read()
                if t == MSG_CHAT and b"joining room" in p:
                    self.joined.set()
                elif t == MSG_SERVER_USERINFO:
                    self.subscribe(p)
                elif t == MSG_SERVER_DL_BEGIN:
                    fourcc = struct.unpack("<I", p[20:24])[0]
                    self.guid_kind[p[:16]] = "video" if fourcc == FOURCC_VIDEO else "audio"
                elif t == MSG_SERVER_DL_WRITE:
                    kind = self.guid_kind.get(p[:16])
                    if kind:
                        data = p[17:]
                        self.stats[kind + "_rx"] += len(data)
                        if len(data) >= 8:
                            sent = struct.unpack("<d", data[:8])[0]
                            if sent > 0:
                                self.stats["delays"].append(time.time() - sent)
        except (asyncio.IncompleteReadError, ConnectionError):
            self.stats["disconnects"] += 1
        except asyncio.CancelledError:
            pass

    def subscribe(self, p):
        offs, recs = 0, []
        while offs + 8 <= len(p):
            active = p[offs]
            end_user = p.index(b"\0", offs + 6)
            end_chan = p.index(b"\0", end_user + 1)
            user = p[offs + 6:end_user].decode()
            if active and user not in self.subscribed:
                self.subscribed.add(user)
                recs.append(user.encode() + b"\0" + struct.pack("<I", 0xFFFFFFFF))
            offs = end_chan + 1
        if recs:
            self.send(MSG_CLIENT_SET_USERMASK, b"".join(recs))

    def write(self, guid, data, last):
        self.send(MSG_CLIENT_UL_WRITE, guid + bytes([1 if last else 0]) + data)

    def payload(self, size):
        return struct.pack("<d", time.time()) + b"\x55" * max(0, size - 8)

    async def send_audio(self, chidx, stop_at):
        per_tick = int(self.args.audio_kbps * 1000 / 8 * AUDIO_TICK)
        ticks = int(INTERVAL_SECONDS / AUDIO_TICK)
        nxt = time.time()
        while time.time() < stop_at:
            guid = os.urandom(16)
            self.send(MSG_CLIENT_UL_BEGIN, guid + struct.pack("<iIB", 0, FOURCC_AUDIO, chidx))
            for i in range(ticks):
                last = i == ticks - 1 or nxt + AUDIO_TICK >= stop_at
                self.write(guid, self.payload(per_tick), last)
                self.stats["audio_tx"] += per_tick
                await self.writer.drain()
                nxt += AUDIO_TICK
                await asyncio.sleep(max(0, nxt - time.time()))
                if last:
                    break

    async def send_video(self, stop_at):
        fps = self.args.fps
        frames = int(INTERVAL_SECONDS * fps)
        avg = self.args.video_kbps * 1000 / 8 / fps
        # A keyframe ~4x the average, the rest shrunk so the interval still averages out.
        key = int(avg * 4)
        delta = int((avg * frames - key) / (frames - 1))
        nxt = time.time()
        while time.time() < stop_at:
            guid = os.urandom(16)
            self.send(MSG_CLIENT_UL_BEGIN, guid + struct.pack("<iIB", 0, FOURCC_VIDEO, self.args.audio_channels))
            for f in range(frames):
                end = f == frames - 1 or nxt + 1 / fps >= stop_at
                size = key if f == 0 else delta
                left = size
                while left > 0:
                    part = min(left, MAX_WRITE)
                    left -= part
                    self.write(guid, self.payload(part), end and left == 0)
                self.stats["video_tx"] += size
                await self.writer.drain()
                nxt += 1 / fps
                await asyncio.sleep(max(0, nxt - time.time()))
                if end:
                    break

    async def keepalive(self, stop_at):
        while time.time() < stop_at:
            self.send(MSG_KEEPALIVE, b"")
            await asyncio.sleep(3)


def run_room(args, room, start_at, stop_at, out):
    stats = {"audio_tx": 0, "video_tx": 0, "audio_rx": 0, "video_rx": 0, "delays": [], "disconnects": 0, "errors": []}

    async def main():
        clients = [Client(args, room, i, stats) for i in range(args.users)]
        results = await asyncio.gather(*(c.run(start_at, stop_at) for c in clients), return_exceptions=True)
        stats["errors"] = [repr(r) for r in results if isinstance(r, Exception)]

    asyncio.run(main())
    out.put(stats)


def cpu_seconds(pid):
    """User+system CPU time of a local process, from ps (works on macOS and Linux)."""
    t = subprocess.run(["ps", "-o", "time=", "-p", str(pid)], capture_output=True, text=True).stdout.strip()
    if not t:
        return None
    days, _, t = t.rpartition("-")
    parts = [float(x) for x in t.split(":")]
    secs = 0.0
    for x in parts:
        secs = secs * 60 + x
    return secs + (int(days) * 86400 if days else 0)


def pct(sorted_vals, q):
    if not sorted_vals:
        return float("nan")
    return sorted_vals[min(len(sorted_vals) - 1, int(q * len(sorted_vals)))]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, required=True)
    ap.add_argument("--rooms", type=int, default=1)
    ap.add_argument("--users", type=int, default=4, help="per room")
    ap.add_argument("--audio-kbps", type=int, default=128, help="per channel; the app's default is 128, max 256")
    ap.add_argument("--audio-channels", type=int, default=1, help="per user; the app allows up to 8")
    ap.add_argument("--video-kbps", type=int, default=0, help="0 = no video; HD is 1200")
    ap.add_argument("--fps", type=int, default=24)
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--stagger", type=float, default=0.1, help="seconds between one client's arrival and the next")
    ap.add_argument("--warmup", type=float, default=0, help="extra seconds before load starts (default: after every join)")
    ap.add_argument("--drain", type=float, default=3, help="keep reading after sending stops")
    ap.add_argument("--proxy", action="store_true", help="send a PROXY v1 line (server has ProxyProtocol yes)")
    ap.add_argument("--pid", type=int, help="server pid on this machine, to measure its CPU")
    args = ap.parse_args()

    start_at = time.time() + args.stagger * args.rooms * args.users + 5 + args.warmup
    stop_at = start_at + args.seconds
    out = multiprocessing.Queue()
    procs = [multiprocessing.Process(target=run_room, args=(args, r, start_at, stop_at, out)) for r in range(args.rooms)]
    for p in procs:
        p.start()

    cpu0 = None
    if args.pid:
        time.sleep(max(0, start_at - time.time()))
        cpu0, wall0 = cpu_seconds(args.pid), time.time()
        time.sleep(max(0, stop_at - time.time()))
        cpu1, wall1 = cpu_seconds(args.pid), time.time()

    results = [out.get() for _ in procs]
    for p in procs:
        p.join()

    tot = {k: sum(r[k] for r in results) for k in ("audio_tx", "video_tx", "audio_rx", "video_rx", "disconnects")}
    errors = [e for r in results for e in r["errors"]]
    delays = sorted(d for r in results for d in r["delays"])
    fan = args.users - 1
    mbps = lambda b: b * 8 / args.seconds / 1e6  # noqa: E731

    print(f"rooms={args.rooms} users/room={args.users} audio={args.audio_channels}x{args.audio_kbps}kbps "
          f"video={args.video_kbps}kbps@{args.fps}fps seconds={args.seconds:g}")
    for kind in ("audio", "video"):
        if tot[kind + "_tx"]:
            want = tot[kind + "_tx"] * fan
            print(f"  {kind}: sent {mbps(tot[kind + '_tx']):.1f} Mbps, relayed {mbps(tot[kind + '_rx']):.1f} Mbps "
                  f"of {mbps(want):.1f} expected ({100 * tot[kind + '_rx'] / want:.1f}%)")
    print(f"  server egress {mbps(tot['audio_rx'] + tot['video_rx']):.1f} Mbps "
          f"= {(tot['audio_rx'] + tot['video_rx']) / args.seconds * 3600 / 1e9:.2f} GB/h")
    print(f"  relay delay p50 {1000 * pct(delays, .5):.1f} ms, p95 {1000 * pct(delays, .95):.1f} ms, "
          f"p99 {1000 * pct(delays, .99):.1f} ms, max {1000 * (delays[-1] if delays else float('nan')):.1f} ms")
    print(f"  disconnects {tot['disconnects']}, client errors {len(errors)}" + (f": {errors[:3]}" if errors else ""))
    if cpu0 is not None and cpu1 is not None:
        print(f"  server CPU {100 * (cpu1 - cpu0) / (wall1 - wall0):.1f}% of one core")


if __name__ == "__main__":
    main()
