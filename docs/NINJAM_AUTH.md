# NINJAM authentication & permissions — protocol reference

A pragmatic dive into how the upstream Cockos NINJAM server handles
auth, what roles exist, and how the user-list / lobby concepts work.
Written for the implementer who wants to know what they're working
with before extending it. Source: `ninjam/server/usercon.{h,cpp}` and
`ninjam/server/ninjamsrv.cpp` in this repo.

---

## The handshake

NINJAM auth is per-user, not per-room. There is no native concept of
"room password" — the room (a `User_Group` in the server) doesn't carry
credentials of its own; only individual user entries do.

1. Client opens a TCP connection.
2. Server sends a `ServerAuthChallenge` containing a random nonce (the
   challenge), the server license text, and the protocol version.
3. Client computes `SHA1(SHA1(username + ":" + password) + challenge)`
   and sends it with the username in `ClientAuthUser`.
4. Server looks the username up in its config:
   - matches an explicit `User <name> <pass> [flags]` line → expected
     hash is `SHA1(SHA1(name + ":" + pass) + challenge)`
   - matches the `StatusUserPass` user → status role (read-only)
   - starts with `anonymous` / `anonymous:nickname` and `AnonymousUsers
     yes` → expected hash skips the password (no `reqpass` set), the
     user is admitted with anonymous privileges
   - otherwise → reject with "invalid login/password"
5. On match, server replies `ServerAuthReply { flag: 1, maxchan, ... }`
   with the user's resolved channel cap and an optionally-renamed
   username (the server may suffix `-2`, `-3` etc to disambiguate
   duplicates).

The hash construction matters: the password never crosses the wire, so
operator credentials are safe against passive sniffing.

## Permission flags

`ninjam/server/usercon.h` defines nine bits. The cfg parser at
`ninjamsrv.cpp:447-479` accepts them as a string of letters:

| Flag | Bit | Cfg letter | Grants |
|------|-----|------------|--------|
| `PRIV_TOPIC` | 1 | `T` | change the room topic |
| `PRIV_CHATSEND` | 2 | `C` | send chat messages |
| `PRIV_BPM` | 4 | `B` | change BPM / BPI unilaterally |
| `PRIV_KICK` | 8 | `K` | kick other users |
| `PRIV_RESERVE` | 16 | `R` | reserved slot (never bumped by "server full") |
| `PRIV_ALLOWMULTI` | 32 | `M` | multiple logins from the same nickname |
| `PRIV_HIDDEN` | 64 | `H` | hidden user — doesn't appear in user list, doesn't count for the slot cap |
| `PRIV_VOTE` | 128 | `V` | vote in BPM/BPI changes |
| `PRIV_SHOW_PRIVATE` | 256 | `P` | see private rooms in status (only relevant under `PrivateGroupMode`) |

`*` is shorthand for "everything except `H`" — effectively `TCBKRMVP`.

Default (no flags letter): `PRIV_CHATSEND | PRIV_VOTE` (`CV`) — chat
and vote. Useful baseline for anything that isn't a moderator.

There is **no `PRIV_ROOMOWNER`** or similar concept. "Admin" is
emergent: a user with `*` who can kick, change topic, and force BPM
behaves like a room owner. The protocol doesn't enforce a single
owner.

## Admin commands (chat-driven)

Permission flags gate which chat commands the server will honor from a
user. The commands are sent as regular chat messages with a `!` prefix:

| Command | Required flag | Notes |
|---------|---------------|-------|
| `!kick <user>` | `K` | drops the target's connection |
| `!topic <text>` | `T` | sets the room topic |
| `!bpm <n>` | `B` | sets BPM directly (no vote) |
| `!bpi <n>` | `B` | sets BPI directly |
| `!vote bpm <n>` | `V` | proposes a BPM vote (threshold + timeout from cfg) |
| `!vote bpi <n>` | `V` | proposes a BPI vote |

There is no formal admin console. Operators run a chat command from a
client that's logged in as the `*`-privileged user.

## Anonymous handling

When `AnonymousUsers` is `yes`, clients can authenticate with no
password by sending `anonymous` or `anonymous:<nickname>` as the
username. The server then:

