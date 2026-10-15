#!/usr/bin/env python3
"""Smoke tests for ninjamsrv over the real NINJAM wire protocol.

  smoke.py binary <path-to-ninjamsrv>
      Starts the binary with throwaway configs and checks anonymous logins,
      with and without ProxyProtocol, and private-room codes: signed codes
      join, retired ones are refused and close the room they opened.

  smoke.py image <host> <user> <pass> <port> [<port> ...]
      Logs in as the status user on each port of a running container (the
      deployed configs expect a PROXY protocol line, so one is sent first).
"""
import hashlib
import hmac
import os
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time

MSG_SERVER_AUTH_CHALLENGE = 0x00
MSG_SERVER_AUTH_REPLY = 0x01
MSG_CLIENT_AUTH_USER = 0x80
MSG_CHAT = 0xC0
MSG_KEEPALIVE = 0xFD
PROTOCOL_VERSION = 0x00020000

failures = []


def check(name, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + name + (f" ({detail})" if detail else ""))
    if not ok:
        failures.append(name)


class Conn:
    def __init__(self, host, port, timeout=15):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.buf = b""

    def send_raw(self, data):
        self.sock.sendall(data)

    def read_msg(self):
        """Returns (type, payload), or None when the server closes the connection."""
        while True:
            if len(self.buf) >= 5:
                t, n = self.buf[0], struct.unpack("<I", self.buf[1:5])[0]
                if len(self.buf) >= 5 + n:
                    payload = self.buf[5:5 + n]
                    self.buf = self.buf[5 + n:]
                    return t, payload
            try:
                data = self.sock.recv(65536)
            except (ConnectionResetError, socket.timeout):
                return None
            if not data:
                return None
            self.buf += data

    def close(self):
        self.sock.close()


def login(host, port, user, password="", header_parts=(), keep=False):
    """Returns the effective username the server accepted, or None if it refused
    the login or closed the connection before the auth challenge. With keep=True
    returns (name, conn) and leaves the connection open."""
    c = Conn(host, port)
    if keep:
        return _login(c, user, password, header_parts), c
    try:
        return _login(c, user, password, header_parts)
    finally:
        c.close()


def _login(c, user, password, header_parts):
    if True:
        for part in header_parts:
            c.send_raw(part)
            time.sleep(0.2)  # split across TCP segments on purpose
        msg = c.read_msg()
        if not msg or msg[0] != MSG_SERVER_AUTH_CHALLENGE:
            return None
        challenge = msg[1][:8]
        inner = hashlib.sha1(f"{user}:{password}".encode()).digest()
        passhash = hashlib.sha1(inner + challenge).digest()
        body = passhash + user.encode() + b"\0" + struct.pack("<II", 1, PROTOCOL_VERSION)
        c.send_raw(bytes([MSG_CLIENT_AUTH_USER]) + struct.pack("<I", len(body)) + body)
        msg = c.read_msg()
        if not msg:
            return None
        if msg[0] != MSG_SERVER_AUTH_REPLY:
            # The status user gets no auth reply: the server sends the room
            # info (user list, config or topic) and closes instead.
            return user
        if msg[1][0] != 1:
            return None
        return msg[1][1:].split(b"\0")[0].decode()


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def wait_listening(port, proc, seconds=10):
    deadline = time.time() + seconds
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            socket.create_connection(("127.0.0.1", port), timeout=1).close()
            return True
        except OSError:
            time.sleep(0.2)
    return False


def start_server(binary, workdir, proxy):
    port = free_port()
    cfg = os.path.join(workdir, f"proxy-{proxy}.cfg")
    with open(cfg, "w") as f:
        f.write(f"Port {port}\nMaxUsers 6\nAnonymousUsers yes\nAnonymousMaskIP yes\n"
                f"ProxyProtocol {'yes' if proxy else 'no'}\nACL 0.0.0.0/0 allow\n")
    log = open(os.path.join(workdir, f"proxy-{proxy}.log"), "w")
    proc = subprocess.Popen([binary, cfg], stdout=log, stderr=subprocess.STDOUT)
    if not wait_listening(port, proc):
        proc.kill()
        sys.exit(f"ninjamsrv did not start (ProxyProtocol {proxy}); see {log.name}")
    return proc, port


def run_binary(binary):
    host = "127.0.0.1"
    with tempfile.TemporaryDirectory() as workdir:
        proc, port = start_server(binary, workdir, proxy=False)
        try:
            name = login(host, port, "anonymous:plain")
            check("plain anonymous login", name == "plain@127.0.0.x", name)
        finally:
            proc.terminate()
            proc.wait(10)

        proc, port = start_server(binary, workdir, proxy=True)
        try:
            cases = [
                ("PROXY TCP4 source becomes the peer", [b"PROXY TCP4 95.210.9.44 172.16.0.2 51234 2049\r\n"], "v4@95.210.9.x"),
                ("PROXY header split across segments", [b"PROXY TCP4 81.2", b"3.4.5 172.16.0.2 1 2049\r\n"], "split@81.23.4.x"),
                ("PROXY TCP6 IPv4-mapped source", [b"PROXY TCP6 ::ffff:200.1.2.3 ::1 1 2049\r\n"], "mapped@200.1.2.x"),
                ("PROXY TCP6 native source keeps the peer", [b"PROXY TCP6 2001:db8::1 ::1 1 2049\r\n"], "v6@127.0.0.x"),
                ("PROXY UNKNOWN keeps the peer", [b"PROXY UNKNOWN\r\n"], "unk@127.0.0.x"),
            ]
            for title, parts, expected in cases:
                user = "anonymous:" + expected.split("@")[0]
                name = login(host, port, user, header_parts=parts)
                check(title, name == expected, name)

            name = login(host, port, "anonymous:garbage", header_parts=[b"GET / HTTP/1.0\r\n\r\n"])
            check("non-PROXY data is dropped", name is None, name)
            name = login(host, port, "anonymous:silent")
            check("missing PROXY header times out", name is None, name)
        finally:
            proc.terminate()
            proc.wait(10)

        run_rooms(binary, workdir)


