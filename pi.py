"""
pi.py — SSH/SFTP helpers for the Raspberry Pi, over the Tailscale SOCKS5 proxy.

Everything here opens a fresh, short-lived connection to the Pi, runs one job
(health snapshot, directory listing, download, upload), and closes. The
interactive shell (long-lived) lives in terminal.py instead.

All config comes from environment variables — no secrets are hardcoded.
"""

import io
import os
import posixpath
import stat

import paramiko
import socks


def _cfg():
    """Read Pi connection settings from the environment (lazily, per call)."""
    return {
        "host": os.environ.get("PI_TAILSCALE_IP"),
        "port": int(os.environ.get("PI_SSH_PORT", "22")),
        "user": os.environ.get("PI_SSH_USER"),
        "password": os.environ.get("PI_SSH_PASSWORD"),
        "socks_host": os.environ.get("TS_SOCKS_HOST", "127.0.0.1"),
        "socks_port": int(os.environ.get("TS_SOCKS_PORT", "1055")),
        "home": os.environ.get("PI_HOME", "/home/" + (os.environ.get("PI_SSH_USER") or "pi")),
    }


def is_configured() -> bool:
    c = _cfg()
    return bool(c["host"] and c["user"] and c["password"])


def _socket():
    """A TCP socket to the Pi, routed through the local Tailscale SOCKS5 proxy."""
    c = _cfg()
    sock = socks.socksocket()
    sock.set_proxy(socks.SOCKS5, c["socks_host"], c["socks_port"])
    sock.settimeout(15)
    sock.connect((c["host"], c["port"]))
    return sock


def _client() -> paramiko.SSHClient:
    """Open a password-authenticated SSH client to the Pi over the tailnet."""
    c = _cfg()
    if not is_configured():
        raise RuntimeError("Pi is not configured (set PI_TAILSCALE_IP / PI_SSH_USER / PI_SSH_PASSWORD)")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=c["host"],
        port=c["port"],
        username=c["user"],
        password=c["password"],
        sock=_socket(),
        timeout=15,
        banner_timeout=15,
        auth_timeout=15,
        allow_agent=False,
        look_for_keys=False,
    )
    return client


# ——— Health ———

# One round trip: emit key=value lines that Python parses. CPU% needs two
# /proc/stat samples 0.3s apart to compute utilisation.
_HEALTH_CMD = r"""
echo "model=$(tr -d '\0' </proc/device-tree/model 2>/dev/null || echo unknown)"
echo "hostname=$(hostname)"
echo "os=$(. /etc/os-release 2>/dev/null; echo ${PRETTY_NAME:-Linux})"
echo "kernel=$(uname -r)"
echo "uptime=$(cut -d. -f1 /proc/uptime)"
echo "loadavg=$(cut -d' ' -f1-3 /proc/loadavg)"
echo "ncpu=$(nproc)"
read a b c d e f g h i j < /proc/stat
t1=$((a+b+c+d+e+f+g)); idle1=$d
sleep 0.3
read a b c d e f g h i j < /proc/stat
t2=$((a+b+c+d+e+f+g)); idle2=$d
echo "cpu_total_delta=$((t2-t1))"
echo "cpu_idle_delta=$((idle2-idle1))"
awk '/MemTotal/{t=$2}/MemAvailable/{a=$2}END{print "mem_total_kb="t; print "mem_avail_kb="a}' /proc/meminfo
df -kP / | awk 'NR==2{print "disk_total_kb="$2; print "disk_used_kb="$3}'
echo "temp_milli=$(cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null || echo 0)"
"""


