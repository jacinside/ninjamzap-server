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

Both directives are optional and off by default: with no secret configured, room naming
behaves exactly as before.

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
