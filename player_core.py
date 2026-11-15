"""
player_core.py - the shared engine every player window plugs into.

Each player stays its own process and window (own taskbar icon). Attaching
the core (player_launcher.run_player does it) adds, without touching the
player's own UI or playback code:

  * registration + remote control (player_ipc): Now Playing window, tray,
    and media keys can drive the player
  * OS media integration (media_session): MPRIS on Linux, media keys on Windows
  * play history (library.History) with play counts
  * per-track length/fade overrides: players that support them natively read
    library.get_override(); for the rest the core ends the track at the
    override length (with a fade where the backend allows it)
  * box art lookup for Now Playing / MPRIS
  * right-click menu on the playlist: Export to WAV/FLAC, Set length/fade,
    Open containing folder

The core reads the player's state from the attributes all players share
(audio_file, playlist, current_index, status_var, current_position_seconds,
total/raw_duration_seconds, *_listbox) and drives it through the methods
they share (toggle_play_pause, next_track, prev_track, stop,
seek_to_position). A player can refine this by defining:

  core_current_track()   -> path of the track playing ("file#N" for subtracks)
  core_track_for_index(i)-> path for playlist row i
  core_handles_overrides = True   (it applies length/fade overrides itself)
  core_restart_current() -> restart the current track (after an override change)
"""
import atexit
import os
import queue
import threading
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox

import ttkbootstrap as tb

import library
from media_session import start_media_session
from platform_support import IS_WINDOWS, open_path
from player_ipc import PlayerRegistration, active_player, send_command

PSF_FAMILY = {".psf", ".minipsf", ".psf2", ".minipsf2", ".gsf", ".minigsf", ".usf", ".miniusf",
              ".2sf", ".mini2sf", ".ssf", ".minissf", ".dsf", ".minidsf", ".ncsf", ".minincsf"}
# player kind / file extension -> box-art system key (library.SYSTEMS)
ART_SYSTEM_BY_EXT = {".gsf": "gsf", ".minigsf": "gsf", ".usf": "usf", ".miniusf": "usf",
                     ".2sf": "2sf", ".mini2sf": "2sf", ".psf": "psf", ".minipsf": "psf",
                     ".psf2": "psf2", ".minipsf2": "psf2", ".spc": "spc", ".nsf": "nsf", ".nsfe": "nsf",
                     ".gbs": "gbs", ".hes": "hes", ".ssf": "ssf", ".minissf": "ssf", ".dsf": "dsf",
                     ".minidsf": "dsf", ".kss": "kss"}
LISTBOX_ATTRS = ("playlist_listbox", "subtrack_listbox", "chapter_listbox")
PROC_ATTRS = ("gsf_proc", "process", "proc", "sid_proc", "vgmstream_proc", "mpv_proc",
              "vgmplay_proc")


def read_psf_tags(path):
    """[TAG] block of a PSF-family file as a dict (lower-case keys)."""
    tags = {}
    try:
        with open(path, "rb") as f:
            data = f.read()
        pos = data.rfind(b"[TAG]")
        if pos == -1:
            return tags
        for line in data[pos + 5:].decode("utf-8", "replace").splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                k = k.strip().lower()
                tags[k] = (tags[k] + "\n" + v.strip()) if k in tags else v.strip()
    except OSError:
        pass
    return tags


def _plain_path(track):
    """Strip a "#N" subtrack suffix (only when the bare file exists)."""
    track = str(track or "")
    if "#" in track:
        base = track.rsplit("#", 1)[0]
        if os.path.exists(base):
            return base
    return track


