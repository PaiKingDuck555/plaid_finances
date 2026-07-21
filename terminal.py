"""
terminal.py — the "waiter".

Takes keystrokes the browser sends over Socket.IO, carries them to the Pi by
opening an SSH connection *through the local Tailscale SOCKS5 proxy*, and
streams the Pi's output back to the browser.

Wire it into the app:

    from flask_socketio import SocketIO
    from terminal import init_terminal

    socketio = SocketIO(app, async_mode="threading", cors_allowed_origins=[])
    init_terminal(socketio)

Run with ONE gunicorn worker — the SSH session lives in-process, tied to the
Socket.IO session id.
"""

import os

import paramiko
import socks
from flask import request, session


def _cfg():
    return {
        "host": os.environ.get("PI_TAILSCALE_IP"),
        "port": int(os.environ.get("PI_SSH_PORT", "22")),
        "user": os.environ.get("PI_SSH_USER"),
        "password": os.environ.get("PI_SSH_PASSWORD"),
        "socks_host": os.environ.get("TS_SOCKS_HOST", "127.0.0.1"),
        "socks_port": int(os.environ.get("TS_SOCKS_PORT", "1055")),
        "owner_id": (os.environ.get("OWNER_GITHUB_ID") or os.environ.get("ALLOWED_GITHUB_ID") or "").strip(),
    }


# sid -> (transport, channel), so input/resize handlers can find the shell.
_sessions: dict[str, tuple] = {}


def _open_ssh():
    """Open an SSH shell to the Pi, routed over the Tailscale SOCKS5 proxy."""
    c = _cfg()
    if not (c["host"] and c["user"] and c["password"]):
        raise RuntimeError("Pi is not configured (PI_TAILSCALE_IP / PI_SSH_USER / PI_SSH_PASSWORD)")

    sock = socks.socksocket()
    sock.set_proxy(socks.SOCKS5, c["socks_host"], c["socks_port"])
    sock.settimeout(15)
    sock.connect((c["host"], c["port"]))  # travels the tailnet

    transport = paramiko.Transport(sock)
    transport.start_client(timeout=15)
    transport.auth_password(c["user"], c["password"])

    chan = transport.open_session()
    chan.get_pty(term="xterm-256color", width=120, height=32)
    chan.invoke_shell()
    chan.settimeout(0.0)  # non-blocking reads for the pump
    return transport, chan


def _is_owner():
    """Only the allowlisted GitHub id may open a shell to the Pi."""
    owner_id = _cfg()["owner_id"]
    if not owner_id:
        # No owner pinned — fall back to the OAuth page gate (session login set).
        return bool(session.get("github_login"))
    return str(session.get("github_id")) == str(owner_id)


def init_terminal(socketio):
    ns = "/terminal"

    @socketio.on("connect", namespace=ns)
    def _connect():
        # Page being OAuth-gated is NOT enough — a socket can be opened
        # directly, so re-check identity here before touching the Pi.
        if not _is_owner():
            return False  # rejects the socket connection

        sid = request.sid
        try:
            transport, chan = _open_ssh()
        except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
            socketio.emit("output", f"\r\n[connect failed: {exc}]\r\n",
                          namespace=ns, to=sid)
            return False

        _sessions[sid] = (transport, chan)
        socketio.start_background_task(_pump, socketio, sid, chan)

    @socketio.on("input", namespace=ns)
    def _input(data):
        entry = _sessions.get(request.sid)
        if entry:
            try:
                entry[1].send(data)
            except Exception:  # noqa: BLE001
                pass

    @socketio.on("resize", namespace=ns)
    def _resize(dims):
        entry = _sessions.get(request.sid)
        if entry:
            try:
                entry[1].resize_pty(width=int(dims["cols"]), height=int(dims["rows"]))
            except Exception:  # noqa: BLE001
                pass

    @socketio.on("disconnect", namespace=ns)
    def _disconnect():
        entry = _sessions.pop(request.sid, None)
        if entry:
            try:
                entry[1].close()
                entry[0].close()
            except Exception:  # noqa: BLE001
                pass


def _pump(socketio, sid, chan):
    """Read from the Pi's shell and push it to the browser, forever."""
    ns = "/terminal"
    while True:
        socketio.sleep(0.01)
        if chan.recv_ready():
            try:
                data = chan.recv(4096)
            except Exception:  # noqa: BLE001
                break
            if not data:
                break
            socketio.emit("output", data.decode(errors="replace"),
                          namespace=ns, to=sid)
        elif chan.exit_status_ready():
            break
    socketio.emit("output", "\r\n[session closed]\r\n", namespace=ns, to=sid)
