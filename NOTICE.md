# Attributions and Notices

## NinjamZap Server

**Copyright (C) 2026 Javier Alejandro Cordero**
Licensed under GPL v2 — see [LICENSE](LICENSE).

This is a derivative work of Cockos NINJAM (see "Based on" below) plus
the additions listed in "What's added in this fork".

---

## Based on Cockos NINJAM

**NINJAM** (Novel Intervallic Network Audio Monitor)
**Copyright (C) 2004–2025 Cockos Incorporated**
**Created by**: Justin Frankel

- Original source: https://ninjam.com (SVN / tarball)
- GitHub mirror: https://github.com/justinfrankel/ninjam
- WDL library (depended on): https://github.com/justinfrankel/WDL

NINJAM is GPL v2. This fork keeps that same license — no upgrade to GPL v3
— and preserves the original per-file copyright headers in every file
that came from upstream.

### Upstream base and connected history

`ninjam/` and `WDL/` started from upstream NINJAM commit `405036d2`
(2025-06-01) and have since merged upstream `main` up to `f4c0eff3`.
Every change made here to `ninjam/` is mirrored, with its original
author, date and message, on the `ninjamzap/server` branch of
https://github.com/jacinside/ninjam — a GitHub fork of the upstream
repository — so it can be compared with upstream and offered back as
pull requests. Fixes taken from upstream pull requests keep their
original author and link the pull request in the commit message.

### Files inherited largely intact from upstream

- `ninjam/server/usercon.h`, `usercon.cpp`
- `ninjam/server/ninjamsrv.cpp`
- `ninjam/server/Makefile`
- `ninjam/server/example.cfg`
- `ninjam/netmsg.h`, `netmsg.cpp`, `ninjam/mpb.h`, `mpb.cpp`
- `WDL/` — tree-wide, unmodified; Cockos's WDL library (Zlib/libpng
  license; bundled with the upstream NINJAM distribution), as of
  upstream NINJAM `405036d2`

Modifications inside these files are marked with "NinjamZap extension"
comments where they add behaviour, and described in the matching commit
messages and in [`CHANGELOG.md`](CHANGELOG.md).

---

## What's added in this fork

All new code below is GPL v2 (matching upstream):

| Area | Files | What it adds |
|------|-------|--------------|
| Video relay | hooks in `usercon.cpp`, new fourcc routing | Per-interval H264/VP8/MJPG channel passthrough with audio-priority two-pass scheduling and drop-on-congestion. |
| Thread-per-group | threading members in `usercon.h`, lifecycle in `ninjamsrv.cpp` | Optional per-room pthread when `PrivateGroupMode` is configured. Lobby stays on main thread. |
| `RoomPassword` directive | config parsing in `ninjamsrv.cpp` | Per-room password enforcement. |
| Signed room tokens | `PrivateGroupTokenSecret` / `PrivateGroupTokenMaxFail` in `ninjamsrv.cpp`, `usercon.cpp` | Only names signed with the operator's secret create private rooms; per-connection limit on bad codes. |
| Hidden bot slot | (already in upstream as `AllowHiddenUsers`; production cfg enables it) | Lets relay/recording bots connect without showing in user lists or counting in vote divisors. Shared room passwords can't grant hidden status. |
| Configurable anon channel cap | (already in upstream; production cfg sets it) | `MaxChannels 32 8` in production cfgs so anon users can run audio + video together. |
| Container deploy | `Dockerfile`, `configs/`, `deploy.sh`, `fly.toml` | Fly.io / generic container deployment with secrets rendering. |
| Documentation | `docs/VIDEO_SUPPORT.md`, `docs/PRIVATE_ROOMS.md`, `docs/NINJAM_AUTH.md`, `CHANGELOG.md` | Protocol and operator-facing reference docs. |

---

## License chain

- Cockos NINJAM upstream: **GPL v2 or (at your option) any later version**
  (per file headers).
- This fork: **GPL v2** — same as upstream, no upgrade. The "or later"
  option from upstream remains available to downstream consumers.
- Bundled WDL: **Zlib/libpng-style** permissive license. Compatible with
  GPL v2 redistribution.

See [LICENSE](LICENSE) for the GPL v2 full text.

---

## Contact

**NinjamZap Project**
Repository: https://github.com/jacinside/ninjamzap-server
Connected NINJAM fork: https://github.com/jacinside/ninjam (`ninjamzap/server` branch)
Mobile client: https://github.com/jacinside/ninjamzap-core
Public website: https://www.ninjamzap.com

**Original NINJAM**
Website: https://ninjam.com
GitHub: https://github.com/justinfrankel/ninjam