class PlayerCore:
    TICK_MS = 500

    def __init__(self, window, kind):
        self.w = window
        self.kind = kind
        self._commands = queue.Queue()
        self._snapshot = {"status": "Stopped"}
        self._closed = False
        self._track = None
        self._play_id = None
        self._last_progress_save = 0.0
        self._last_publish = 0.0
        self._tag_cache = {}
        self._art_cache = {}
        self._override = None
        self._override_state = None  # None / "fading" / "done"
        self.registration = PlayerRegistration(kind, self._queue_command)
        self.media = start_media_session(self._on_media_command, lambda: dict(self._snapshot),
                                         identity=f"joshw-browser ({kind.upper()} player)")
        window.bind("<FocusIn>", self._mark_active, add="+")
        self._attach_playlist_menu()
        atexit.register(self.close)
        window.bind("<Destroy>", self._on_destroy, add="+")
        window.after(self.TICK_MS, self._tick)

    # ------------------------------------------------------------------ commands
    def _queue_command(self, cmd, arg):
        """Called on IPC / D-Bus threads; executed on the Tk thread by _tick."""
        self._commands.put((cmd, arg))
        try:
            self.w.after(0, self._drain_commands)
        except Exception:
            pass
        return True

    def _on_media_command(self, cmd, arg=None):
        """MPRIS (already addressed to this player) or a Windows media key
        (global: route it to whichever player was used last)."""
        if IS_WINDOWS and cmd in ("play_pause", "next", "prev", "stop"):
            target = active_player()
            if target and target.get("pid") != os.getpid():
                send_command(target, cmd)
                return
        self._queue_command(cmd, arg)

    def _drain_commands(self):
        while True:
            try:
                cmd, arg = self._commands.get_nowait()
            except queue.Empty:
                return
            try:
                self.run_command(cmd, arg)
            except Exception as e:
                print(f"[core] command {cmd} failed: {e}")

    def run_command(self, cmd, arg=None):
        w = self.w
        status = self._status()
        if cmd == "play_pause":
            w.toggle_play_pause()
        elif cmd == "play":
            if status != "Playing":
                w.toggle_play_pause()
        elif cmd == "pause":
            if status == "Playing":
                w.toggle_play_pause()
        elif cmd == "next":
            w.next_track()
        elif cmd == "prev":
            w.prev_track()
        elif cmd == "stop":
            for name in ("stop", "stop_current_playback"):
                if hasattr(w, name):
                    getattr(w, name)()
                    break
        elif cmd == "seek" and arg is not None and hasattr(w, "seek_to_position"):
            w.seek_to_position(max(0.0, float(arg)))
        elif cmd == "raise":
            w.deiconify()
            w.lift()
            w.focus_force()
        elif cmd == "quit":
            (getattr(w, "close", None) or w.destroy)()
        elif cmd == "volume" and arg is not None:
            slider = getattr(w, "volume_slider", None)
            if slider is not None:
                slider.set(max(0, min(100, float(arg) * 100)))
        self._mark_active()
        self._tick(reschedule=False)

    def _mark_active(self, event=None):
        if event is not None and event.widget is not self.w:
            return
        self.registration.state["last_active"] = time.time()

    # --------------------------------------------------------------- state
    def current_track(self):
        w = self.w
        if hasattr(w, "core_current_track"):
            try:
                return w.core_current_track()
            except Exception:
                pass
        playlist = getattr(w, "playlist", None)
        idx = getattr(w, "current_index", None)
        if playlist and isinstance(idx, int) and 0 <= idx < len(playlist):
            entry = playlist[idx]
            if isinstance(entry, dict):
                entry = entry.get("file")
            if entry:
                if getattr(w, "total_chapters", 1) and getattr(w, "total_chapters", 1) > 1:
                    return f"{entry}#{idx + 1}"
                return str(entry)
        return str(getattr(w, "audio_file", "") or "")

    def track_for_index(self, i):
        w = self.w
        if hasattr(w, "core_track_for_index"):
            return w.core_track_for_index(i)
        if hasattr(w, "subtrack_listbox") and not hasattr(w, "playlist_listbox"):
            return f"{getattr(w, 'audio_file', '')}#{i + 1}"
        playlist = getattr(w, "playlist", None) or []
        if 0 <= i < len(playlist):
            entry = playlist[i]
            if isinstance(entry, dict):
                entry = entry.get("file")
            if getattr(w, "total_chapters", 1) and getattr(w, "total_chapters", 1) > 1:
                return f"{entry}#{i + 1}"
            return str(entry)
        return None

    def _status(self):
        w = self.w
        text = ""
        try:
            text = str(w.status_var.get()).lower()
        except Exception:
            pass
        if getattr(w, "is_paused", False) or "pause" in text:
            return "Paused"
        if "play" in text or "seek" in text or "buffer" in text:
            return "Playing"
        return "Stopped"

    def _position(self):
        for name in ("current_position_seconds", "raw_position_seconds"):
            v = getattr(self.w, name, None)
            if isinstance(v, (int, float)):
                return float(v)
        return 0.0

    def _duration(self):
        for name in ("total_duration_seconds", "raw_duration_seconds"):
            v = getattr(self.w, name, None)
            if isinstance(v, (int, float)) and v > 0:
                return float(v)
        return 0.0

    def _describe(self, track):
        """title / artist / album (game) for a track, cached."""
        if track in self._tag_cache:
            return self._tag_cache[track]
        plain = _plain_path(track)
        ext = os.path.splitext(plain)[1].lower()
        game = library.parse_game_folder(library.game_folder_name(plain))
        tags = read_psf_tags(plain) if ext in PSF_FAMILY else {}
        title = tags.get("title") or Path(plain).stem
        if "#" in str(track) and str(track) != plain:
            title = f"{title} (track {str(track).rsplit('#', 1)[1]})"
        info = {"title": title, "artist": tags.get("artist", ""),
                "album": tags.get("game") or game["title"], "year": tags.get("year") or game["year"],
                "system": library.system_key_for(None, game["platform"]) or ART_SYSTEM_BY_EXT.get(ext)}
        self._tag_cache[track] = info
        return info

    def _lookup_art(self, track, info):
        key = (info["album"], info["system"])
        if key in self._art_cache:
            return self._art_cache[key]
        self._art_cache[key] = None

        def work():
            art = library.find_box_art(info["album"], info["system"])
            self._art_cache[key] = art
            if art and self._track == track:
                self._snapshot["art"] = art
                self.registration.publish(art=art)
                if self.media:
                    self.media.notify()
        if info["system"]:
            threading.Thread(target=work, daemon=True).start()
        return None

    # --------------------------------------------------------------- tick
    def _tick(self, reschedule=True):
        if self._closed:
            return
        try:
            if not self.w.winfo_exists():
                return
        except tk.TclError:
            return
        self._drain_commands()
        track = self.current_track()
        status = self._status()
        position, duration = self._position(), self._duration()

        if track and track != self._track and status == "Playing":
            self._on_track_started(track)
        elif self._track and status == "Playing" and position < 1.0 and \
                self._snapshot.get("position", 0) > 5.0 and track == self._track:
            self._on_track_started(track)  # same track restarted (loop / replay)

        info = self._describe(track) if track else {}
        self._snapshot = {
            "status": status, "file": track, "position": position, "duration": duration,
            "title": info.get("title", ""), "artist": info.get("artist", ""),
            "album": info.get("album", ""), "year": info.get("year", ""),
            "art": self._art_cache.get((info.get("album"), info.get("system"))),
            "kind": self.kind, "can_seek": hasattr(self.w, "seek_to_position"),
            "volume": self._volume_fraction(),
        }
        now = time.time()
        if self._play_id is not None and status == "Playing" and now - self._last_progress_save > 5:
            self._last_progress_save = now
            library.history().progress(self._play_id, position)
        if now - self._last_publish > 1.0 or not reschedule:
            self._last_publish = now
            self.registration.publish(**self._snapshot)
            if self.media:
                self.media.notify()
        self._enforce_override(status, position)
        if reschedule:
            self.w.after(self.TICK_MS, self._tick)

    def _volume_fraction(self):
        try:
            return float(self.w.volume_var.get()) / 100.0
        except Exception:
            return 1.0

    def _on_track_started(self, track):
        self._track = track
        self._override = library.get_override(track)
        self._override_state = None
        self._last_progress_save = time.time()
        info = self._describe(track)
        self._play_id = library.history().start(track, title=info["title"], player=self.kind)
        self._lookup_art(track, info)
        self.registration.state["last_active"] = time.time()

    # --------------------------------------------------------------- overrides
    def _enforce_override(self, status, position):
        """Safety net for players that don't apply overrides themselves:
        end the track at length (+fade) even if the backend would go on."""
        o = self._override
        if not o or getattr(self.w, "core_handles_overrides", False) or status != "Playing":
            return
        length, fade = o
        if self._override_state is None and position >= length:
            self._override_state = "fading"
            proc = self._backend_process()
            if fade > 0 and proc is not None and hasattr(proc, "fade_out"):
                proc.fade_out(fade)
            else:
                fade = 0
            self._override_deadline = position + fade
        if self._override_state == "fading" and position >= self._override_deadline:
            self._override_state = "done"
            self.w.next_track()

    def _backend_process(self):
        for name in PROC_ATTRS:
            p = getattr(self.w, name, None)
            if p is not None and hasattr(p, "poll"):
                return p
        return None

    # --------------------------------------------------------------- playlist menu
    def _attach_playlist_menu(self):
        self._listbox = None
        for name in LISTBOX_ATTRS:
            lb = getattr(self.w, name, None)
            if lb is not None:
                self._listbox = lb
                break
        if self._listbox is None:
            # some players build their UI after __init__: try again shortly
            self.w.after(1500, self._attach_playlist_menu_late)
            return
        self._listbox.bind("<Button-3>", self._on_playlist_right_click, add="+")
        if not IS_WINDOWS:
            self._listbox.bind("<Button-2>", self._on_playlist_right_click, add="+")

    def _attach_playlist_menu_late(self):
        if self._listbox is None:
            for name in LISTBOX_ATTRS:
                lb = getattr(self.w, name, None)
                if lb is not None:
                    self._listbox = lb
                    lb.bind("<Button-3>", self._on_playlist_right_click, add="+")
                    break

    def _on_playlist_right_click(self, event):
        lb = self._listbox
        try:
            i = lb.nearest(event.y)
        except Exception:
            return
        if i < 0:
            return
        track = self.track_for_index(i)
        if not track:
            return
        menu = tk.Menu(self.w, tearoff=0)
        from exporter import can_export
        if can_export(track):
            menu.add_command(label="Export to WAV...", command=lambda: self.export_dialog(track, "wav"))
            menu.add_command(label="Export to FLAC...", command=lambda: self.export_dialog(track, "flac"))
            menu.add_separator()
        menu.add_command(label="Set length / fade...", command=lambda: self.override_dialog(track))
        if library.get_override(track):
            menu.add_command(label="Clear length / fade override", command=lambda: self._clear_override(track))
        menu.add_separator()
        menu.add_command(label="Open containing folder",
                         command=lambda: open_path(os.path.dirname(_plain_path(track))))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    # --------------------------------------------------------------- dialogs
    def override_dialog(self, track):
        current = library.get_override(track)
        if current:
            length, fade = current
        else:
            length = self._duration() if track == self._track else 0.0
            fade = 0.0
            plain = _plain_path(track)
            tags = read_psf_tags(plain) if os.path.splitext(plain)[1].lower() in PSF_FAMILY else {}
            if tags.get("length"):
                length = library.parse_time_text(tags["length"]) or length
                fade = library.parse_time_text(tags.get("fade", "0")) or 0.0
        dlg = tb.Toplevel(self.w)
        dlg.title("Set length / fade")
        dlg.transient(self.w)
        dlg.resizable(False, False)
        frm = tb.Frame(dlg, padding=14)
        frm.pack(fill="both", expand=True)
        tb.Label(frm, text=os.path.basename(_plain_path(track)) + ("" if "#" not in track else f"  #{track.rsplit('#', 1)[1]}"),
                 font=("Arial", 9, "bold")).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))
        tb.Label(frm, text="Length (m:ss):").grid(row=1, column=0, sticky="w")
        len_var = tk.StringVar(value=library.format_time_text(length) if length else "")
        e1 = tb.Entry(frm, textvariable=len_var, width=12)
        e1.grid(row=1, column=1, sticky="w", padx=6, pady=2)
        tb.Label(frm, text="Fade (seconds):").grid(row=2, column=0, sticky="w")
        fade_var = tk.StringVar(value=f"{fade:g}")
        tb.Entry(frm, textvariable=fade_var, width=12).grid(row=2, column=1, sticky="w", padx=6, pady=2)
        tb.Label(frm, text="Plays this long, then fades out. Saved for this track only.",
                 font=("Arial", 8, "italic")).grid(row=3, column=0, columnspan=2, sticky="w", pady=(6, 8))
        btns = tb.Frame(frm)
        btns.grid(row=4, column=0, columnspan=2, sticky="e")

        def save(event=None):
            ln = library.parse_time_text(len_var.get())
            fd = library.parse_time_text(fade_var.get()) or 0.0
            if not ln or ln <= 0:
                messagebox.showerror("Invalid length", "Enter a length like 2:30 or 150.", parent=dlg)
                return
            library.set_override(track, ln, fd)
            dlg.destroy()
            self._override_changed(track)

        tb.Button(btns, text="Cancel", bootstyle="secondary", command=dlg.destroy).pack(side="right", padx=(6, 0))
        tb.Button(btns, text="Save", command=save).pack(side="right")
        dlg.bind("<Return>", save)
        dlg.bind("<Escape>", lambda e: dlg.destroy())
        e1.focus_set()

    def _clear_override(self, track):
        library.clear_override(track)
        self._override_changed(track)

    def _override_changed(self, track):
        if track != self._track:
            return
        self._override = library.get_override(track)
        self._override_state = None
        w = self.w
        for name in ("core_restart_current", "play_current_track", "load_and_play_current"):
            if hasattr(w, name):
                if messagebox.askyesno("Length changed", "Restart this track with the new length?", parent=w):
                    getattr(w, name)()
                return

    def export_dialog(self, track, fmt):
        info = self._describe(track)
        base = f"{info['album']} - {info['title']}" if info.get("album") else info["title"]
        base = "".join(c if c not in '<>:"/\\|?*' else "_" for c in base).strip() or "export"
        initial_dir = getattr(self.w, "_last_export_dir", None) or os.path.expanduser("~")
        out = filedialog.asksaveasfilename(
            parent=self.w, title=f"Export to {fmt.upper()}", initialdir=initial_dir,
            initialfile=f"{base}.{fmt}", defaultextension=f".{fmt}",
            filetypes=[(f"{fmt.upper()} audio", f"*.{fmt}"), ("All files", "*.*")])
        if not out:
            return
        self.w._last_export_dir = os.path.dirname(out)
        ExportProgressWindow(self.w, track, out, fmt,
                             duration_hint=self._duration() if track == self._track else None)

    # --------------------------------------------------------------- shutdown
    def _on_destroy(self, event):
        if event.widget is self.w:
            self.close()

    def close(self):
        if self._closed:
            return
        self._closed = True
        try:
            if self._play_id is not None:
                library.history().progress(self._play_id, self._position())
        except Exception:
            pass
        if self.media:
            self.media.close()
        self.registration.close()


