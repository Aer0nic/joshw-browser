"""
media_session.py - hook players into the OS's media controls.

Linux:   each player publishes an MPRIS2 service on the session D-Bus
         (org.mpris.MediaPlayer2.joshw.p<pid>). Desktop media keys, the
         sound applet / lock screen controls and `playerctl` then work, and
         the desktop routes keys to whichever player was used last.
         Needs the pure-Python `jeepney` package; silently disabled without it.

Windows: the first player to start registers the global media keys
         (Play/Pause, Next, Previous, Stop) with RegisterHotKey and forwards
         each press to the most recently active player (see player_ipc).
         When that player exits another one takes the keys over.

Both run on background threads and talk to the player through callbacks:
    on_command(cmd, arg)   cmd in play_pause/play/pause/next/prev/stop/seek/raise/quit
    get_state() -> dict    status ("Playing"/"Paused"/"Stopped"), title, artist,
                           album, position, duration, art (local file path)
"""
import os
import sys
import threading
import time


def start_media_session(on_command, get_state, identity="joshw-browser"):
    """Start the platform's media integration. Returns an object with
    .notify() (state changed) and .close(), or None if unavailable."""
    try:
        if sys.platform.startswith("win"):
            return WindowsMediaKeys(on_command)
        if sys.platform.startswith("linux"):
            return MprisService(on_command, get_state, identity)
    except Exception as e:
        print(f"[media] integration unavailable: {e}")
    return None


# ---------------------------------------------------------------------------
# Linux: MPRIS2 over D-Bus (jeepney)
# ---------------------------------------------------------------------------
MPRIS_PATH = "/org/mpris/MediaPlayer2"
ROOT_IFACE = "org.mpris.MediaPlayer2"
PLAYER_IFACE = "org.mpris.MediaPlayer2.Player"
PROPS_IFACE = "org.freedesktop.DBus.Properties"

INTROSPECTION = """<!DOCTYPE node PUBLIC "-//freedesktop//DTD D-BUS Object Introspection 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/introspect.dtd">
<node>
 <interface name="org.freedesktop.DBus.Introspectable">
  <method name="Introspect"><arg name="data" direction="out" type="s"/></method>
 </interface>
 <interface name="org.freedesktop.DBus.Properties">
  <method name="Get"><arg direction="in" type="s"/><arg direction="in" type="s"/><arg direction="out" type="v"/></method>
  <method name="GetAll"><arg direction="in" type="s"/><arg direction="out" type="a{sv}"/></method>
  <method name="Set"><arg direction="in" type="s"/><arg direction="in" type="s"/><arg direction="in" type="v"/></method>
  <signal name="PropertiesChanged"><arg type="s"/><arg type="a{sv}"/><arg type="as"/></signal>
 </interface>
 <interface name="org.mpris.MediaPlayer2">
  <method name="Raise"/><method name="Quit"/>
  <property name="CanQuit" type="b" access="read"/><property name="CanRaise" type="b" access="read"/>
  <property name="HasTrackList" type="b" access="read"/><property name="Identity" type="s" access="read"/>
  <property name="DesktopEntry" type="s" access="read"/>
  <property name="SupportedUriSchemes" type="as" access="read"/><property name="SupportedMimeTypes" type="as" access="read"/>
 </interface>
 <interface name="org.mpris.MediaPlayer2.Player">
  <method name="Next"/><method name="Previous"/><method name="Pause"/><method name="PlayPause"/>
  <method name="Stop"/><method name="Play"/>
  <method name="Seek"><arg direction="in" type="x"/></method>
  <method name="SetPosition"><arg direction="in" type="o"/><arg direction="in" type="x"/></method>
  <method name="OpenUri"><arg direction="in" type="s"/></method>
  <signal name="Seeked"><arg type="x"/></signal>
  <property name="PlaybackStatus" type="s" access="read"/><property name="Rate" type="d" access="readwrite"/>
  <property name="Metadata" type="a{sv}" access="read"/><property name="Volume" type="d" access="readwrite"/>
  <property name="Position" type="x" access="read"/><property name="MinimumRate" type="d" access="read"/>
  <property name="MaximumRate" type="d" access="read"/><property name="CanGoNext" type="b" access="read"/>
  <property name="CanGoPrevious" type="b" access="read"/><property name="CanPlay" type="b" access="read"/>
  <property name="CanPause" type="b" access="read"/><property name="CanSeek" type="b" access="read"/>
  <property name="CanControl" type="b" access="read"/>
 </interface>
</node>"""


