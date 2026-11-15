import array
import os
import queue
import threading
import time
import tkinter as tk
import ttkbootstrap as tb
from config import apply_global_font_scale, load_saved_font_size
from player_launcher import load_saved_theme, attach_settings_sync, launch_player, run_player
import random
from tkinter import ttk, messagebox, filedialog
from config import resource_path
from platform_support import apply_gain
from players import (
    get_saved_player_volume,
    register_volume_player,
    set_player_volume,
    InteractiveProgressBar,
    ExtractedFilesBrowserWindow,
    get_next_track_index
)

SAMPLE_RATE = 44100


def _entry_path(entry):
    return entry.get("file") if isinstance(entry, dict) else entry


class PSFAudioWorker:
    """Plays PSF/PSF2 tracks through one continuous audio stream.

    * gapless: when a track ends, the track queued with queue_next() starts
      straight away on the same stream (the UI gets an "advanced" message)
    * seeking: AOSDK can't jump, but playback is deterministic, so a seek
      restarts the track if needed and renders silently up to the target
      (~20x faster than real time)
    * per-track length/fade overrides (library.get_override)
    """

    CHUNK = 4096

    def __init__(self, audio_file, ui_queue, volume=1.0, start_at=0.0):
        self.audio_file = audio_file
        self.ui_queue = ui_queue
        self.volume = max(0.0, float(volume))

        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._thread = None
        self._lock = threading.Lock()
        self._next_file = None
        self._seek_target = start_at if start_at and start_at > 0 else None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        self._pause_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def pause(self):
        self._pause_event.clear()

    def resume(self):
        self._pause_event.set()

    def set_volume(self, vol):
        with self._lock:
            self.volume = max(0.0, float(vol))

    def queue_next(self, path):
        """Track to continue with (gaplessly) when the current one ends."""
        with self._lock:
            self._next_file = path

    def seek(self, seconds):
        with self._lock:
            self._seek_target = max(0.0, float(seconds))

    def reset_track(self):
        self.seek(0.0)

    # ------------------------------------------------------------------
    def _run(self):
        try:
            import sounddevice as sd
            from psf_ctypes import AOSDKContext, AOSDKError
        except Exception as e:
            self.ui_queue.put(("error", f"Failed to initialize audio subsystem: {e}"))
            self.ui_queue.put(("status", "Audio Error"))
            return

        try:
            stream = sd.RawOutputStream(samplerate=SAMPLE_RATE, channels=2, dtype="int16",
                                        blocksize=self.CHUNK, latency="high")
            stream.start()
        except Exception as e:
            self.ui_queue.put(("error", f"Could not open audio output device: {e}"))
            self.ui_queue.put(("status", "Device Error"))
            return

        path = self.audio_file
        try:
            with AOSDKContext() as ctx:
                while not self._stop_event.is_set():
                    try:
                        track = self._open_track(ctx, path)
                    except AOSDKError as e:
                        self.ui_queue.put(("error", str(e)))
                        self.ui_queue.put(("status", "Load Error"))
                        return
                    self.ui_queue.put(("status", "Playing"))
                    if self._play(ctx, stream, track) != "ended":
                        break
                    with self._lock:
                        nxt, self._next_file = self._next_file, None
                    if not nxt:
                        self.ui_queue.put(("ended", None))
                        break
                    path = nxt
                    self.ui_queue.put(("advanced", path))
                ctx.stop()
        except Exception as e:
            if not self._stop_event.is_set():
                self.ui_queue.put(("error", f"Playback error: {e}"))
                self.ui_queue.put(("status", "Playback Error"))
        finally:
            try:
                if self._stop_event.is_set():
                    stream.abort()
                else:
                    stream.stop()  # let the last buffer play out
                stream.close()
            except Exception:
                pass
            if not self._stop_event.is_set():
                self.ui_queue.put(("status", "Stopped"))

    def _open_track(self, ctx, path):
        from library import get_override
        from platform_support import windows_safe_path
        ctx.open(windows_safe_path(path))
        duration = ctx.get_duration()
        fade = ctx.get_fade()
        override = get_override(path)
        track = {"path": path, "override": override, "end": None, "fade_start": None, "fade_len": 1}
        if override:
            length, ofade = override
            track["end"] = int((length + ofade) * SAMPLE_RATE)
            track["fade_start"] = int(length * SAMPLE_RATE)
            track["fade_len"] = max(1, int(ofade * SAMPLE_RATE))
            display = length + ofade
        else:
            display = duration
        track["display"] = display
        self.ui_queue.put(("opened", {"file": path, "duration": display, "fade": fade,
                                      "tags": ctx.get_tags(), "format": ctx.format_name,
                                      "override": bool(override)}))
        return track

    def _post_position(self, frames, track):
        pos = frames / float(SAMPLE_RATE)
        dur = track["display"]
        pct = int(pos / dur * 100) if dur and dur > 0 else 0
        self.ui_queue.put(("position", (pos, dur, min(pct, 100))))

    def _play(self, ctx, stream, track):
        """Play the open track. Returns "ended" or "stopped"."""
        frames = 0
        last_post = 0.0
        while not self._stop_event.is_set():
            if not self._pause_event.is_set():
                time.sleep(0.05)
                continue

            with self._lock:
                target, self._seek_target = self._seek_target, None
            if target is not None:
                frames = self._seek(ctx, track, frames, int(target * SAMPLE_RATE))
                if frames is None:
                    return "stopped"
                continue

            n = self.CHUNK
            if track["end"] is not None:
                n = min(n, track["end"] - frames)
                if n <= 0:
                    return "ended"
            count, pcm = ctx.render(n)
            if count <= 0 or (track["end"] is None and ctx.is_done()):
                return "ended"

            with self._lock:
                vol = self.volume
            fs = track["fade_start"]
            if fs is not None and frames + count > fs:
                fl = track["fade_len"]
                pcm = apply_gain(pcm, [vol * max(0.0, min(1.0, 1 - (frames + i - fs) / fl)) for i in range(count)])
            elif vol != 1.0:
                pcm = apply_gain(pcm, vol)
            try:
                stream.write(pcm)
            except Exception:
                if self._stop_event.is_set():
                    return "stopped"
                raise
            frames += count

            now = time.time()
            if now - last_post >= 0.25:
                last_post = now
                self._post_position(frames, track)
        return "stopped"

    def _seek(self, ctx, track, frames, target):
        """Render silently from the current spot (or the start) to target."""
        if track["end"] is not None:
            target = min(target, track["end"])
        if target < frames:
            # Re-open rather than reset(): older libaosdk.so builds keep
            # counting toward the track length across a reset and stop early
            from platform_support import windows_safe_path
            ctx.open(windows_safe_path(track["path"]))
            frames = 0
        if target - frames > SAMPLE_RATE:
            self.ui_queue.put(("status", "Seeking..."))
        last_post = 0.0
        while frames < target:
            if self._stop_event.is_set():
                return None
            with self._lock:
                if self._seek_target is not None:  # a newer seek: let the loop take it
                    break
            count, _ = ctx.render(min(16384, target - frames))
            if count <= 0:
                break
            frames += count
            now = time.time()
            if now - last_post >= 0.25:
                last_post = now
                self._post_position(frames, track)
        self._post_position(frames, track)
        self.ui_queue.put(("status", "Playing" if self._pause_event.is_set() else "Paused"))
        return frames