class ExportProgressWindow(tb.Toplevel):
    def __init__(self, parent, track, out_path, fmt, duration_hint=None):
        super().__init__(parent)
        self.title(f"Exporting {fmt.upper()}")
        self.resizable(False, False)
        self.transient(parent)
        self.stop_event = threading.Event()
        self.out_path = out_path
        frm = tb.Frame(self, padding=14)
        frm.pack(fill="both", expand=True)
        tb.Label(frm, text=os.path.basename(out_path), font=("Arial", 9, "bold")).pack(anchor="w")
        self.text = tk.StringVar(value="Starting...")
        tb.Label(frm, textvariable=self.text).pack(anchor="w", pady=(4, 6))
        self.bar = tb.Progressbar(frm, length=320, mode="indeterminate")
        self.bar.pack(fill="x")
        self.bar.start(12)
        btns = tb.Frame(frm)
        btns.pack(fill="x", pady=(10, 0))
        self.cancel_btn = tb.Button(btns, text="Cancel", bootstyle="secondary", command=self.cancel)
        self.cancel_btn.pack(side="right")
        self.open_btn = tb.Button(btns, text="Open folder", command=lambda: open_path(os.path.dirname(out_path)))
        self._q = queue.Queue()
        self.protocol("WM_DELETE_WINDOW", self.cancel)
        threading.Thread(target=self._work, args=(track, out_path, fmt, duration_hint), daemon=True).start()
        self.after(100, self._poll)

    def _work(self, track, out_path, fmt, duration_hint):
        from exporter import ExportCancelled, export_track
        try:
            export_track(track, out_path, fmt, progress=lambda f, t: self._q.put(("progress", f, t)),
                         stop_event=self.stop_event, duration_hint=duration_hint)
            self._q.put(("done", None, None))
        except ExportCancelled:
            self._q.put(("cancelled", None, None))
        except Exception as e:
            self._q.put(("error", None, str(e)))

    def _poll(self):
        try:
            while True:
                kind, frac, text = self._q.get_nowait()
                if kind == "progress":
                    if frac is not None:
                        if str(self.bar.cget("mode")) != "determinate":
                            self.bar.stop()
                            self.bar.configure(mode="determinate", maximum=100)
                        self.bar["value"] = frac * 100
                        self.text.set(f"{text} {int(frac * 100)}%")
                    else:
                        self.text.set(text)
                elif kind == "done":
                    self.bar.stop()
                    self.bar.configure(mode="determinate", maximum=100)
                    self.bar["value"] = 100
                    self.text.set("Export finished.")
                    self.cancel_btn.configure(text="Close", command=self.destroy)
                    self.open_btn.pack(side="right", padx=(0, 6))
                    return
                elif kind == "cancelled":
                    self.destroy()
                    return
                elif kind == "error":
                    self.bar.stop()
                    self.text.set("Export failed.")
                    messagebox.showerror("Export failed", text, parent=self)
                    self.cancel_btn.configure(text="Close", command=self.destroy)
                    return
        except queue.Empty:
            pass
        if self.winfo_exists():
            self.after(150, self._poll)

    def cancel(self):
        self.stop_event.set()
        self.text.set("Cancelling...")


def attach_player_core(window, kind=None):
    """Attach the shared core to a player window (idempotent, never fatal)."""
    if getattr(window, "core", None) is not None:
        return window.core
    if kind is None:
        mod = type(window).__module__.rsplit(".", 1)[-1]
        if mod == "__main__":  # run as a script: use the file name instead
            import sys
            mod = os.path.splitext(os.path.basename(getattr(sys.modules["__main__"], "__file__", "") or ""))[0]
        kind = mod[:-len("_player")] if mod.endswith("_player") else mod
        kind = {"twosf": "2sf", "neobae": "midi"}.get(kind, kind)
    try:
        window.core = PlayerCore(window, kind)
    except Exception as e:
        print(f"[core] could not attach: {e}")
        window.core = None
    return window.core