class MprisService:
    def __init__(self, on_command, get_state, identity):
        import jeepney  # noqa: F401  (ImportError -> integration disabled)
        self.on_command = on_command
        self.get_state = get_state
        self.identity = identity
        self.bus_name = f"org.mpris.MediaPlayer2.joshw.p{os.getpid()}"
        self._stop = threading.Event()
        self._dirty = threading.Event()
        self._last_sent = {}
        self._track_no = 0
        self._last_track = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def notify(self):
        self._dirty.set()

    def close(self):
        self._stop.set()

    # -- property values -------------------------------------------------------
    def _metadata(self, st):
        track = (st.get("file"), st.get("title"))
        if track != self._last_track:
            self._last_track = track
            self._track_no += 1
        md = {"mpris:trackid": ("o", f"/org/joshw/track/{self._track_no}"),
              "xesam:title": ("s", st.get("title") or "")}
        if st.get("duration"):
            md["mpris:length"] = ("x", int(float(st["duration"]) * 1e6))
        if st.get("artist"):
            md["xesam:artist"] = ("as", [st["artist"]])
        if st.get("album"):
            md["xesam:album"] = ("s", st["album"])
        if st.get("art"):
            from pathlib import Path
            md["mpris:artUrl"] = ("s", Path(st["art"]).resolve().as_uri())
        return md

    def _props(self, iface):
        st = self.get_state() or {}
        if iface == ROOT_IFACE:
            return {"CanQuit": ("b", True), "CanRaise": ("b", True), "HasTrackList": ("b", False),
                    "Identity": ("s", self.identity), "DesktopEntry": ("s", "joshw-browser"),
                    "SupportedUriSchemes": ("as", ["file"]), "SupportedMimeTypes": ("as", [])}
        if iface == PLAYER_IFACE:
            status = st.get("status") or "Stopped"
            return {"PlaybackStatus": ("s", status), "Rate": ("d", 1.0),
                    "Metadata": ("a{sv}", self._metadata(st)),
                    "Volume": ("d", float(st.get("volume", 1.0))),
                    "Position": ("x", int(float(st.get("position") or 0) * 1e6)),
                    "MinimumRate": ("d", 1.0), "MaximumRate": ("d", 1.0),
                    "CanGoNext": ("b", True), "CanGoPrevious": ("b", True),
                    "CanPlay": ("b", True), "CanPause": ("b", True),
                    "CanSeek": ("b", bool(st.get("can_seek", True))), "CanControl": ("b", True)}
        return {}

    # -- D-Bus loop ------------------------------------------------------------
    def _run(self):
        from jeepney import DBusAddress, HeaderFields, MessageType, new_error, new_method_return, new_signal
        from jeepney.bus_messages import message_bus
        from jeepney.io.blocking import open_dbus_connection
        try:
            conn = open_dbus_connection(bus="SESSION")
            conn.send_and_get_reply(message_bus.RequestName(self.bus_name, 4))  # DO_NOT_QUEUE
        except Exception as e:
            print(f"[media] D-Bus unavailable: {e}")
            return
        props_addr = DBusAddress(MPRIS_PATH, interface=PROPS_IFACE)
        last_check = 0.0
        while not self._stop.is_set():
            try:
                msg = conn.receive(timeout=0.25)
            except TimeoutError:
                msg = None
            except Exception:
                break
            now = time.monotonic()
            if msg is None or now - last_check > 1.0:
                last_check = now
                self._emit_changes(conn, props_addr, new_signal)
            if msg is None or msg.header.message_type != MessageType.method_call:
                continue
            f = msg.header.fields
            iface, member = f.get(HeaderFields.interface), f.get(HeaderFields.member)
            try:
                reply = self._dispatch(msg, iface, member, new_method_return, new_error)
            except Exception as e:
                reply = new_error(msg, "org.freedesktop.DBus.Error.Failed", "s", (str(e),))
            if reply is not None:
                try:
                    conn.send(reply)
                except Exception:
                    break
        try:
            conn.close()
        except Exception:
            pass

    def _dispatch(self, msg, iface, member, new_method_return, new_error):
        body = msg.body
        if member == "Introspect":
            return new_method_return(msg, "s", (INTROSPECTION,))
        if iface == PROPS_IFACE:
            if member == "Get":
                props = self._props(body[0])
                if body[1] not in props:
                    return new_error(msg, "org.freedesktop.DBus.Error.UnknownProperty", "s", (body[1],))
                return new_method_return(msg, "v", (props[body[1]],))
            if member == "GetAll":
                return new_method_return(msg, "a{sv}", (self._props(body[0]),))
            if member == "Set":
                if body[1] == "Volume":
                    self.on_command("volume", float(body[2][1]))
                return new_method_return(msg)
        commands = {"Raise": "raise", "Quit": "quit", "Next": "next", "Previous": "prev",
                    "Pause": "pause", "PlayPause": "play_pause", "Stop": "stop", "Play": "play"}
        if member in commands:
            self.on_command(commands[member], None)
            self._dirty.set()
            return new_method_return(msg)
        if member == "Seek":
            st = self.get_state() or {}
            self.on_command("seek", max(0.0, float(st.get("position") or 0) + body[0] / 1e6))
            return new_method_return(msg)
        if member == "SetPosition":
            self.on_command("seek", max(0.0, body[1] / 1e6))
            return new_method_return(msg)
        if member == "OpenUri":
            return new_method_return(msg)
        return new_error(msg, "org.freedesktop.DBus.Error.UnknownMethod", "s", (str(member),))

    def _emit_changes(self, conn, props_addr, new_signal):
        """Send PropertiesChanged for status/metadata changes (not Position:
        clients extrapolate it, as the MPRIS spec intends)."""
        props = self._props(PLAYER_IFACE)
        watched = {k: props[k] for k in ("PlaybackStatus", "Metadata", "Volume", "CanSeek")}
        changed = {k: v for k, v in watched.items() if self._last_sent.get(k) != v}
        if not changed:
            self._dirty.clear()
            return
        self._last_sent.update(changed)
        try:
            conn.send(new_signal(props_addr, "PropertiesChanged", "sa{sv}as", (PLAYER_IFACE, changed, [])))
        except Exception:
            pass
        self._dirty.clear()