# ── Private rooms ──────────────────────────────────────────────────────────
CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
ROOM_SECRET = "test-secret"


def mint_code(payload):
    """Same signing as hs-backend's room-token.ts and nzVerifyRoomToken."""
    d = hmac.new(ROOM_SECRET.encode(), payload.encode(), hashlib.sha1).digest()
    bits = (d[0] << 8) | d[1]
    return payload + "".join(CROCKFORD[(bits >> (5 * i)) & 31] for i in (2, 1, 0))


def revoked_fp(code):
    """Same as revokedFingerprint() in hs-backend's room-token.ts."""
    return hmac.new(ROOM_SECRET.encode(), f"revoked:{code}".encode(), hashlib.sha1).hexdigest()[:16]


def send_chat(c, text):
    body = b"MSG\0" + text.encode() + b"\0"
    c.send_raw(bytes([MSG_CHAT]) + struct.pack("<I", len(body)) + body)


def keep_alive(c):
    """Idle clients are dropped after a few missed keepalives; send them so a
    disconnect we observe is the server's doing, not a timeout."""
    def loop():
        try:
            while True:
                c.send_raw(bytes([MSG_KEEPALIVE, 0, 0, 0, 0]))
                time.sleep(2)
        except OSError:
            pass
    threading.Thread(target=loop, daemon=True).start()


def wait_chat(c, needle, seconds):
    """Reads until a server chat line contains `needle`. Returns 'found',
    'closed' (connection ended first) or 'timeout'."""
    c.sock.settimeout(seconds)
    deadline = time.time() + seconds
    while time.time() < deadline:
        msg = c.read_msg()
        if msg is None:
            return "closed" if time.time() < deadline else "timeout"
        if msg[0] == MSG_CHAT and needle.encode() in msg[1]:
            return "found"
    return "timeout"


def run_rooms(binary, workdir):
    host = "127.0.0.1"
    port = free_port()
    revoked = os.path.join(workdir, "revoked.txt")
    cfg = os.path.join(workdir, "rooms.cfg")
    with open(cfg, "w") as f:
        f.write(f"Port {port}\nMaxUsers 4\nAnonymousUsers yes\nACL 0.0.0.0/0 allow\n"
                f"PrivateGroupMode 4\nPrivateGroupLobbySize 8\nPrivateGroupAllowChat no\n"
                f"PrivateGroupPublicPrefix \"\"\nPrivateGroupTokenSecret {ROOM_SECRET}\n"
                f"PrivateGroupRevokedCodesFile {revoked}\n")
    log = open(os.path.join(workdir, "rooms.log"), "w")
    proc = subprocess.Popen([binary, cfg], stdout=log, stderr=subprocess.STDOUT)
    if not wait_listening(port, proc):
        proc.kill()
        sys.exit(f"ninjamsrv did not start (rooms); see {log.name}")
    conns = []
    try:
        check("fingerprint matches the backend vector", revoked_fp("K7QM2XA9") == "685565320861bd6b")
        live, retired = mint_code("M3MB5"), mint_code("R3T1R")  # Crockford only: no I, L, O, U

        def join(user, code):
            name, c = login(host, port, "anonymous:" + user, keep=True)
            conns.append(c)
            keep_alive(c)
            send_chat(c, code[:4] + "-" + code[4:].lower())  # dash and case are sugar
            return c

        # A room opened with a code before it was retired, to be emptied later.
        inside = join("inside", retired)
        check("signed code joins its room", wait_chat(inside, "joining room", 5) == "found")

        c = join("forger", "ABCDE" + "000")
        check("forged code is refused", wait_chat(c, "invalid room code", 5) == "found")

        with open(revoked + ".tmp", "w") as f:
            f.write("# ninjamzap revoked room codes v1\n" + revoked_fp(retired) + "\n")
        os.rename(revoked + ".tmp", revoked)

        check("open room under a retired code is told why",
              wait_chat(inside, "code was changed", 12) == "found")
        check("then its users are disconnected", wait_chat(inside, "never-sent", 5) == "closed")

        c = join("latecomer", retired)
        check("retired code is refused in the lobby", wait_chat(c, "no longer valid", 5) == "found")
        c = join("member", live)
        check("other codes still join", wait_chat(c, "joining room", 5) == "found")

        with open(revoked, "w") as f:
            f.write("garbage without the header\n")
        time.sleep(7)
        c = join("latecomer2", retired)
        check("a file without the header doesn't clear the list",
              wait_chat(c, "no longer valid", 5) == "found")
    finally:
        for c in conns:
            c.close()
        proc.terminate()
        proc.wait(10)


def run_image(host, user, password, ports):
    header = [b"PROXY TCP4 95.210.9.44 172.16.0.2 51234 2049\r\n"]
    for port in ports:
        name = None
        deadline = time.time() + 30  # the container renders configs and starts four servers
        while time.time() < deadline:
            try:
                name = login(host, int(port), user, password, header)
                if name:
                    break
            except OSError:
                pass
            time.sleep(1)
        check(f"status login on port {port}", name == user, name)
    name = login(host, int(ports[0]), user, password + "-wrong", header)
    check("wrong status password is refused", name is None, name)


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "binary":
        run_binary(sys.argv[2])
    elif len(sys.argv) >= 6 and sys.argv[1] == "image":
        run_image(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5:])
    else:
        sys.exit(__doc__)
    if failures:
        sys.exit(f"{len(failures)} check(s) failed")
    print("all checks passed")