class PSFPlayerWindow(tb.Window):
    def __init__(self, parent=None, audio_file=None, playlist=None, start_index=0, **kwargs):
        # Standalone root window (own process), themed from settings.json
        super().__init__(themename=load_saved_theme())
        apply_global_font_scale(load_saved_font_size())
        self.parent = parent
        self.audio_file = audio_file
        self.playlist = playlist or [{"file": audio_file, "title": os.path.basename(audio_file), "track_index": 0}]
        self.current_index = start_index if start_index is not None and 0 <= start_index < len(self.playlist) else 0
        
        self.worker = None
        self.is_paused = False
        self.current_position_seconds = 0.0
        self.total_duration_seconds = 180.0
        self.is_scrubbing = False
        self.ui_queue = queue.Queue()

        saved_volume = get_saved_player_volume()
        self.volume_var = tk.IntVar(value=saved_volume)
        self.volume_label_var = tk.StringVar(value=f"Volume: {saved_volume}%")
        register_volume_player(self)

        self.position_var = tk.StringVar(value="Position: 00:00:00")
        self.duration_var = tk.StringVar(value="Duration: 00:00:00")
        self.status_var = tk.StringVar(value="Stopped")
        self.track_var = tk.StringVar()
        self.seek_pct_var = tk.StringVar(value="0%")
        self.tags_var = tk.StringVar(value="")

        self.load_playlist_files()
        self.build_ui()

        try:
            icon_file = resource_path("assets/icons/player_icon.png")
            if icon_file.exists():
                self.iconphoto(False, tk.PhotoImage(file=str(icon_file)))
        except Exception:
            pass

        self.protocol("WM_DELETE_WINDOW", self.close)
        attach_settings_sync(self, on_font_changed=apply_global_font_scale)
        self.start_queue_polling()

        if self.playlist:
            self.play_current_track()

    def play_random_track(self):
        if not self.playlist:
            return
        if len(self.playlist) == 1:
            self.play_current_track()
            return
        new_index = self.current_index
        while new_index == self.current_index:
            new_index = random.randint(0, len(self.playlist) - 1)
        self.current_index = new_index
        self.play_current_track()
    
    def format_time(self, seconds):
        if seconds is None or seconds < 0:
            return "00:00:00"
        total_seconds = int(seconds)
        return f"{total_seconds // 3600:02d}:{(total_seconds % 3600) // 60:02d}:{total_seconds % 60:02d}"

    def load_playlist_files(self):
        folder = os.path.dirname(self.audio_file)
        abs_target = os.path.normpath(os.path.abspath(self.audio_file))
        self.playlist = []
        try:
            for f in sorted(os.listdir(folder)):
                if f.lower().endswith((".psf", ".minipsf", ".psf2", ".minipsf2")):
                    self.playlist.append(os.path.join(folder, f))
            norm_playlist = [os.path.normpath(os.path.abspath(p)) for p in self.playlist]
            if abs_target in norm_playlist:
                self.current_index = norm_playlist.index(abs_target)
            else:
                self.playlist = [self.audio_file]
                self.current_index = 0
        except Exception:
            self.playlist = [self.audio_file]
            self.current_index = 0

    def build_ui(self):
        filename = os.path.basename(self.audio_file)
        self.title(f"PSF Player - {filename}")
        self.geometry("520x600")
        self.minsize(500, 500)

        padding = tb.Frame(self, padding=12)
        padding.pack(fill="both", expand=True)

        browse_frame = tb.Frame(padding)
        browse_frame.pack(side="top", anchor="w", pady=(0, 6))

        browse_btn = tb.Button(browse_frame, text="📁", width=3, command=self.on_browse_clicked)
        browse_btn.pack(side="top", anchor="w")

        self.title_label = tb.Label(padding, text=f"Now Playing:\n{filename}", font=("Arial", 9, "bold"), wraplength=480, justify="center")
        self.title_label.pack(pady=(0, 4))

        self.meta_label = tb.Label(padding, textvariable=self.tags_var, font=("Arial", 8), wraplength=480, justify="center", foreground="#555555")
        self.meta_label.pack(pady=(0, 6))

        info_frame = tb.Frame(padding)
        info_frame.pack(fill="x", pady=(0, 4))

        status_sub_frame = tb.Frame(info_frame)
        status_sub_frame.pack(side="left", padx=5)
        tb.Label(status_sub_frame, textvariable=self.status_var, font=("Arial", 9)).pack(side="left")
        tb.Label(status_sub_frame, text=" (", font=("Arial", 9)).pack(side="left")
        tb.Label(status_sub_frame, textvariable=self.seek_pct_var, font=("Arial", 9, "bold")).pack(side="left")
        tb.Label(status_sub_frame, text=")", font=("Arial", 9)).pack(side="left")

        tb.Label(info_frame, textvariable=self.position_var, font=("Arial", 9)).pack(side="right", padx=5)

        progress_container = tb.Frame(padding)
        progress_container.pack(fill="x", pady=(2, 6))
        self.progress_bar_widget = InteractiveProgressBar(
            progress_container,
            get_duration_func=lambda: self.total_duration_seconds,
            seek_func=self.seek_to_position,
            seek_start_func=lambda: setattr(self, "is_scrubbing", True),
            seek_release_func=lambda: setattr(self, "is_scrubbing", False)
        )
        self.progress_bar_widget.frame.pack(fill="x", expand=True)

        meta_frame = tb.Frame(padding)
        meta_frame.pack(fill="x", pady=(0, 8))
        tb.Label(meta_frame, textvariable=self.track_var, font=("Arial", 9, "bold")).pack(side="left", padx=5)
        tb.Label(meta_frame, textvariable=self.duration_var, font=("Arial", 9)).pack(side="right", padx=5)

        vol_frame = tb.Frame(padding)
        vol_frame.pack(fill="x", pady=(0, 8))
        tb.Label(vol_frame, textvariable=self.volume_label_var, font=("Arial", 9)).pack(side="left", padx=5)
        self.volume_slider = tb.Scale(vol_frame, from_=0, to=100, orient="horizontal", command=self.on_volume_slide)
        self.volume_slider.set(self.volume_var.get())
        self.volume_slider.pack(side="right", fill="x", expand=True, padx=5)

        list_frame = tb.LabelFrame(padding, text="Playlist Files", padding=6)
        list_frame.pack(fill="both", expand=True, pady=(0, 10))

        self.playlist_listbox = tb.Listbox(list_frame, font=("Arial", 9), selectmode="single", exportselection=False)
        self.playlist_listbox.pack(side="left", fill="both", expand=True)
        scrollbar = tb.Scrollbar(list_frame, orient="vertical", command=self.playlist_listbox.yview)
        scrollbar.pack(side="right", fill="y")
        self.playlist_listbox.configure(yscrollcommand=scrollbar.set)
        self.playlist_listbox.bind("<<ListboxSelect>>", self.on_playlist_select)
        self.playlist_listbox.bind("<Motion>", self.on_listbox_motion)
        self.playlist_listbox.bind("<Leave>", self.on_listbox_leave)
        self.populate_playlist_listbox()

        controls = tb.Frame(padding)
        controls.pack(pady=4)

        tb.Button(controls, text="Stop", width=8, command=self.stop).grid(row=0, column=0, padx=2)
        tb.Button(controls, text="|<< Prev", width=8, command=self.prev_track).grid(row=0, column=1, padx=2)
        tb.Button(controls, text="Play/Pause", width=10, command=self.toggle_play_pause).grid(row=0, column=2, padx=2)
        tb.Button(controls, text="Next >>|", width=8, command=self.next_track).grid(row=0, column=3, padx=2)
        tb.Button(controls, text="Close", width=8, command=self.close).grid(row=0, column=4, padx=2)

        # Row 1: Random button centered under column 2 (Play/Pause)
        tb.Button(controls, text="Random", width=8, command=self.play_random_track).grid(row=1, column=2, pady=(6, 0), padx=2)

    def populate_playlist_listbox(self):
        self.playlist_listbox.delete(0, tk.END)
        for f in self.playlist:
            self.playlist_listbox.insert(tk.END, os.path.basename(f))
        self.update_listbox_selection()

    def get_dynamic_hover_color(self):
        try:
            if hasattr(self, "style"):
                theme_name = self.style.theme_use().lower()
                if "dark" in theme_name:
                    return "#2a3b4c"
        except Exception:
            pass
        return "#e8f0fe"

    def on_listbox_motion(self, event):
        idx = self.playlist_listbox.nearest(event.y)
        hover_color = self.get_dynamic_hover_color()
        for i in range(self.playlist_listbox.size()):
            if i == idx:
                self.playlist_listbox.itemconfig(i, background=hover_color)
            else:
                self.playlist_listbox.itemconfig(i, background="")

    def on_listbox_leave(self, event):
        for i in range(self.playlist_listbox.size()):
            self.playlist_listbox.itemconfig(i, background="")

    def update_listbox_selection(self):
        if 0 <= self.current_index < len(self.playlist):
            self.playlist_listbox.selection_clear(0, tk.END)
            self.playlist_listbox.selection_set(self.current_index)
            self.playlist_listbox.see(self.current_index)

    def on_playlist_select(self, event):
        sel = self.playlist_listbox.curselection()
        if sel and sel[0] != self.current_index:
            self.current_index = sel[0]
            self.play_current_track()

    # The worker applies length/fade overrides itself (see player_core)
    core_handles_overrides = True

    def seek_to_position(self, target_time):
        """AOSDK can't jump, so the worker restarts the track if needed and
        renders silently up to the target (shown as "Seeking...")."""
        target_time = max(0.0, float(target_time))
        if not self.worker:
            self.play_current_track(start_at=target_time)
            return
        self.worker.seek(target_time)
        self.current_position_seconds = target_time
        self.position_var.set(f"Position: {self.format_time(target_time)}")
        self.progress_bar_widget.update_progress(target_time, self.total_duration_seconds)

    def restart_track(self):
        if self.worker:
            self.worker.seek(0.0)
            self.current_position_seconds = 0.0
            self.position_var.set("Position: 00:00:00")
            self.seek_pct_var.set("0%")
            self.progress_bar_widget.update_progress(0.0, self.total_duration_seconds)
            self.status_var.set("Playing")

    def start_queue_polling(self):
        def poll():
            try:
                while True:
                    msg = self.ui_queue.get_nowait()
                    self.handle_queue_message(msg[0], msg[1])
            except queue.Empty:
                pass
            if self.winfo_exists():
                self.after(40, poll)
        self.after(40, poll)

    def handle_queue_message(self, msg_type, data):
        if not self.winfo_exists():
            return
        if msg_type == "opened":
            dur = data.get("duration")
            tags = data.get("tags", {})
            fmt = data.get("format", "PSF")
            if dur and dur > 0:
                self.total_duration_seconds = float(dur)
                self.duration_var.set(f"Duration: {self.format_time(dur)}")
            else:
                self.total_duration_seconds = 180.0
                self.duration_var.set("Duration: Unknown")
            tag_lines = [f"{k.capitalize()}: {v}" for k, v in tags.items() if k in ("game", "title", "artist", "year")]
            self.tags_var.set(f"[{fmt}] " + " | ".join(tag_lines))
            # Gapless: hand the worker the following track now
            next_index = get_next_track_index(self.current_index, len(self.playlist), False)
            if self.worker and next_index is not None and next_index != self.current_index:
                self.worker.queue_next(_entry_path(self.playlist[next_index]))
        elif msg_type == "advanced":
            # The worker moved on to the queued track without a gap
            paths = [_entry_path(e) for e in self.playlist]
            if data in paths:
                self.current_index = paths.index(data)
            self._show_current_track()
            self.current_position_seconds = 0.0
            self.position_var.set("Position: 00:00:00")
            self.seek_pct_var.set("0%")
        elif msg_type == "position":
            curr, tot, pct = data
            if not self.is_scrubbing:
                self.current_position_seconds = curr
                self.position_var.set(f"Position: {self.format_time(curr)}")
                self.seek_pct_var.set(f"{pct}%")
                self.progress_bar_widget.update_progress(curr, self.total_duration_seconds)
        elif msg_type == "status":
            self.status_var.set(str(data))
        elif msg_type == "ended":
            self.advance_after_end()
        elif msg_type == "error":
            messagebox.showerror("PSF Player", str(data), parent=self)

    def _show_current_track(self):
        self.audio_file = _entry_path(self.playlist[self.current_index])
        filename = os.path.basename(self.audio_file)
        self.title(f"PSF Player - {filename}")
        if hasattr(self, "title_label") and self.winfo_exists():
            self.title_label.configure(text=f"Now Playing:\n{filename}")
        self.track_var.set(f"Track: {self.current_index + 1} / {len(self.playlist)}")
        self.update_listbox_selection()

    def core_restart_current(self):
        self.play_current_track()

    def play_current_track(self, start_at=0.0):
        self.stop_current_playback()
        self.current_position_seconds = 0.0
        self._show_current_track()
        self.status_var.set("Starting...")
        self.position_var.set("Position: 00:00:00")
        self.seek_pct_var.set("0%")

        vol_gain = max(0, min(150, self.volume_var.get())) / 100.0

        self.worker = PSFAudioWorker(self.audio_file, self.ui_queue, vol_gain, start_at=start_at)
        self.worker.start()
        self.is_paused = False

    def stop_current_playback(self):
        if self.worker:
            w = self.worker
            self.worker = None
            w.stop()

    def toggle_play_pause(self):
        if not self.worker or self.status_var.get() == "Stopped":
            self.play_current_track()
            return
        if self.is_paused:
            self.worker.resume()
            self.is_paused = False
            self.status_var.set("Playing")
        else:
            self.worker.pause()
            self.is_paused = True
            self.status_var.set("Paused")

    def prev_track(self):
        if len(self.playlist) > 1:
            self.current_index = (self.current_index - 1) % len(self.playlist)
            self.play_current_track()

    def next_track(self):
        if len(self.playlist) > 1:
            self.current_index = (self.current_index + 1) % len(self.playlist)
            self.play_current_track()

    def advance_after_end(self):
        next_index = get_next_track_index(self.current_index, len(self.playlist), False)
        if next_index is None:
            self.stop_current_playback()
            self.status_var.set("Stopped")
            return
        self.current_index = next_index
        self.play_current_track()

    def on_volume_slide(self, val):
        set_player_volume(float(val), source_player=self)

    def apply_live_volume(self):
        vol = max(0, min(150, self.volume_var.get())) / 100.0
        if self.worker:
            self.worker.set_volume(vol)

    def on_browse_clicked(self):
        ExtractedFilesBrowserWindow(self, os.path.dirname(self.audio_file))

    def stop(self):
        self.stop_current_playback()
        self.status_var.set("Stopped")
        self.position_var.set("Position: 00:00:00")
        self.seek_pct_var.set("0%")
        if hasattr(self, "progress_bar_widget"):
            self.progress_bar_widget.update_progress(0, self.total_duration_seconds)

    def close(self):
        self.stop_current_playback()
        try:
            self.destroy()
        except Exception:
            pass


if __name__ == "__main__":
    run_player(PSFPlayerWindow)