# ---------------------------------------------------------------------------
# Windows: global media keys
# ---------------------------------------------------------------------------
class WindowsMediaKeys:
    VK = {0xB3: "play_pause", 0xB0: "next", 0xB1: "prev", 0xB2: "stop"}
    WM_HOTKEY, WM_QUIT, MOD_NOREPEAT = 0x0312, 0x0012, 0x4000

    def __init__(self, on_key):
        """on_key(cmd) is called (on a background thread) for each press."""
        self.on_key = on_key
        self._tid = None
        self._stop = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def notify(self):
        pass

    def close(self):
        self._stop.set()
        if self._tid:
            import ctypes
            ctypes.windll.user32.PostThreadMessageW(self._tid, self.WM_QUIT, 0, 0)

    def _run(self):
        import ctypes
        from ctypes import wintypes
        user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
        self._tid = kernel32.GetCurrentThreadId()
        # Only one process can own a hotkey: retry until whoever has them exits
        while not self._stop.is_set():
            ok = [vk for i, vk in enumerate(self.VK) if user32.RegisterHotKey(None, i + 1, self.MOD_NOREPEAT, vk)]
            if ok:
                break
            time.sleep(3)
        if self._stop.is_set():
            return
        ids = {i + 1: cmd for i, cmd in enumerate(self.VK.values())}
        msg = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
            if msg.message == self.WM_HOTKEY and msg.wParam in ids:
                try:
                    self.on_key(ids[msg.wParam])
                except Exception:
                    pass
        for i in ids:
            user32.UnregisterHotKey(None, i)
