# Private (invite-only) rooms

How NinjamZap's private rooms work — both the protocol extension that
makes them possible and the end-to-end invite-link flow.

For background on how NINJAM auth works in general (per-user model,
permission flags, ACL, anonymous handling, PrivateGroupMode), read
[NINJAM_AUTH.md](NINJAM_AUTH.md) first.

---

## The problem

Upstream NINJAM authenticates per user. To run an invite-only room the
operator has to add a `User <name> <pass>` line per invitee, redeploy,
share each credential out-of-band. That's manageable for two friends
and hostile for any social graph beyond that — if Bernd wants to bring
two more musicians he has to ask the operator to provision them
manually.

A Zoom-style model — one shared room password, each guest picks their
own nickname — fits the social use case much better, but vanilla
NINJAM doesn't ship it. So we added it as a fork extension.

## The fix: `RoomPassword` directive

New cfg directive (parser in `ninjamsrv.cpp:305-352`, auth fallback in
the `localUserInfoLookup::Run()` method around `ninjamsrv.cpp:260-295`):

```cfg
RoomPassword <password> [flags]
```

Semantics:

- Any client whose supplied password hashes against this shared secret
  is authenticated, **regardless of nickname**. Explicit `User <name>`
  entries take precedence in the username-lookup loop, so a guest
  can't impersonate the admin by typing the same nickname.
- The granted privilege set defaults to `PRIV_CHATSEND | PRIV_VOTE`
  (`CV`) — same as a no-flags `User` entry. Pass `[flags]` (`T`, `C`,
  `B`, `K`, `R`, `M`, `V`, `P`) to change this.
- `PRIV_HIDDEN` (`H`) is rejected at parse time. A shared password
  granting hidden status would let any invitee make themselves
  invisible, defeating user-list utility for everyone in the room.
- Empty / unset means "feature disabled" — vanilla NINJAM auth is
  unchanged, so the directive is a no-op for public rooms that don't
  set it.

Hash construction matches the per-user path:
`SHA1(nickname + ":" + room_password)`. The secret never crosses the
wire in cleartext.

### Three-tier permission model

A typical private cfg ends up looking like this:

| Tier | Cfg shape | Who | Privs |
|------|-----------|-----|-------|
| **Owner** | `User admin <pass> *` | operator (one fixed nickname, one fixed password) | full admin |
| **Co-host** (optional) | `User <name> <pass> CBTK` | specific people | chat + topic + BPM + kick |
| **Guest** | `RoomPassword <pass> CV` | anyone with the invite link | chat + vote |

The owner connects manually with their dedicated account. Guests use
the invite link (see below). Co-hosts get their own dedicated user
entry too — they don't use the shared invite link, they use their
own personal credential.

## End-to-end invite-link flow

1. Operator publishes `https://www.ninjamzap.com/join?server=<host>&port=<n>&pass=<secret>`
   — a Universal Link backed by `apple-app-site-association` at
   `/.well-known/apple-app-site-association` (`paths: ["/join*"]`)
2. Guest taps the link from email / iMessage / wherever:
   - iOS with the app installed → universal link opens NinjamZap
     directly, skipping the web page
   - iOS without the app or desktop → the `/join` page loads, shows
     a "Get the app" CTA, and a fallback `ninjamzap://join?…` custom
     scheme link
3. App receives the URL via `Linking.addEventListener('url')` in
   `App.tsx` (cold start: `getInitialURL()`)
4. Handler parses `server`, `port`, `pass` and calls
   `navigationRef.navigate('Connection', { server, port, pass })`
5. `ConnectionScreen` autofills the host and password fields and
   opens the password section. The user picks a nickname and taps
   Connect.
6. The client sends `(nickname, room_pass)` to the server. The
   `RoomPassword` fallback admits the connection.

### Per-room password autosave

`ConnectionScreen` also persists the password locally so the guest
doesn't have to keep the invite link around. Keyed by `host:port` in
AsyncStorage (`@room_password:<host>:<port>`). Saved on successful
connect, restored on:

