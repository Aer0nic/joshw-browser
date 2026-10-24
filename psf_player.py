import array
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from config import is_emulated_file
from players import (
    get_saved_player_volume,
    register_volume_player,
    set_player_volume,
    InteractiveProgressBar,
    ExtractedFilesBrowserWindow,
    get_next_track_index
)

class PSFAudioWorker:
    def __init__(self, audio_file, ui_queue, volume=1.0, loop_forever=False, loop_count=1):
        self.audio_file = audio_file
        self.ui_queue = ui_queue
        self.volume = max(0.0, float(volume))
        self.loop_forever = loop_forever
        self.loop_count = loop_count
        self.repeat_count = 0

        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._seek_reset_event = threading.Event()
        self._thread = None
        self._lock = threading.Lock()

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

    def reset_track(self):
        self._seek_reset_event.set()

    def update_loop_settings(self, loop_forever, loop_count):
        with self._lock:
            self.loop_forever = loop_forever
            self.loop_count = loop_count

    def _run(self):
        try:
            import sounddevice as sd
            from psf_ctypes import AOSDKContext, AOSDKError
        except Exception as e:
            self.ui_queue.put(("error", f"Failed to initialize audio subsystem: {e}"))
            self.ui_queue.put(("status", "Audio Error"))
            return

        try:
            with AOSDKContext() as ctx:
                try:
                    ctx.open(self.audio_file)
                except AOSDKError as e:
                    self.ui_queue.put(("error", str(e)))
                    self.ui_queue.put(("status", "Load Error"))
                    return

                duration = ctx.get_duration()
                fade = ctx.get_fade()
                tags = ctx.get_tags()
                fmt = ctx.format_name

                self.ui_queue.put(("opened", {"duration": duration, "fade": fade, "tags": tags, "format": fmt}))
                self.ui_queue.put(("status", "Playing"))

                chunk_size = 2048
                sample_rate = ctx.sample_rate

                try:
                    stream = sd.RawOutputStream(
                        samplerate=sample_rate, channels=2, dtype="int16", blocksize=chunk_size
                    )
                    stream.start()
                except Exception as e:
                    self.ui_queue.put(("error", f"Could not open audio output device: {e}"))
                    self.ui_queue.put(("status", "Device Error"))
                    return

                try:
                    last_pos_time = 0.0
                    while not self._stop_event.is_set():
                        if not self._pause_event.is_set():
                            time.sleep(0.05)
                            continue

                        if self._seek_reset_event.is_set():
                            self._seek_reset_event.clear()
                            try:
                                ctx.reset()
                                self.ui_queue.put(("position", (0.0, duration, 0)))
                            except Exception:
                                pass

                        count, pcm = ctx.render(chunk_size)
                        if count <= 0 or ctx.is_done():
                            with self._lock:
                                loop_forever, loop_count = self.loop_forever, self.loop_count

                            if loop_forever:
                                ctx.reset()
                                continue
                            elif self.repeat_count < loop_count - 1:
                                self.repeat_count += 1
                                ctx.reset()
                                continue
                            else:
                                self.ui_queue.put(("ended", None))
                                break

                        with self._lock:
                            vol = self.volume

                        if vol != 1.0 and len(pcm) > 0:
                            arr = array.array("h")
                            arr.frombytes(pcm)
                            for i in range(len(arr)):
                                arr[i] = max(-32768, min(32767, int(arr[i] * vol)))
                            pcm = arr.tobytes()

                        try:
                            stream.write(pcm)
                        except Exception:
                            if self._stop_event.is_set():
                                break
                            raise

                        now = time.time()
                        if now - last_pos_time >= 0.2:
                            last_pos_time = now
                            curr_pos = ctx.get_position()
                            pct = int((curr_pos / duration) * 100) if duration and duration > 0 else 0
                            self.ui_queue.put(("position", (curr_pos, duration, pct)))
                finally:
                    try:
                        stream.stop()
                        stream.close()
                    except Exception:
                        pass
                    ctx.stop()
        except Exception:
            if not self._stop_event.is_set():
                self.ui_queue.put(("status", "Playback Error"))
        finally:
            if not self._stop_event.is_set():
                self.ui_queue.put(("status", "Stopped"))


