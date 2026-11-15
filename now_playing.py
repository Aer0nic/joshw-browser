"""
now_playing.py - one window (and a tray icon) to control every player.

Players stay separate processes with their own windows; this reads the
player registry (player_ipc) once a second and sends commands back.

    NowPlayingWindow(parent)          the mini controller
    TrayIcon(app, on_show, on_now_playing, on_quit)   system tray menu (pystray)
"""
import os
import threading
import tkinter as tk

import ttkbootstrap as tb

from player_ipc import active_player, running_players, send_command

KIND_NAMES = {"gsf": "GSF", "usf": "USF", "2sf": "2SF", "psf": "PSF", "gme": "GME", "sid": "SID",
              "mpv": "MPV", "midi": "MIDI", "arcade": "Arcade", "vgmstream": "vgmstream"}
ART_SIZE = 160


def _fmt(seconds):
    s = int(max(0, seconds or 0))
    return f"{s // 3600}:{(s % 3600) // 60:02d}:{s % 60:02d}" if s >= 3600 else f"{s // 60}:{s % 60:02d}"


class NowPlayingWindow(tb.Toplevel):
    _instance = None

    @classmethod
    def show(cls, parent):
        if cls._instance is not None and cls._instance.winfo_exists():
            cls._instance.deiconify()
            cls._instance.lift()
            return cls._instance
        cls._instance = cls(parent)
        return cls._instance

    def __init__(self, parent):
        super().__init__(parent)
        self.title("Now Playing")
        self.geometry("560x340")
        self.minsize(460, 300)
        self.selected_pid = None
        self._players = []
        self._art_path = None
        self._art_photo = None
        self._seeking = False

        outer = tb.Frame(self, padding=10)
        outer.pack(fill="both", expand=True)

        top = tb.Frame(outer)
        top.pack(fill="x")
        self.art_label = tb.Label(top, anchor="center")
        self.art_label.pack(side="left", padx=(0, 12))
        self._blank_art()

        info = tb.Frame(top)
        info.pack(side="left", fill="both", expand=True)
        self.title_var = tk.StringVar(value="Nothing playing")
        self.game_var = tk.StringVar()
        self.artist_var = tk.StringVar()
        self.status_var = tk.StringVar()
        tb.Label(info, textvariable=self.title_var, font=("Arial", 12, "bold"), wraplength=330,
                 justify="left").pack(anchor="w")
        tb.Label(info, textvariable=self.game_var, wraplength=330, justify="left").pack(anchor="w", pady=(2, 0))
        tb.Label(info, textvariable=self.artist_var, font=("Arial", 9, "italic"), wraplength=330,
                 justify="left").pack(anchor="w")
        tb.Label(info, textvariable=self.status_var, font=("Arial", 9)).pack(anchor="w", pady=(6, 0))

        self.pos_var = tk.DoubleVar(value=0.0)
        self.time_var = tk.StringVar(value="0:00 / 0:00")
        self.scale = tb.Scale(info, from_=0, to=100, variable=self.pos_var, orient="horizontal")
        self.scale.pack(fill="x", pady=(6, 0))
        self.scale.bind("<ButtonPress-1>", lambda e: setattr(self, "_seeking", True))
        self.scale.bind("<ButtonRelease-1>", self._on_seek_release)
        tb.Label(info, textvariable=self.time_var, font=("Arial", 9)).pack(anchor="e")

        btns = tb.Frame(info)
        btns.pack(anchor="w", pady=(6, 0))
        for text, cmd in (("⏮", "prev"), ("⏯", "play_pause"), ("⏹", "stop"), ("⏭", "next")):
            tb.Button(btns, text=text, width=3, command=lambda c=cmd: self.send(c)).pack(side="left", padx=2)
        tb.Button(btns, text="Show player", bootstyle="outline", command=lambda: self.send("raise")).pack(
            side="left", padx=(10, 0))

        tb.Label(outer, text="Running players:", font=("Arial", 9, "bold")).pack(anchor="w", pady=(10, 2))
        self.player_list = tk.Listbox(outer, height=4, exportselection=False)
        self.player_list.pack(fill="both", expand=True)
        self.player_list.bind("<<ListboxSelect>>", self._on_select_player)
        self.player_list.bind("<Double-1>", lambda e: self.send("raise"))

        self.after(100, self.refresh)

    # ------------------------------------------------------------------
    def _blank_art(self):
        from PIL import Image, ImageTk
        img = Image.new("RGB", (ART_SIZE, ART_SIZE), (60, 60, 60))
        self._art_photo = ImageTk.PhotoImage(img)
        self.art_label.configure(image=self._art_photo)
        self._art_path = None

    def _show_art(self, path):
        if path == self._art_path:
            return
        if not path or not os.path.exists(path):
            self._blank_art()
            return
        try:
            from PIL import Image, ImageTk
            img = Image.open(path).convert("RGB")
            img.thumbnail((ART_SIZE, ART_SIZE))
            canvas = Image.new("RGB", (ART_SIZE, ART_SIZE), (30, 30, 30))
            canvas.paste(img, ((ART_SIZE - img.width) // 2, (ART_SIZE - img.height) // 2))
            self._art_photo = ImageTk.PhotoImage(canvas)
            self.art_label.configure(image=self._art_photo)
            self._art_path = path
        except Exception:
            self._blank_art()

    def current(self):
        for p in self._players:
            if p.get("pid") == self.selected_pid:
                return p
        return self._players[0] if self._players else None

    def send(self, cmd, arg=None):
        p = self.current()
        if p:
            threading.Thread(target=send_command, args=(p, cmd, arg), daemon=True).start()
            self.after(300, self.refresh_once)

    def _on_seek_release(self, event):
        self._seeking = False
        p = self.current()
        if p and p.get("duration"):
            self.send("seek", self.pos_var.get() / 100.0 * float(p["duration"]))

    def _on_select_player(self, event):
        sel = self.player_list.curselection()
        if sel and sel[0] < len(self._players):
            self.selected_pid = self._players[sel[0]].get("pid")
            self.refresh_once()

    def refresh_once(self):
        self._players = running_players()
        if self.selected_pid not in [p.get("pid") for p in self._players]:
            act = active_player()
            self.selected_pid = act.get("pid") if act else None
        self.player_list.delete(0, "end")
        for i, p in enumerate(self._players):
            kind = KIND_NAMES.get(p.get("kind"), str(p.get("kind", "")).upper())
            self.player_list.insert("end", f"[{kind}] {p.get('title') or '-'}  ({p.get('status', '')})")
            if p.get("pid") == self.selected_pid:
                self.player_list.selection_set(i)
        p = self.current()
        if not p:
            self.title_var.set("Nothing playing")
            self.game_var.set("Start a track from the browser; it shows up here.")
            self.artist_var.set("")
            self.status_var.set("")
            self.time_var.set("0:00 / 0:00")
            self.pos_var.set(0)
            self._show_art(None)
            return
        self.title_var.set(p.get("title") or os.path.basename(str(p.get("file", ""))))
        game = p.get("album") or ""
        if p.get("year"):
            game = f"{game} ({p['year']})" if game else p["year"]
        self.game_var.set(game)
        self.artist_var.set(p.get("artist") or "")
        kind = KIND_NAMES.get(p.get("kind"), str(p.get("kind", "")).upper())
        self.status_var.set(f"{p.get('status', '')} - {kind} player")
        pos, dur = float(p.get("position") or 0), float(p.get("duration") or 0)
        self.time_var.set(f"{_fmt(pos)} / {_fmt(dur) if dur else '∞'}")
        if not self._seeking:
            self.pos_var.set(min(100.0, pos / dur * 100) if dur else 0)
        self._show_art(p.get("art"))

    def refresh(self):
        if not self.winfo_exists():
            return
        try:
            self.refresh_once()
        except Exception as e:
            print(f"[now playing] {e}")
        self.after(1000, self.refresh)


class TrayIcon:
    """System tray icon with playback controls (pystray, own thread).

    Menu callbacks run on pystray's thread; anything touching Tk is handed to
    `schedule` (e.g. a function that puts work on the browser's Tk queue).

    pystray's plain X11 backend (used on Linux unless PyGObject/AppIndicator
    is installed) has no menus: it only reports left clicks. There we catch
    the right click ourselves and call on_context_menu(x_root, y_root) via
    `schedule`, so the browser can pop up a Tk menu at the pointer.
    """

    def __init__(self, icon_path, schedule, on_show, on_now_playing, on_quit, on_context_menu=None):
        import pystray
        from PIL import Image
        self._pystray = pystray
        image = Image.open(icon_path) if icon_path and os.path.exists(icon_path) else Image.new("RGB", (64, 64), (40, 90, 160))

        def player(cmd):
            def run(icon, item):
                send_player_command(cmd)
            return run

        def tk_call(fn):
            return lambda icon, item: schedule(fn)

        menu = pystray.Menu(
            pystray.MenuItem("Show browser", tk_call(on_show), default=True),
            pystray.MenuItem("Now Playing...", tk_call(on_now_playing)),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Play / Pause", player("play_pause")),
            pystray.MenuItem("Next track", player("next")),
            pystray.MenuItem("Previous track", player("prev")),
            pystray.MenuItem("Stop", player("stop")),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit joshw-browser", tk_call(on_quit)),
        )
        self.icon = pystray.Icon("joshw-browser", image, "joshw-browser", menu)

        if not getattr(self.icon, "HAS_MENU", True) and on_context_menu is not None \
                and hasattr(self.icon, "_on_button_press"):
            original_press = self.icon._on_button_press

            def on_button_press(event):
                if getattr(event, "detail", None) == 3:  # right button
                    x, y = getattr(event, "root_x", 0), getattr(event, "root_y", 0)
                    schedule(lambda: on_context_menu(x, y))
                else:
                    original_press(event)

            self.icon._on_button_press = on_button_press
            handlers = getattr(self.icon, "_message_handlers", None)
            if isinstance(handlers, dict):
                import Xlib.X
                handlers[Xlib.X.ButtonPress] = on_button_press

        # Not run_detached(): on the X11 backend it starts the setup thread
        # twice, one of which waits forever, keeping the process alive after
        # quit and making stop() hang for 5 s. run() on our own daemon thread
        # starts it once, and a daemon thread never blocks exit.
        self.icon.SETUP_THREAD_TIMEOUT = 1.0
        self._thread = threading.Thread(target=self._run, name="tray-icon", daemon=True)
        self._thread.start()

    def _run(self):
        try:
            self.icon.run()
        except Exception as e:
            print(f"[tray] stopped: {e}")

    def stop(self):
        """Remove the icon without blocking the caller (usually the Tk thread)."""
        def work():
            try:
                self.icon.stop()
            except Exception:
                pass
        threading.Thread(target=work, name="tray-stop", daemon=True).start()


def send_player_command(cmd):
    """Send cmd to the most recently active player (from any thread)."""
    p = active_player()
    if p:
        send_command(p, cmd)
