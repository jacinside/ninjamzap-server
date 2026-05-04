# NinjamZap Server

A fork of the [Cockos NINJAM](https://www.cockos.com/ninjam/) server that adds **real-time video channel support** alongside audio, plus a thread-per-group architecture for private rooms.

It powers the public NinjamZap rooms (`video.ninjamzap.com:2049`, `:2050`) used by the [NinjamZap mobile client](https://www.ninjamzap.com), and any standard NINJAM client (Reaper/ReaNINJAM, Jamtaba, etc.) can connect for the audio path.

License: **GPL v2** — same as upstream NINJAM. See [LICENSE](LICENSE).

---

## What's different from upstream

### 1. Video channels

Video frames are relayed in-band through the same interval-based protocol that NINJAM already uses for audio. No separate signaling channel; video is just another channel type identified by its **fourcc**:

- `H264` — H.264 / AVC
- `VP8 ` — VP8 / WebM
- `MJPG` — Motion JPEG

The server prioritises audio: each main loop iteration relays audio first and then video, and video frames are dropped first for slow subscribers so that audio quality stays clean even on a congested link.

Tunables (in the server `.cfg`):

| Directive | Purpose |
|---|---|
| `AllowVideoChannels yes` | Enable video relay |
| `VideoTransferTimeout` | Max ms to spend pushing a video frame to one subscriber |
| `VideoCongestionThreshold` | Send-queue size at which the server starts dropping video frames |
| `SendBufferKB` / `RecvBufferKB` | Per-connection socket buffer sizes |

Full protocol/architecture details: [docs/VIDEO_SUPPORT.md](docs/VIDEO_SUPPORT.md).

### 2. Thread-per-group

Private rooms (when configured) each run on their own `pthread`; the public lobby stays on the main thread. Users are migrated between rooms through a mutex-protected queue. When `PrivateGroupMode` is **not** configured the server behaves exactly like upstream — no extra threads are created, so there is no overhead for plain public-server use.

---

## Quick build (native)

```bash
cd ninjam/server
make             # Linux
MAC=1 make       # macOS
./ninjamsrv ../../configs/default.cfg
```

The binary is `ninjam/server/ninjamsrv`. It accepts the same CLI flags as upstream:

```bash
./ninjamsrv config.cfg [-port 2049] [-logfile server.log] [-pidfile server.pid]
```

`SIGHUP` reloads the config without restarting; `SIGINT` shuts down cleanly (joins all room threads).

---

## Run with Docker

The included [`Dockerfile`](Dockerfile) builds the server in a Debian-slim multi-stage image (~23 MB final). It runs **two** server instances out of the box (ports `2049` and `2050`) via [`configs/entrypoint.sh`](configs/entrypoint.sh) — handy if you want a single VM to host two rooms.

```bash
# Build
docker build -t ninjamzap-server .

# Run (publish both ports)
docker run --rm \
  -p 2049:2049 \
  -p 2050:2050 \
  --name ninjamzap-server \
  ninjamzap-server
```

Override the configs by bind-mounting your own:

```bash
docker run --rm \
  -p 2049:2049 -p 2050:2050 \
  -v "$PWD/my-2049.cfg:/opt/ninjam/server.cfg:ro" \
  -v "$PWD/my-2050.cfg:/opt/ninjam/server-2050.cfg:ro" \
  -v "$PWD/motd.txt:/opt/ninjam/motd.txt:ro" \
  ninjamzap-server
```

If you only need a single port, point the entrypoint at one config or simplify it — the binary itself is just `ninjamsrv server.cfg`.

### Sample config (`configs/default.cfg`)

```
Port 2049
MaxUsers 6
MaxChannels 32 2

ServerLicense cclicense.txt
StatusUserPass <user> <pass>

AnonymousUsers yes
AnonymousUsersCanChat yes
AnonymousMaskIP yes

AllowVideoChannels yes
SendBufferKB 2048
RecvBufferKB 1024

ACL 0.0.0.0/0 allow

DefaultTopic "Welcome to your NINJAM room"
DefaultBPM 120
DefaultBPI 16

MOTDFile /opt/ninjam/motd.txt
```

Annotated reference with every option: [`ninjam/server/example.cfg`](ninjam/server/example.cfg).

---

## Deploy to Fly.io

`fly.toml` and `Dockerfile` are tuned for Fly.io. The image is small enough to run on a `shared-cpu-1x` 256 MB machine.

```bash
flyctl auth login
flyctl launch --copy-config --no-deploy   # first time only
flyctl deploy
flyctl logs --app <your-app>
```

A few things worth knowing if you go this route:

- **Dedicated IPv4 is required.** NINJAM is plain TCP, and Fly's shared IPv4 only handles HTTP. Run `flyctl ips allocate-v4` (~$2/mo).
- **No HTTP health checks.** The NINJAM server treats Fly's TCP probes as failed client connections — don't configure them.
- **Logs.** The Dockerfile uses `stdbuf -oL` so `flyctl logs` captures output line by line.
- **Two ports on one machine.** Add a second `[[services]]` block in `fly.toml` for the second port (the IPv4 covers both). The included `entrypoint.sh` already supervises two `ninjamsrv` processes.

A working `fly.toml` template is in this repo.

---

## Connect

From the NinjamZap mobile client (iOS/Android) or any standard NINJAM client:

- Host: `your.host.example`
- Port: `2049`
- Username: anything (anonymous unless you configure `UserPass`)

Topic and welcome message are set via `DefaultTopic` and `MOTDFile`.

---

## Contributing / structure

```
ninjam/server/usercon.{h,cpp}    # user/group state, relay, video drop, threading
ninjam/server/ninjamsrv.cpp      # main loop, config parsing, thread lifecycle
ninjam/netmsg.h                  # Net_Message, Net_Connection, send queue
ninjam/server/Makefile           # build (links pthread)
ninjam/server/example.cfg        # documented config template
configs/                         # production configs + Docker entrypoint
docs/                            # protocol & architecture notes
WDL/                             # Cockos WDL helper library (vendored)
```

Issues and PRs welcome. If you're using this in production we'd love to know — open an issue or reach out via `support@ninjamzap.com`.

---

## Acknowledgements

Built on top of the original [Cockos NINJAM](https://www.cockos.com/ninjam/) by Justin Frankel & Brennan Underwood. NINJAM is the reason any of this works at all.