class PSFPlayerWindow(tk.Toplevel):
    def __init__(self, parent, audio_file, playlist=None, start_index=0, **kwargs):
        super().__init__(parent)
        self.parent = parent
        self.audio_file = audio_file
        self.playlist = playlist or [{"file": audio_file, "title": os.path.basename(audio_file), "track_index": 0}]
        self.current_index = start_index if 0 <= start_index < len(self.playlist) else 0
        
        self.worker = None
        self.is_paused = False
        self.current_position_seconds = 0.0
        self.total_duration_seconds = 180.0
        self.is_scrubbing = False
        self.ui_queue = queue.Queue()

        is_emu = is_emulated_file(self.audio_file)
        self.loop_chk_var = tk.BooleanVar(value=is_emu)
        self.loop_count_var = tk.IntVar(value=1)
        self.loop_forever_var = tk.BooleanVar(value=False)

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
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.start_queue_polling()

        if self.playlist:
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
        self.geometry("520x620")
        self.minsize(460, 520)
        self.transient(self.parent)

        padding = ttk.Frame(self, padding=12)
        padding.pack(fill="both", expand=True)

        browse_frame = ttk.Frame(padding)
        browse_frame.pack(side="top", anchor="w", pady=(0, 6))

        browse_btn = tk.Button(browse_frame, text="📁", font=("Arial", 12), width=3, command=self.on_browse_clicked)
        browse_btn.pack(side="top", anchor="w")

        self.title_label = ttk.Label(padding, text=f"Now Playing:\n{filename}", font=("Arial", 9, "bold"), wraplength=480, justify="center")
        self.title_label.pack(pady=(0, 4))

        self.meta_label = ttk.Label(padding, textvariable=self.tags_var, font=("Arial", 8), wraplength=480, justify="center", foreground="#555555")
        self.meta_label.pack(pady=(0, 6))

        info_frame = ttk.Frame(padding)
        info_frame.pack(fill="x", pady=(0, 4))

        status_sub_frame = ttk.Frame(info_frame)
        status_sub_frame.pack(side="left", padx=5)
        ttk.Label(status_sub_frame, textvariable=self.status_var, font=("Arial", 9)).pack(side="left")
        ttk.Label(status_sub_frame, text=" (", font=("Arial", 9)).pack(side="left")
        ttk.Label(status_sub_frame, textvariable=self.seek_pct_var, font=("Arial", 9, "bold")).pack(side="left")
        ttk.Label(status_sub_frame, text=")", font=("Arial", 9)).pack(side="left")

        ttk.Label(info_frame, textvariable=self.position_var, font=("Arial", 9)).pack(side="right", padx=5)

        progress_container = ttk.Frame(padding)
        progress_container.pack(fill="x", pady=(2, 6))
        self.progress_bar_widget = InteractiveProgressBar(
            progress_container,
            get_duration_func=lambda: self.total_duration_seconds,
            seek_func=self.seek_to_position,
            seek_start_func=lambda: setattr(self, "is_scrubbing", True),
            seek_release_func=lambda: setattr(self, "is_scrubbing", False)
        )
        self.progress_bar_widget.frame.pack(fill="x", expand=True)

        meta_frame = ttk.Frame(padding)
        meta_frame.pack(fill="x", pady=(0, 8))
        ttk.Label(meta_frame, textvariable=self.track_var, font=("Arial", 9, "bold")).pack(side="left", padx=5)
        ttk.Label(meta_frame, textvariable=self.duration_var, font=("Arial", 9)).pack(side="right", padx=5)

        vol_frame = ttk.Frame(padding)
        vol_frame.pack(fill="x", pady=(0, 8))
        ttk.Label(vol_frame, textvariable=self.volume_label_var, font=("Arial", 9)).pack(side="left", padx=5)
        self.volume_slider = ttk.Scale(vol_frame, from_=0, to=150, orient="horizontal", command=self.on_volume_slide)
        self.volume_slider.set(self.volume_var.get())
        self.volume_slider.pack(side="right", fill="x", expand=True, padx=5)

        list_frame = ttk.LabelFrame(padding, text="Playlist Files", padding=6)
        list_frame.pack(fill="both", expand=True, pady=(0, 10))

        self.playlist_listbox = tk.Listbox(list_frame, font=("Arial", 9), selectmode="single", exportselection=False)
        self.playlist_listbox.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.playlist_listbox.yview)
        scrollbar.pack(side="right", fill="y")
        self.playlist_listbox.configure(yscrollcommand=scrollbar.set)
        self.playlist_listbox.bind("<<ListboxSelect>>", self.on_playlist_select)
        self.populate_playlist_listbox()

        controls = ttk.Frame(padding)
        controls.pack(pady=4)

        ttk.Button(controls, text="|<< Prev", width=8, command=self.prev_track).grid(row=0, column=0, padx=2)
        ttk.Button(controls, text="Restart", width=8, command=self.restart_track).grid(row=0, column=1, padx=2)
        ttk.Button(controls, text="Play/Pause", width=10, command=self.toggle_play_pause).grid(row=0, column=2, padx=2)
        ttk.Button(controls, text="Seek Info", width=8, command=self.show_seek_info).grid(row=0, column=3, padx=2)
        ttk.Button(controls, text="Next >>|", width=8, command=self.next_track).grid(row=0, column=4, padx=2)

        btn_bottom = ttk.Frame(padding)
        btn_bottom.pack(pady=(6, 0), fill="x")

        ttk.Checkbutton(btn_bottom, variable=self.loop_chk_var, command=self.on_loop_settings_changed).pack(side="left", padx=(0, 2))
        ttk.Spinbox(btn_bottom, from_=1, to=99, width=3, textvariable=self.loop_count_var, command=self.on_loop_settings_changed).pack(side="left", padx=(0, 6))
        ttk.Button(btn_bottom, text="Stop", width=8, command=self.stop).pack(side="left", padx=2)
        ttk.Checkbutton(btn_bottom, text="Loop forever", variable=self.loop_forever_var, command=self.on_loop_settings_changed).pack(side="right", padx=(4, 0))
        ttk.Button(btn_bottom, text="Close", width=8, command=self.close).pack(side="right", padx=2)

    def populate_playlist_listbox(self):
        self.playlist_listbox.delete(0, tk.END)
        for f in self.playlist:
            self.playlist_listbox.insert(tk.END, os.path.basename(f))
        self.update_listbox_selection()

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

    def seek_to_position(self, target_time):
        if target_time <= 1.0:
            self.restart_track()
        else:
            messagebox.showinfo("Seeking Unsupported", "PSF/PSF2 emulation does not support arbitrary scrubbing.")
            self.progress_bar_widget.update_progress(self.current_position_seconds, self.total_duration_seconds)

    def show_seek_info(self):
        messagebox.showinfo("PSF Seeking Limitation", "Hardware register states cannot be arbitrarily rewound.")

    def restart_track(self):
        if self.worker:
            self.worker.reset_track()
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

    def play_current_track(self):
        self.stop_current_playback()
        self.current_position_seconds = 0.0
        self.audio_file = self.playlist[self.current_index]
        filename = os.path.basename(self.audio_file)

        # Updates window title dynamically
        self.title(f"PSF Player - {filename}")
        if hasattr(self, "title_label") and self.winfo_exists():
            self.title_label.configure(text=f"Now Playing:\n{filename}")
        self.track_var.set(f"Track: {self.current_index + 1} / {len(self.playlist)}")
        self.status_var.set("Starting...")
        self.position_var.set("Position: 00:00:00")
        self.seek_pct_var.set("0%")
        self.update_listbox_selection()

        vol_gain = max(0, min(150, self.volume_var.get())) / 100.0
        loop_forever = self.loop_forever_var.get()
        loop_count = max(1, int(self.loop_count_var.get())) if self.loop_chk_var.get() else 1

        self.worker = PSFAudioWorker(self.audio_file, self.ui_queue, vol_gain, loop_forever, loop_count)
        self.worker.start()
        self.is_paused = False

    def stop_current_playback(self):
        if self.worker:
            w = self.worker
            self.worker = None
            w.stop()

    def toggle_play_pause(self):
        if not self.worker:
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

    def on_loop_settings_changed(self):
        if self.worker:
            self.worker.update_loop_settings(
                self.loop_forever_var.get(),
                max(1, int(self.loop_count_var.get())) if self.loop_chk_var.get() else 1
            )

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

    def close(self):
        self.stop_current_playback()
        try:
            self.destroy()
        except Exception:
            pass