- mount (from `@last_host`)
- server-browser pick
- explicit deep-link with no `pass` param (link wins over saved if
  it does carry a pass — assume the owner rotated the secret)
- onBlur of the host field (when the field has no password yet)

Cleared automatically when the user connects to the same host with
no password (interpreted as "this room is anonymous now").

### Share button

`ConnectionScreen.handleShareInvite` and `SessionScreen.handleShareInvite`
compose the same `https://www.ninjamzap.com/join?…` URL and pass it
to `Share.share`. The session screen reads the password from the
AsyncStorage entry (since it doesn't own the ConnectionScreen state).

Same Zoom-style tradeoff applies: anyone who gets the link can join
until the operator rotates `ROOM_INVITE_PASS`.

## Deploy workflow

The real `private-*.cfg` never lands in the public repo
(`.gitignore` rule + `configs/private-*.cfg.example` template).
Credentials live in Fly secrets and are rendered into the cfg at
container startup by `configs/entrypoint.sh`:

```bash
flyctl secrets set \
  PRIVATE_ADMIN_USER=admin \
  PRIVATE_ADMIN_PASS=<long-random> \
  ROOM_INVITE_PASS=<word-style> \
  -a ninjamzap-server
flyctl deploy -a ninjamzap-server
```

Order matters: `flyctl secrets set` alone restarts the machines with
the *current* image. If the current image doesn't yet have the
`RoomPassword` feature, the new directive in the rendered cfg will
be ignored (or, depending on the binary version, log a warning).
Run `flyctl deploy` to push the binary first, or do both in
sequence.

Rotate the password:

```bash
flyctl secrets set ROOM_INVITE_PASS=<new-pass> -a ninjamzap-server
```

This restarts the machine and re-renders the cfg without rebuilding
the binary. Cached AsyncStorage values on existing guest devices
keep working until they reconnect; after that they get
"invalid login/password" and need a fresh invite link.

## Port allocation

| Port | Cfg | Purpose | Anonymous |
|------|-----|---------|-----------|
| 2049 | `default.cfg` | Public default room | yes |
| 2050 | `default-2050.cfg` | Public secondary | yes |
| 2090 | `private-2090.cfg.example` → rendered | Invite-only (RoomPassword) | no |

Convention going forward: `2049–2079` for public, `2080+` for private.
The Fly app exposes all three ports on the same machine; `fly.toml`
has a `[[services]]` block per port and the container is sized for
the combined worst-case load (`memory = 1024mb`).

## What we deliberately didn't build

- **Per-invite single-use tokens.** The link carries the room
  password verbatim. Rotating the secret invalidates *all* outstanding
  invites, which is the same blast radius as a Zoom room password.
  Single-use tokens require a backend (`hs-backend`) that mints +
  redeems them and a pool of disposable user accounts in the cfg.
  We opted to defer that — see Path B-full in the design discussion
  on `develop` (commit messages around `c0d…`) for the alternative.
- **In-app "Share Invite" with quota.** The share button works today,
  but there's no per-tier "you can invite N people / month" gate. We
  intentionally left this out of v1 so Bernd can freely forward links
  to his friends without bumping into entitlement walls.
- **Per-room owner concept exposed in the protocol.** NINJAM doesn't
  have it; we didn't add one. Owner is whoever holds `*` privs.
- **PrivateGroupMode + RoomPassword combination.** Both features
  exist in the fork but the interaction hasn't been exercised. If
  you want one server with multiple isolated invite-only rooms, this
  is the next thing to test.

## Reading order

1. [NINJAM_AUTH.md](NINJAM_AUTH.md) — generic NINJAM auth concepts
2. This document — our extension and operations
3. [VIDEO_SUPPORT.md](VIDEO_SUPPORT.md) — the other fork extension
4. `ninjam/server/ninjamsrv.cpp` lines `260-295` (auth fallback)
   and `305-352` (cfg parser) — the source of truth
