"""
player_ipc.py - how player processes find and control each other.

Every running player registers <cache>/players/<pid>.json with its current
state and the localhost port of a tiny command server. Anything (the Now
Playing window, the tray icon, another player handling a media key) can then:

    for p in running_players(): ...
    send_command(p, "play_pause")        # or next, prev, stop, seek <sec>, raise

The protocol is one JSON line each way over 127.0.0.1. Each registration
carries a random token that callers must echo, so only processes that can
read the user's cache directory can control players.

No Tk in here: the player side hands commands to Tk via a callback.
"""
import json
import os
import secrets
import socket
import socketserver
import threading
import time
from pathlib import Path

from config import get_cache_directory

STALE_AFTER = 6.0  # seconds without a heartbeat -> considered gone


def registry_dir():
    d = Path(get_cache_directory()) / "players"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _pid_alive(pid):
    try:
        if os.name == "nt":
            import ctypes
            h = ctypes.windll.kernel32.OpenProcess(0x1000, False, int(pid))  # QUERY_LIMITED_INFORMATION
            if not h:
                return False
            code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
            ctypes.windll.kernel32.CloseHandle(h)
            return code.value == 259  # STILL_ACTIVE
        os.kill(int(pid), 0)
        return True
    except (OSError, ValueError):
        return False


def running_players():
    """Registered, live players, most recently active first."""
    out = []
    now = time.time()
    for f in registry_dir().glob("*.json"):
        try:
            info = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if now - info.get("updated", 0) > STALE_AFTER and not _pid_alive(info.get("pid", -1)):
            try:
                f.unlink()
            except OSError:
                pass
            continue
        if now - info.get("updated", 0) > STALE_AFTER * 3:
            continue  # alive but hung; don't route commands to it
        out.append(info)
    out.sort(key=lambda i: i.get("last_active", 0), reverse=True)
    return out


def active_player(exclude_pid=None):
    """The player a global command (media key, tray) should go to:
    the most recently active one that is playing or paused, else the newest."""
    players = [p for p in running_players() if p.get("pid") != exclude_pid]
    for p in players:
        if p.get("status") in ("Playing", "Paused"):
            return p
    return players[0] if players else None


def send_command(info, cmd, arg=None, timeout=1.5):
    """Send a command to a registered player; returns its reply dict or None."""
    try:
        with socket.create_connection(("127.0.0.1", int(info["port"])), timeout=timeout) as s:
            s.sendall((json.dumps({"token": info["token"], "cmd": cmd, "arg": arg}) + "\n").encode())
            data = b""
            while not data.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                data += chunk
        return json.loads(data.decode() or "null")
    except Exception:
        return None


class _Handler(socketserver.StreamRequestHandler):
    def handle(self):
        try:
            line = self.rfile.readline(65536)
            req = json.loads(line.decode())
        except Exception:
            return
        server = self.server
        if not secrets.compare_digest(str(req.get("token", "")), server.token):
            self.wfile.write(b'{"ok": false, "error": "bad token"}\n')
            return
        try:
            ok = server.on_command(req.get("cmd"), req.get("arg"))
        except Exception as e:
            self.wfile.write((json.dumps({"ok": False, "error": str(e)}) + "\n").encode())
            return
        self.wfile.write((json.dumps({"ok": bool(ok is not False)}) + "\n").encode())


class _Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


class PlayerRegistration:
    """The player side: command server + heartbeat file.

    on_command(cmd, arg) is called on a server thread; the Tk player passes a
    function that queues the command for its main loop.
    """

    def __init__(self, kind, on_command):
        self.pid = os.getpid()
        self.token = secrets.token_hex(16)
        self.server = _Server(("127.0.0.1", 0), _Handler)
        self.server.token = self.token
        self.server.on_command = on_command
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.file = registry_dir() / f"{self.pid}.json"
        self.state = {"pid": self.pid, "port": self.port, "token": self.token, "kind": kind,
                      "started": time.time(), "last_active": time.time()}
        self.publish()

    def publish(self, **state):
        self.state.update(state)
        self.state["updated"] = time.time()
        tmp = self.file.with_suffix(".tmp")
        try:
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # token: owner only
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(self.state))
            os.replace(tmp, self.file)
        except OSError:
            pass

    def close(self):
        try:
            self.server.shutdown()
            self.server.server_close()
        except Exception:
            pass
        try:
            self.file.unlink()
        except OSError:
            pass
