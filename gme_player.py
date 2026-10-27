import ctypes
import os
import platform
import queue
import random
import threading
import time
import array
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path
from config import resource_path
from players import get_saved_player_volume, register_volume_player, set_player_volume, InteractiveProgressBar, ExtractedFilesBrowserWindow

def generate_gme_m3u_if_needed(audio_file):
    """
    Safely queries libgme using gme_track_count. If multiple tracks exist, 
    generates a <filename>_tracks.m3u file in the same folder and returns its path.
    If it's a single track or fails, returns the original audio_file path.
    """
    ext = os.path.splitext(audio_file)[1].lower()
    subtrack_exts = (".nsf", ".nsfe", ".gbs", ".hes", ".kss")
    
    if ext not in subtrack_exts or audio_file.lower().endswith(".m3u"):
        return audio_file

    folder = os.path.dirname(audio_file)
    stem = Path(audio_file).stem
    m3u_path = os.path.join(folder, f"{stem}_tracks.m3u")

    if os.path.exists(m3u_path):
        return m3u_path

    try:
        emu = ctypes.c_void_p(None)
        file_bytes = str(Path(audio_file).resolve()).encode("utf-8")
        
        err = get_gme().gme_open_file(file_bytes, ctypes.byref(emu), 44100)
        if err:
            return audio_file

        count = get_gme().gme_track_count(emu)
        get_gme().gme_delete(emu)

        if count <= 1:
            return audio_file

        with open(m3u_path, "w", encoding="utf-8") as f_m3u:
            f_m3u.write("#EXTM3U\n")
            for i in range(count):
                track_num = i + 1
                f_m3u.write(f"#EXTINF:-1,Track {track_num}\n")
                f_m3u.write(f"{os.path.basename(audio_file)}#{i}\n")
        
        return m3u_path
    except Exception:
        return audio_file

# ==========================================
# 1. libgme CTypes Wrapper & GMEContext
# ==========================================

_gme_lib = None

def get_gme():
    global _gme_lib
    if _gme_lib is not None:
        return _gme_lib

    system = platform.system()
    if system == "Windows":
        names = ["gme.dll", "libgme.dll"]
    else:
        names = ["libgme.so.0", "libgme.so"]

    lib = None
    for name in names:
        try:
            lib = ctypes.CDLL(name)
            break
        except Exception:
            try:
                bundled_lib = str(resource_path(name))
                lib = ctypes.CDLL(bundled_lib)
                break
            except Exception:
                try:
                    lib = ctypes.CDLL(os.path.join(os.path.dirname(__file__), name))
                    break
                except Exception:
                    continue

    if lib is None:
        raise RuntimeError("Could not load Game Music Emu (libgme) shared library.")

    lib.gme_open_file.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.c_int]
    lib.gme_open_file.restype = ctypes.c_char_p

    lib.gme_track_count.argtypes = [ctypes.c_void_p]
    lib.gme_track_count.restype = ctypes.c_int

    lib.gme_free_info.argtypes = [ctypes.c_void_p]

    lib.gme_start_track.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.gme_start_track.restype = ctypes.c_char_p

    lib.gme_play.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_short)]
    lib.gme_play.restype = ctypes.c_char_p

    lib.gme_seek.argtypes = [ctypes.c_void_p, ctypes.c_int]
    lib.gme_seek.restype = ctypes.c_char_p

    lib.gme_delete.argtypes = [ctypes.c_void_p]

    lib.gme_track_info.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_int]
    lib.gme_track_info.restype = ctypes.c_char_p

    if hasattr(lib, "gme_set_fade"):
        lib.gme_set_fade.argtypes = [ctypes.c_void_p, ctypes.c_int]

    _gme_lib = lib
    return _gme_lib