def get_health() -> dict:
    """Return a health snapshot of the Pi, or {reachable: False, error: ...}."""
    try:
        client = _client()
    except Exception as exc:  # noqa: BLE001
        return {"reachable": False, "error": str(exc)}
    try:
        _in, out, _err = client.exec_command(_HEALTH_CMD, timeout=20)
        raw = out.read().decode(errors="replace")
    except Exception as exc:  # noqa: BLE001
        return {"reachable": False, "error": str(exc)}
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001
            pass

    kv: dict[str, str] = {}
    for line in raw.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            kv[k.strip()] = v.strip()

    def _int(key, default=0):
        try:
            return int(kv.get(key, default))
        except (TypeError, ValueError):
            return default

    total_delta = _int("cpu_total_delta")
    idle_delta = _int("cpu_idle_delta")
    cpu_pct = 0.0
    if total_delta > 0:
        cpu_pct = round(100.0 * (total_delta - idle_delta) / total_delta, 1)

    mem_total = _int("mem_total_kb")
    mem_avail = _int("mem_avail_kb")
    mem_used = max(mem_total - mem_avail, 0)

    disk_total = _int("disk_total_kb")
    disk_used = _int("disk_used_kb")

    temp_c = round(_int("temp_milli") / 1000.0, 1) if _int("temp_milli") else None

    return {
        "reachable": True,
        "model": kv.get("model") or "Raspberry Pi",
        "hostname": kv.get("hostname"),
        "os": kv.get("os"),
        "kernel": kv.get("kernel"),
        "uptime_seconds": _int("uptime"),
        "loadavg": kv.get("loadavg"),
        "ncpu": _int("ncpu", 1),
        "cpu_percent": cpu_pct,
        "mem_used_kb": mem_used,
        "mem_total_kb": mem_total,
        "mem_percent": round(100.0 * mem_used / mem_total, 1) if mem_total else 0,
        "disk_used_kb": disk_used,
        "disk_total_kb": disk_total,
        "disk_percent": round(100.0 * disk_used / disk_total, 1) if disk_total else 0,
        "temp_c": temp_c,
    }


# ——— File browser (SFTP), confined under PI_HOME ———

def _safe_path(path: str | None) -> str:
    """Resolve a requested path under PI_HOME, rejecting escapes via '..'."""
    home = _cfg()["home"].rstrip("/") or "/"
    if not path:
        return home
    # Treat everything as relative to home unless it's already under home.
    candidate = path if path.startswith("/") else posixpath.join(home, path)
    candidate = posixpath.normpath(candidate)
    if candidate != home and not candidate.startswith(home + "/"):
        raise ValueError("path outside allowed home directory")
    return candidate


def list_dir(path: str | None = None) -> dict:
    """List a directory on the Pi. Returns {path, parent, entries[]}."""
    target = _safe_path(path)
    home = _cfg()["home"].rstrip("/") or "/"
    client = _client()
    try:
        sftp = client.open_sftp()
        entries = []
        for attr in sftp.listdir_attr(target):
            if attr.filename.startswith("."):
                continue  # hide dotfiles for a cleaner view
            is_dir = stat.S_ISDIR(attr.st_mode)
            entries.append({
                "name": attr.filename,
                "path": posixpath.join(target, attr.filename),
                "is_dir": is_dir,
                "size": attr.st_size,
                "mtime": attr.st_mtime,
            })
        entries.sort(key=lambda e: (not e["is_dir"], e["name"].lower()))
    finally:
        client.close()

    parent = posixpath.dirname(target)
    if target == home or not target.startswith(home):
        parent = None
    elif len(parent) < len(home):
        parent = home
    return {"path": target, "parent": parent, "home": home, "entries": entries}


def open_download(path: str):
    """Return (filename, size, stream, closer) for streaming a file down.

    The caller must invoke closer() when finished to release the SSH session.
    """
    target = _safe_path(path)
    client = _client()
    sftp = client.open_sftp()
    attr = sftp.stat(target)
    if stat.S_ISDIR(attr.st_mode):
        client.close()
        raise ValueError("cannot download a directory")
    remote = sftp.open(target, "rb")
    remote.prefetch()

    def _closer():
        try:
            remote.close()
        finally:
            client.close()

    return posixpath.basename(target), attr.st_size, remote, _closer


def upload(dir_path: str | None, filename: str, fileobj) -> dict:
    """Upload a file object into a directory on the Pi."""
    safe_name = posixpath.basename(filename or "upload.bin")
    if not safe_name or safe_name in (".", ".."):
        raise ValueError("invalid filename")
    target_dir = _safe_path(dir_path)
    dest = posixpath.join(target_dir, safe_name)
    # dest must still be inside home
    _safe_path(dest)
    client = _client()
    try:
        sftp = client.open_sftp()
        sftp.putfo(fileobj, dest)
        attr = sftp.stat(dest)
    finally:
        client.close()
    return {"ok": True, "path": dest, "size": attr.st_size}