- skips the password requirement (`reqpass = 0`)
- uses the suffix after `anonymous:` as the display name, sanitised:
  16-char max, replaces `@` and `.` with `_`, appends `@<hostmask>`
  derived from the client IP
- if `AnonymousMaskIP yes` (recommended for public rooms) the last
  IPv4 octet becomes `.x` so the user list shows `nick@1.2.3.x`
- assigns `PRIV_CHATSEND` (if `AnonymousUsersCanChat yes`) +
  `PRIV_VOTE` + `PRIV_ALLOWMULTI` (if `AnonymousUsers multi`)

When `AnonymousUsers no` the anonymous path is rejected immediately —
this is the model used by invite-only rooms. The NinjamZap mobile
client used to *always* send `anonymous:` even when the user typed a
password; that's been fixed (Mobile c0d… in `develop`).

## Status users

A separate role from cfg: `StatusUserPass <name> <pass>` defines a
read-only account that exists only so the listing backend can probe
the room cheaply. Status users get `priv = 0` and `max_channels = 0`;
they never appear in the user list and can't speak. The NinjamZap
backend's `/server-list` endpoint uses this to fetch the per-room
status of `2049` / `2050` without consuming a real slot.

## Lobby & PrivateGroupMode

By default the server runs a single `User_Group` — the room. Every
authenticated user joins that one room. The word "lobby" appears in
`njclient.cpp:826` as a degenerate case: when the server has neither
`m_max_localch` nor any remote users yet, the client treats itself as
"in a lobby effectively" and runs the audio mix in monitor-only mode.

The NinjamZap fork added `PrivateGroupMode N`: a multi-room mode where
the main `User_Group` is the lobby and up to N additional groups can
be spawned on demand. Each private group runs on its own pthread to
avoid head-of-line blocking between rooms.

- `PrivateGroupMode N` — enable, with N as the max number of
  concurrent private rooms.
- `PrivateGroupPublicPrefix "open"` — names starting with this prefix
  are listed in the public lobby; everything else is hidden until a
  user joins by typing the exact room name.
- `PrivateGroupLobbySize N` — max users in the lobby.
- `PrivateGroupAllowChat yes/no` — let lobby occupants chat.
- `PrivateGroupLobbyMOTDFile path` — separate MOTD for the lobby.

Per-room password is **not** part of this. PrivateGroupMode hides
rooms by obscurity (knowing the name) but the auth layer is still
per-user. Combining PrivateGroupMode with the `RoomPassword` extension
(see [PRIVATE_ROOMS.md](PRIVATE_ROOMS.md)) is possible but hasn't been
tested in this repo.

## ACL

`ninjamsrv.cpp:413-446` parses `ACL <cidr> <allow|deny|reserve>` lines
in order. First match wins. `deny` drops the connection at the TCP
layer before any auth. `reserve` marks the slot so a "server full"
condition still admits this IP (used in dev with `192.168.0.0/16
reserve`).

ACLs don't carry credentials; they're a pre-auth network filter only.

## Practical implications

- Want a public anonymous server? `AnonymousUsers yes`, no `User`
  lines needed.
- Want one trusted operator + open public room? Combine
  `AnonymousUsers yes` with a single `User admin <pass> *` entry. The
  admin connects with the explicit account; everyone else stays
  anonymous.
- Want invite-only? Set `AnonymousUsers no` and add a `User` line per
  invitee. This is the upstream model — it doesn't scale beyond
  hand-managed friend groups, which motivates the `RoomPassword`
  extension.
- Want per-room privacy? Use `PrivateGroupMode` and don't publish the
  room names. (You're trading password security for obscurity, so
  anyone who guesses or leaks the room name can join.)
- Want a bot that doesn't take a slot? Give it a `User` entry with
  `H` (hidden) and set `AllowHiddenUsers yes`. Hidden users are also
  **excluded from the vote divisor** (`usercon.cpp:1492`:
  `if (!(p->m_auth_privs & PRIV_HIDDEN)) vucnt++;`), so a bot won't
  skew BPM/BPI quorum. Combine with `C` (chat) if the bot needs to
  post status messages; deliberately omit `V` so it can't `!vote` at
  all. This is how the NinjamZap audio-relay/recording bot
  (`ninjamzapbot`) connects to the public 2049/2050 rooms.