class GMEContext:
    def __init__(self, file_path, sample_rate=44100):
        self.file_path = file_path
        self.sample_rate = sample_rate
        self.emu = ctypes.c_void_p(None)
        self.base_length_ms = 180000
        self.fade_ms = 0
        self.duration_ms = 180000

        track_ptr = ctypes.c_void_p()
        err = get_gme().gme_open_file(file_path.encode("utf-8"), ctypes.byref(track_ptr), sample_rate)
        if err:
            raise RuntimeError(f"Failed to open GME file: {err.decode('utf-8', errors='ignore')}")
        self.emu = track_ptr

    def load_track(self, track_index, loop_forever=False):
        self.current_track = track_index
        lib = get_gme()
        err = lib.gme_start_track(self.emu, track_index)
        if err:
            raise RuntimeError(f"Failed to start track {track_index}: {err.decode('utf-8', errors='ignore')}")

        if hasattr(lib, "gme_set_fade"):
            if loop_forever:
                lib.gme_set_fade(self.emu, 0)
            else:
                lib.gme_set_fade(self.emu, -1)

        info_ptr = ctypes.c_void_p()
        lib.gme_track_info(self.emu, ctypes.byref(info_ptr), track_index)
        
        self.base_length_ms = 180000
        self.fade_ms = 0

        if info_ptr.value:
            try:
                val = ctypes.c_int.from_address(info_ptr.value).value
                if val > 0:
                    self.base_length_ms = val
            except Exception:
                pass

            try:
                fade_val = ctypes.c_int.from_address(info_ptr.value + 12).value
                if fade_val > 0:
                    self.fade_ms = fade_val
            except Exception:
                pass
                
            lib.gme_free_info(info_ptr)

        default_fade_ms = 5000
        if self.fade_ms <= 0 or self.fade_ms == self.base_length_ms:
            actual_fade_ms = default_fade_ms
        else:
            actual_fade_ms = self.fade_ms

        if loop_forever:
            self.duration_ms = self.base_length_ms
        else:
            self.duration_ms = self.base_length_ms + actual_fade_ms

    def get_track_count(self):
        return get_gme().gme_track_count(self.emu)

    def render(self, num_samples, buf):
        err = get_gme().gme_play(self.emu, num_samples * 2, buf)
        if err:
            return False
        return True

    def seek(self, position_ms):
        get_gme().gme_seek(self.emu, int(position_ms))

    def close(self):
        if self.emu:
            get_gme().gme_delete(self.emu)
            self.emu = ctypes.c_void_p(None)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


# ==========================================
# 2. Audio Worker Thread
# ==========================================

