# Changelog

All notable changes to NinjamZap Server are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

Versioning policy (while in `0.x`, on-wire and config-file behaviour may
still change between minor releases — once it stabilizes the project
moves to `1.0.0`):

- **MAJOR** — wire-level protocol change, breaking config syntax change,
  or any change that requires existing operators to take action when
  upgrading.
- **MINOR** — new optional config directives, new features that are
  off-by-default, new fourcc support, doc additions.
- **PATCH** — bug fixes, observability improvements, dependency bumps
  that do not alter behaviour.

## [Unreleased]

### Added

- **PROXY protocol v1** (`ProxyProtocol yes`, off by default). Behind a TCP proxy every
  connection comes from the proxy's own address, so ACLs, logs and anonymous `@ip` names
  all show the same private IP. With this on, each connection must start with a PROXY v1
  line (`PROXY TCP4 <src> <dst> <sport> <dport>\r\n`); its source address replaces the
  peer's before the connection is accepted. IPv4-mapped `TCP6` sources are used; native
  IPv6 and `UNKNOWN` keep the proxy's address. A connection that sends anything else, or
  no complete header within 10 seconds, is dropped. The deployed configs enable it together
  with Fly.io's `proxy_proto` handler.
- **Signed room tokens for `PrivateGroupMode`** (`PrivateGroupTokenSecret`). When set,
  a room name must be an 8-character Crockford Base32 code whose last 3 characters are a
  truncated `HMAC-SHA1` of the first 5. Names that fail verification are refused, so only
  the holder of the secret can mint names that create rooms — without it, any lobby
  occupant can invent names and occupy the available room slots. Verification is
  stateless; the server needs no link to whatever issues the codes. Codes are
  case-insensitive and dashes are ignored, so `K7QM-2XA9` and `k7qm2xa9` resolve to the
  same room instead of two.
- **Join rate limit** (`PrivateGroupTokenMaxFail`, default 5). Caps how many well-formed
  but incorrectly signed codes one connection may send before the server stops honouring
  its join requests, forcing a reconnect. Input that isn't shaped like a code (ordinary
  chat from a client unaware of rooms) is rejected with a hint and does not count against
  the limit.
- **Retired room codes** (`PrivateGroupRevokedCodesFile <path>`, off by default). A signed
  code verifies forever, so replacing a room's code or deleting the room locked nobody
  out. The file lists retired codes as keyed fingerprints — hex of the first 8 bytes of
  `HMAC-SHA1(secret, "revoked:" + code)` — under the header line
  `# ninjamzap revoked room codes v1`. The server re-reads it within 5 seconds of a
  change, refuses those codes in the lobby (without counting them against
  `PrivateGroupTokenMaxFail`), and tells everyone in a room opened with one why, then
  disconnects them. A missing file revokes nothing; a file without the header is ignored.
  The deploy image polls the list from the backend every 30 seconds (`REVOKED_CODES_URL`).

Both directives are optional and off by default: with no secret configured, room naming
behaves exactly as before.

- **Load test** (`tests/load.py`): rooms of clients that join with signed codes and upload
  audio and video at real bitrates and write sizes, reporting relayed versus expected
  throughput, relay delay and the server's CPU.

### Fixed

From upstream NINJAM pull requests (original authorship kept in each commit):

- **Message parsers** (`mpb.cpp`, justinfrankel/ninjam#25): names and chat parameters
  without a terminator are refused instead of read past the end of the message; a
  `set_channel_info` record with cut-short fields is refused; pan is read as the signed
  byte it is on the wire. Before, a channel panned left was relayed to the other clients
  as hard right (the parsed 128..255 value was clamped to 127 on rebuild).
- **Control characters in relayed chat** (justinfrankel/ninjam#26): characters below
  0x20 in relayed `MSG`, `PRIVMSG` and the topic are replaced with spaces, so one line
  can't pose as several to clients or server-list scrapers.
- **Message leaks** (justinfrankel/ninjam#27): messages received while refusing or timing
  out a connection before auth, and channel-info broadcasts built in lobby mode, are freed.

### Changed

- The private configs (`rooms-2091`, `private-2090`) allow 9 channels per anonymous user,
  like the public ones: 8 audio channels plus video. With 8, a user sending video had only
  7 audio channels.

- Merged upstream NINJAM `main` up to `f4c0eff3` (2025-06-01): const-correct APIs,
  locale-safe character classification in command parsing. `WDL/` updated to the version
  that commit builds with (it was an older snapshot, unmodified).

## [0.1.0] — 2026-06-03

First tagged release of the NinjamZap NINJAM server fork. Snapshot of
the production deployment that backs `video.ninjamzap.com:2049` and
`:2050`, suitable for operators who want to host video-capable rooms
and for client implementers who need a reference target.

### Included

- Fork of the Cockos NINJAM server with full audio relay parity.
- **Video channel support**: relays H264/VP8/MJPG alongside audio over
  the same interval-based protocol. Audio relays first in each loop
  iteration (two-pass); video frames are dropped for slow subscribers
  to protect audio quality.
- **Thread-per-group** for private rooms (`PrivateGroupMode`). The
  lobby stays on the main thread; users migrate to the group's pthread
  via a mutex-protected queue. No threads are created when
  `PrivateGroupMode` is not configured — fully backward compatible with
  vanilla operator setups.
- **Invite-only rooms** via `RoomPassword`. Per-room password
  enforcement layered over the existing user-level auth.
- **Hidden users** (`AllowHiddenUsers`) for bot/relay slots that are
  excluded from the user list and the BPM/BPI vote divisor.
- **Anonymous channel cap configurable**. Production config bumps the
  default `MaxChannels 32 2` (where anon users get 2 slots) to
  `MaxChannels 32 8` so anon users can carry stereo audio + video in
  one session.
- **Fly.io deployment**: `Dockerfile`, `configs/entrypoint.sh` for Fly
  secrets rendering (`STATUS_USER`, `STATUS_PASS`, `BOT_PASS`), runs on
  any container host.
- `SIGHUP` reloads config without restart; `SIGINT` joins all room
  threads cleanly on shutdown.

### Public servers backed by this release

- `video.ninjamzap.com:2049` — public open room.
- `video.ninjamzap.com:2050` — secondary public open room.
- Both with `MaxChannels 32 8` so third-party clients can exercise
  video against them without hitting the anon cap.

### Docs

- [`docs/VIDEO_SUPPORT.md`](docs/VIDEO_SUPPORT.md) — protocol layer for
  video channels, congestion control, drop policy.
- [`docs/PRIVATE_ROOMS.md`](docs/PRIVATE_ROOMS.md) — `PrivateGroupMode`
  and `RoomPassword` setup.
- [`docs/NINJAM_AUTH.md`](docs/NINJAM_AUTH.md) — authentication
  reference for the wire protocol.
- [`ninjam/server/example.cfg`](ninjam/server/example.cfg) — annotated
  reference config covering all options.

### Known limitations

- No top-level CMake build yet; the existing Makefile drives the
  build (`MAC=1 make` on macOS, plain `make` on Linux).
- Wire format documented and stable across this 0.x line in intent,
  but not guaranteed stable until `1.0.0`.