class GMEAudioWorker:
    def __init__(self, audio_file, track_index, ui_queue, volume=1.0, loop_forever=False, loop_count=1, loop_enabled=False):
        self.audio_file = audio_file
        self.track_index = track_index
        self.ui_queue = ui_queue
        self.volume = max(0.0, float(volume))
        self.loop_forever = loop_forever
        self.loop_count = int(loop_count)
        self.loop_enabled = loop_enabled

        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._seek_target_ms = None
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

    def seek(self, position_ms):
        with self._lock:
            self._seek_target_ms = position_ms

    def _run(self):
        try:
            import sounddevice as sd
        except Exception as e:
            self.ui_queue.put(("error", f"Sounddevice initialization failed: {e}"))
            return

        try:
            with GMEContext(self.audio_file) as ctx:
                total_tracks = ctx.get_track_count()
                ctx.load_track(self.track_index, loop_forever=self.loop_forever)
                duration_sec = ctx.duration_ms / 1000.0

                self.ui_queue.put(("opened", {"duration": duration_sec, "track_count": total_tracks}))
                self.ui_queue.put(("status", "Playing"))

                chunk_samples = 4096
                sample_rate = ctx.sample_rate

                try:
                    # Find the best available output device index for HyperX or fallback to default
                    target_device = None
                    devices = sd.query_devices()
                    
                    # First try to find a HyperX playback device with output channels
                    for idx, dev in enumerate(devices):
                        if 'HyperX' in dev['name'] and dev['max_output_channels'] > 0:
                            target_device = idx
                            break
                    
                    # If not found, use system default output
                    if target_device is None:
                        default_info = sd.query_devices(kind='output')
                        target_device = default_info['index']

                    stream = sd.RawOutputStream(
                        samplerate=sample_rate, 
                        channels=2, 
                        dtype="int16", 
                        blocksize=chunk_samples, 
                        latency="high",
                        device=target_device
                    )
                    stream.start()
                except Exception as e:
                    self.ui_queue.put(("error", f"Could not open audio stream: {e}"))
                    return

                base_length_ms = ctx.base_length_ms
                fade_ms = ctx.fade_ms
                default_fade_ms = 5000
                actual_fade_ms = default_fade_ms if (fade_ms <= 0 or fade_ms == base_length_ms) else fade_ms

                base_samples = int((base_length_ms / 1000.0) * sample_rate)
                fade_samples = int((actual_fade_ms / 1000.0) * sample_rate)
                fade_start_sample = base_samples
                max_samples = base_samples + fade_samples

                total_samples_rendered = 0
                loops_completed = 0

                try:
                    last_pos_time = 0.0
                    while not self._stop_event.is_set():
                        if not self._pause_event.is_set():
                            time.sleep(0.05)
                            continue

                        with self._lock:
                            if self._seek_target_ms is not None:
                                target_ms = self._seek_target_ms
                                self._seek_target_ms = None
                                ctx.seek(target_ms)
                                total_samples_rendered = int((target_ms / 1000.0) * sample_rate)

                        if not self.loop_forever and total_samples_rendered >= max_samples:
                            should_loop = False
                            if self.loop_enabled and loops_completed < self.loop_count - 1:
                                loops_completed += 1
                                should_loop = True

                            if should_loop:
                                ctx.load_track(self.track_index, loop_forever=False)
                                total_samples_rendered = 0
                                continue
                            else:
                                self.ui_queue.put(("ended", None))
                                break

                        samples_to_render = chunk_samples
                        if not self.loop_forever:
                            samples_to_render = min(chunk_samples, int(max_samples - total_samples_rendered))

                        sub_buf = (ctypes.c_short * (samples_to_render * 2))()
                        success = ctx.render(samples_to_render, sub_buf)
                        
                        pcm_data = bytes(sub_buf)
                        if not pcm_data or not success:
                            should_loop = False
                            if self.loop_enabled and loops_completed < self.loop_count - 1:
                                loops_completed += 1
                                should_loop = True

                            if should_loop:
                                ctx.load_track(self.track_index, loop_forever=self.loop_forever)
                                total_samples_rendered = 0
                                continue
                            else:
                                self.ui_queue.put(("ended", None))
                                break

                        # Apply volume fade-out only when loop_forever is False and past base length
                        if not self.loop_forever and total_samples_rendered >= fade_start_sample:
                            arr = array.array("h")
                            arr.frombytes(pcm_data)
                            for i in range(0, len(arr), 2):
                                current_abs = total_samples_rendered + (i // 2)
                                fade_prog = current_abs - fade_start_sample
                                multiplier = max(0.0, 1.0 - (fade_prog / fade_samples))
                                arr[i] = int(arr[i] * multiplier)
                                arr[i+1] = int(arr[i+1] * multiplier)
                            pcm_data = arr.tobytes()

                        with self._lock:
                            vol = self.volume

                        if vol != 1.0 and len(pcm_data) > 0:
                            arr = array.array("h")
                            arr.frombytes(pcm_data)
                            for i in range(len(arr)):
                                arr[i] = max(-32768, min(32767, int(arr[i] * vol)))
                            pcm_data = arr.tobytes()

                        stream.write(pcm_data)
                        total_samples_rendered += samples_to_render

                        if self.loop_forever and base_samples > 0:
                            curr_pos_sec = (total_samples_rendered % base_samples) / sample_rate
                        else:
                            curr_pos_sec = total_samples_rendered / sample_rate

                        effective_duration_sec = ctx.duration_ms / 1000.0

                        now = time.time()
                        if now - last_pos_time >= 0.2:
                            last_pos_time = now
                            pct = int((curr_pos_sec / effective_duration_sec) * 100) if effective_duration_sec > 0 else 0
                            self.ui_queue.put(("position", (curr_pos_sec, effective_duration_sec, pct)))
                finally:
                    try:
                        stream.stop()
                        stream.close()
                    except Exception:
                        pass
        except Exception as e:
            if not self._stop_event.is_set():
                self.ui_queue.put(("error", f"Playback error: {e}"))
        finally:
            if not self._stop_event.is_set():
                self.ui_queue.put(("status", "Stopped"))


# ==========================================
# 3. Player UI Window
# ==========================================

class GMEPlayerWindow(tk.Toplevel):
    def __init__(self, parent, audio_file, playlist=None, start_index=0):
        super().__init__(parent)
        self.parent = parent
        self.audio_file = audio_file
        self.worker = None
        self.is_paused = False
        self.current_position_seconds = 0.0
        self.total_duration_seconds = 180.0
        self.is_scrubbing = False
        self.ui_queue = queue.Queue()
        
        self.track_index = 0
        self.total_subtracks = 1

        self.playlist = []
        self.current_index = 0
        self.load_playlist_files(playlist, start_index)

        self.loop_forever_var = tk.BooleanVar(value=False)
        self.loop_enabled_var = tk.BooleanVar(value=False)
        self.loop_count_var = tk.IntVar(value=1)

        saved_volume = get_saved_player_volume()
        self.volume_var = tk.IntVar(value=saved_volume)
        self.volume_label_var = tk.StringVar(value=f"Volume: {saved_volume}%")
        register_volume_player(self)

        self.position_var = tk.StringVar(value="Position: 00:00:00")
        self.duration_var = tk.StringVar(value="Duration: 00:00:00")
        self.status_var = tk.StringVar(value="Stopped")
        self.track_var = tk.StringVar(value="")
        self.seek_pct_var = tk.StringVar(value="0%")

        self.build_ui()

        try:
            icon_file = resource_path("assets/icons/player_icon.png")
            if icon_file.exists():
                self.iconphoto(False, tk.PhotoImage(file=str(icon_file)))
        except Exception:
            pass

        self.protocol("WM_DELETE_WINDOW", self.close)
        self.start_queue_polling()

        if self.playlist:
            self.play_current_track()

    def format_time(self, seconds):
        if seconds is None or seconds < 0:
            return "00:00:00"
        s = int(seconds)
        return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"

    def supports_subtracks(self, filepath):
        ext = os.path.splitext(filepath)[1].lower()
        return ext in (".nsf", ".nsfe", ".gbs", ".hes", ".kss")

    def load_playlist_files(self, provided_playlist=None, provided_index=None):
        folder = os.path.dirname(self.audio_file)
        abs_target = os.path.normpath(os.path.abspath(self.audio_file))

        if provided_playlist:
            self.playlist = [os.path.abspath(p) for p in provided_playlist]
            norm_playlist = [os.path.normpath(os.path.abspath(p)) for p in self.playlist]
            if abs_target in norm_playlist:
                self.current_index = norm_playlist.index(abs_target)
            elif provided_index is not None and 0 <= provided_index < len(self.playlist):
                self.current_index = provided_index
            else:
                self.current_index = 0
            return

        try:
            ext = os.path.splitext(self.audio_file)[1].lower()
            subtrack_exts = (".nsf", ".nsfe", ".gbs", ".hes", ".kss")

            if ext in subtrack_exts and not self.audio_file.lower().endswith(".m3u"):
                stem = Path(self.audio_file).stem
                m3u_path = os.path.join(folder, f"{stem}_tracks.m3u")

                if not os.path.exists(m3u_path):
                    try:
                        track_count = 1
                        with GMEContext(self.audio_file) as ctx:
                            track_count = ctx.get_track_count()

                        if track_count > 1:
                            with open(m3u_path, "w", encoding="utf-8") as f_m3u:
                                f_m3u.write("#EXTM3U\n")
                                for i in range(track_count):
                                    track_num = i + 1
                                    f_m3u.write(f"#EXTINF:-1,Track {track_num}\n")
                                    f_m3u.write(f"{os.path.basename(self.audio_file)}#{i}\n")
                    except Exception:
                        pass

                if os.path.exists(m3u_path):
                    self.audio_file = m3u_path

            if self.audio_file.lower().endswith(".m3u"):
                loaded_tracks = []
                with open(self.audio_file, "r", encoding="utf-8", errors="ignore") as f_m3u:
                    for line in f_m3u:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            if not os.path.isabs(line):
                                if "#" in line:
                                    fname, t_idx = line.split("#", 1)
                                    resolved_path = os.path.join(folder, fname) + f"#{t_idx}"
                                else:
                                    resolved_path = os.path.join(folder, line)
                            else:
                                resolved_path = line
                            loaded_tracks.append(resolved_path)
                
                if loaded_tracks:
                    self.playlist = loaded_tracks
                    self.current_index = 0
                    return

            gme_exts = {".nsf", ".nsfe", ".spc", ".gbs", ".hes", ".kss", ".vgm", ".vgz", ".ay", ".sap", ".gym"}
            files = sorted(os.listdir(folder))
            self.playlist = []
            for f in files:
                full_p = os.path.join(folder, f)
                if os.path.isfile(full_p) and os.path.splitext(f)[1].lower() in gme_exts:
                    self.playlist.append(full_p)
            
            norm_playlist = [os.path.normpath(os.path.abspath(p)) for p in self.playlist]
            if abs_target in norm_playlist:
                self.current_index = norm_playlist.index(abs_target)
            else:
                self.playlist = [self.audio_file]
                self.current_index = 0
        except Exception:
            self.playlist = [self.audio_file]
            self.current_index = 0

    def update_track_label_display(self):
        if self.supports_subtracks(self.audio_file) and self.total_subtracks > 1:
            self.track_var.set(f"Subtrack: {self.track_index + 1} / {self.total_subtracks}")
        else:
            self.track_var.set(f"Track: {self.current_index + 1} / {len(self.playlist)}")

    def build_ui(self):
        filename = os.path.basename(self.audio_file)
        self.title(f"GME Player - {filename}")
        self.geometry("480x640")
        self.minsize(440, 520)
        self.transient(self.parent)

        padding = ttk.Frame(self, padding=12)
        padding.pack(fill="both", expand=True)

        browse_frame = ttk.Frame(padding)
        browse_frame.pack(side="top", anchor="w", pady=(0, 6))
        tk.Button(browse_frame, text="📁", font=("Arial", 12), width=3, command=self.on_browse_clicked).pack(side="top", anchor="w")

        self.title_label = ttk.Label(padding, text=f"Now Playing:\n{filename}", font=("Arial", 9, "bold"), wraplength=440, justify="center")
        self.title_label.pack(pady=(0, 6))

        info_frame = ttk.Frame(padding)
        info_frame.pack(fill="x", pady=(0, 4))
        
        status_sub = ttk.Frame(info_frame)
        status_sub.pack(side="left", padx=5)
        ttk.Label(status_sub, textvariable=self.status_var, font=("Arial", 9)).pack(side="left")
        ttk.Label(status_sub, text=" (", font=("Arial", 9)).pack(side="left")
        ttk.Label(status_sub, textvariable=self.seek_pct_var, font=("Arial", 9, "bold")).pack(side="left")
        ttk.Label(status_sub, text=")", font=("Arial", 9)).pack(side="left")

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

        # Define a custom style for a thicker horizontal slider
        style = ttk.Style()
        style.configure("Thick.Horizontal.TScale", sliderthickness=50, troughrelief="flat")

        vol_frame = ttk.Frame(padding)
        vol_frame.pack(fill="x", pady=(0, 8))
        ttk.Label(vol_frame, textvariable=self.volume_label_var, font=("Arial", 9)).pack(side="left", padx=5)
        self.volume_slider = ttk.Scale(vol_frame, from_=0, to=100, orient="horizontal", command=self.on_volume_slide, style="Thick.Horizontal.TScale")
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
        self.playlist_listbox.bind("<Motion>", self.on_listbox_motion)
        self.playlist_listbox.bind("<Leave>", self.on_listbox_leave)
        self.populate_playlist_listbox()

        controls = ttk.Frame(padding)
        controls.pack(pady=4)

        ttk.Button(controls, text="Stop", width=8, command=self.stop).grid(row=0, column=0, padx=2)
        ttk.Button(controls, text="|<< Prev", width=8, command=self.prev_track).grid(row=0, column=1, padx=2)
        ttk.Button(controls, text="Play/Pause", width=10, command=self.toggle_play_pause).grid(row=0, column=2, padx=2)
        ttk.Button(controls, text="Next >>|", width=8, command=self.next_track).grid(row=0, column=3, padx=2)
        ttk.Button(controls, text="Close", width=8, command=self.close).grid(row=0, column=4, padx=2)

        bottom_sub_frame = ttk.Frame(padding)
        bottom_sub_frame.pack(fill="x", pady=(6, 0))

        left_loop_frame = ttk.Frame(bottom_sub_frame)
        left_loop_frame.pack(side="left")
        tk.Checkbutton(left_loop_frame, variable=self.loop_enabled_var).pack(side="left", padx=(0, 2))
        tk.Spinbox(left_loop_frame, from_=1, to=99, width=3, textvariable=self.loop_count_var).pack(side="left")

        center_rand_frame = ttk.Frame(bottom_sub_frame)
        center_rand_frame.pack(side="left", expand=True)
        ttk.Button(center_rand_frame, text="Random", width=8, command=self.play_random_track).pack()

        right_loop_frame = ttk.Frame(bottom_sub_frame)
        right_loop_frame.pack(side="right")
        # Added command to restart the song fresh when toggling loop forever
        tk.Checkbutton(
            right_loop_frame, 
            text="Loop forever", 
            variable=self.loop_forever_var, 
            command=self.on_loop_forever_toggled
        ).pack(side="right")

    def on_loop_forever_toggled(self):
        # Restart the current track fresh when loop forever is toggled
        if self.playlist:
            self.play_current_track()

    def populate_playlist_listbox(self):
        if not self.winfo_exists():
            return
        self.playlist_listbox.delete(0, tk.END)
        for p in self.playlist:
            if "#" in p:
                _, t_idx = p.split("#", 1)
                display_label = f"Track {int(t_idx) + 1}"
            else:
                display_label = os.path.basename(p)
            self.playlist_listbox.insert(tk.END, display_label)
        self.update_listbox_selection()

    def get_dynamic_hover_color(self):
        try:
            if hasattr(self, "parent") and self.parent and hasattr(self.parent, "style"):
                theme_name = self.parent.style.theme_use().lower()
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
        if not self.winfo_exists():
            return
        try:
            self.playlist_listbox.selection_clear(0, tk.END)
            if 0 <= self.current_index < self.playlist_listbox.size():
                self.playlist_listbox.selection_set(self.current_index)
                self.playlist_listbox.see(self.current_index)
        except Exception:
            pass

    def on_playlist_select(self, event):
        selection = self.playlist_listbox.curselection()
        if not selection:
            return
        idx = selection[0]
        if idx != self.current_index:
            self.current_index = idx
            self.track_index = 0
            self.play_current_track()

    def start_queue_polling(self):
        def poll():
            try:
                while True:
                    msg = self.ui_queue.get_nowait()
                    self.handle_message(msg[0], msg[1])
            except queue.Empty:
                pass
            if self.winfo_exists():
                self.after(40, poll)
        self.after(40, poll)

    def handle_message(self, m_type, data):
        if not self.winfo_exists():
            return
        if m_type == "opened":
            self.total_duration_seconds = data["duration"]
            self.total_subtracks = data["track_count"]
            self.duration_var.set(f"Duration: {self.format_time(self.total_duration_seconds)}")
            self.update_track_label_display()
        elif m_type == "position":
            curr, tot, pct = data
            if not self.is_scrubbing:
                self.current_position_seconds = curr
                self.position_var.set(f"Position: {self.format_time(curr)}")
                self.seek_pct_var.set(f"{pct}%")
                self.progress_bar_widget.update_progress(curr, self.total_duration_seconds)
        elif m_type == "status":
            self.status_var.set(str(data))
        elif m_type == "ended":
            self.next_track()
        elif m_type == "error":
            messagebox.showerror("Playback Error", str(data))

    def play_current_track(self):
        if self.worker:
            self.worker.stop()
            self.worker = None

        raw_entry = self.playlist[self.current_index]
        if "#" in raw_entry:
            base_file, track_str = raw_entry.rsplit("#", 1)
            if not os.path.isabs(base_file):
                self.audio_file = os.path.join(os.path.dirname(self.audio_file), base_file)
            else:
                self.audio_file = base_file
            try:
                self.track_index = int(track_str)
            except ValueError:
                self.track_index = 0
        else:
            self.audio_file = raw_entry
            self.track_index = 0

        filename = os.path.basename(self.audio_file)
        
        self.title(f"GME Player - {filename} (Track {self.track_index + 1})")
        if hasattr(self, "title_label") and self.winfo_exists():
            self.title_label.configure(text=f"Now Playing:\n{filename} [Track {self.track_index + 1}]")
        
        self.update_listbox_selection()
        self.update_track_label_display()

        vol_gain = max(0, min(150, self.volume_var.get())) / 100.0
        
        try:
            loop_cnt = int(self.loop_count_var.get())
        except Exception:
            loop_cnt = 1

        self.worker = GMEAudioWorker(
            self.audio_file, 
            self.track_index, 
            self.ui_queue, 
            volume=vol_gain, 
            loop_forever=self.loop_forever_var.get(),
            loop_count=loop_cnt,
            loop_enabled=self.loop_enabled_var.get()
        )
        self.worker.start()
        self.is_paused = False

    def play_random_track(self):
        if not self.playlist:
            return
        if len(self.playlist) == 1:
            self.track_index = 0
            self.play_current_track()
            return
        new_index = self.current_index
        while new_index == self.current_index:
            new_index = random.randint(0, len(self.playlist) - 1)
        self.current_index = new_index
        self.play_current_track()

    def seek_to_position(self, target_sec):
        if self.worker:
            self.worker.seek(target_sec * 1000.0)

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

    def on_volume_slide(self, val):
        set_player_volume(float(val), source_player=self)

    def apply_live_volume(self):
        vol_gain = max(0, min(150, self.volume_var.get())) / 100.0
        if self.worker:
            self.worker.set_volume(vol_gain)

    def on_browse_clicked(self):
        ExtractedFilesBrowserWindow(self, os.path.dirname(self.audio_file))

    def stop(self):
        if self.worker:
            self.worker.stop()
            self.worker = None
        self.status_var.set("Stopped")
        self.position_var.set("Position: 00:00:00")
        self.seek_pct_var.set("0%")
        if hasattr(self, "progress_bar_widget"):
            self.progress_bar_widget.update_progress(0, self.total_duration_seconds)

    def close(self):
        self.stop()
        try:
            self.destroy()
        except Exception:
            pass