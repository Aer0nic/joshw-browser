import ctypes
import os
import platform
import queue
import random
import threading
import time
import array
import sys
import subprocess
import signal
import json
import tkinter as tk
import ttkbootstrap as tb
from platform_support import SoundDeviceSink, find_tool, IS_WINDOWS, hidden_popen_kwargs, resume_process, set_process_volume, suspend_process
from player_launcher import attach_settings_sync, parse_player_args
from PIL import Image, ImageTk
from tkinter import ttk, messagebox, filedialog
from pathlib import Path
from config import resource_path, get_binary_path, get_cache_directory, is_emulated_file, apply_global_font_scale, load_saved_font_size
from players import get_saved_player_volume, register_volume_player, set_player_volume, InteractiveProgressBar, ExtractedFilesBrowserWindow

def get_shared_settings_path():
    cache_dir = Path(get_cache_directory())
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / "settings.json"

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
    lib = None
    if system == "Windows":
        names = ["libgme.dll", "gme.dll"]
        bundled = find_tool("libgme")  # assets/libraries/windows/gme/libgme.dll
        if bundled:
            try:
                lib = ctypes.CDLL(bundled)
            except OSError as e:
                print(f"[GME] Could not load bundled {bundled}: {e}")
    else:
        names = ["libgme.so.0", "libgme.so"]

    for name in ([] if lib is not None else names):
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

    if hasattr(lib, "gme_ignore_silence"):
        lib.gme_ignore_silence.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.gme_ignore_silence.restype = None

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

    def load_track(self, track_index, loop_forever=False, loop_enabled=False, loop_count=1):
        self.current_track = track_index
        lib = get_gme()
        err = lib.gme_start_track(self.emu, track_index)
        if err:
            raise RuntimeError(f"Failed to start track {track_index}: {err.decode('utf-8', errors='ignore')}")

        if loop_forever:
            if hasattr(lib, "gme_ignore_silence"):
                lib.gme_ignore_silence(self.emu, 1)
            if hasattr(lib, "gme_set_fade"):
                lib.gme_set_fade(self.emu, -1)
        else:
            if hasattr(lib, "gme_ignore_silence"):
                lib.gme_ignore_silence(self.emu, 0)
            if hasattr(lib, "gme_set_fade"):
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
            self.duration_ms = 0
        else:
            multiplier = (1 + int(loop_count)) if loop_enabled else 1
            self.duration_ms = (self.base_length_ms * multiplier) + actual_fade_ms

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
# 2. Audio Worker Thread (Using System Pipe / aplay)
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
        self.proc = None
        self.wpctl_id = None

    def _resolve_and_apply_volume(self, initial_volume):
        """Automatically links the aplay process stream ID under PipeWire/PulseAudio."""
        if not self.proc:
            return
        awk_cmd = f"wpctl status | awk '/Streams:/ {{in_streams=1; next}} in_streams && /aplay/ {{sub(/\\.$/, \"\", $1); print $1; exit}} in_streams && /^[^[:space:]]/ {{exit}}'"
        for _ in range(25):
            if not self.proc or self.proc.poll() is not None:
                break
            try:
                res = subprocess.run(
                    awk_cmd,
                    shell=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    text=True
                )
                if res.returncode == 0 and res.stdout:
                    stream_id = res.stdout.strip()
                    if stream_id.isdigit():
                        self.wpctl_id = stream_id
                        percent = max(0, min(150, int(initial_volume * 100)))
                        subprocess.run(
                            ["wpctl", "set-volume", str(stream_id), f"{percent}%"],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL
                        )
                        return
            except Exception:
                pass
            time.sleep(0.15)

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        self._pause_event.set()
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=0.5)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def pause(self):
        self._pause_event.clear()
        if self.proc and self.proc.poll() is None:
            try:
                suspend_process(self.proc)
            except Exception:
                pass

    def resume(self):
        self._pause_event.set()
        if self.proc and self.proc.poll() is None:
            try:
                resume_process(self.proc)
            except Exception:
                pass

    def set_volume(self, vol):
        with self._lock:
            self.volume = max(0.0, float(vol))
        
        if self.wpctl_id:
            try:
                percent = max(0, min(150, int(vol * 100)))
                subprocess.run(
                    ["wpctl", "set-volume", str(self.wpctl_id), f"{percent}%"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            except Exception:
                self.wpctl_id = None

    def seek(self, position_ms):
        with self._lock:
            self._seek_target_ms = position_ms

    def _run(self):
        try:
            with GMEContext(self.audio_file) as ctx:
                total_tracks = ctx.get_track_count()
                ctx.load_track(self.track_index, loop_forever=self.loop_forever, loop_enabled=self.loop_enabled, loop_count=self.loop_count)
                duration_sec = ctx.duration_ms / 1000.0 if ctx.duration_ms > 0 else 0.0

                self.ui_queue.put(("opened", {"duration": duration_sec, "track_count": total_tracks}))
                self.ui_queue.put(("status", "Playing"))

                chunk_samples = 4096
                sample_rate = ctx.sample_rate

                cmd = [
                    "aplay",
                    "-t", "raw",
                    "-c", "2",
                    "-r", str(sample_rate),
                    "-f", "S16_LE",
                    "-q"
                ]

                try:
                    if IS_WINDOWS:
                        # No aplay on Windows: play through sounddevice instead
                        self.proc = SoundDeviceSink(sample_rate=sample_rate, channels=2)
                    else:
                        self.proc = subprocess.Popen(
                            cmd,
                            stdin=subprocess.PIPE,
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL
                        )
                        threading.Thread(target=self._resolve_and_apply_volume, args=(self.volume,), daemon=True).start()
                except Exception as e:
                    self.ui_queue.put(("error", f"Could not open audio output: {e}"))
                    return

                base_length_ms = ctx.base_length_ms
                fade_ms = ctx.fade_ms
                default_fade_ms = 5000
                actual_fade_ms = default_fade_ms if (fade_ms <= 0 or fade_ms == base_length_ms) else fade_ms

                single_base_samples = int((base_length_ms / 1000.0) * sample_rate)
                multiplier = (1 + self.loop_count) if self.loop_enabled else 1
                total_base_samples = single_base_samples * multiplier
                
                fade_samples = int((actual_fade_ms / 1000.0) * sample_rate)
                fade_start_sample = total_base_samples
                max_samples = float('inf') if self.loop_forever else (total_base_samples + fade_samples)

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
                                ctx.load_track(self.track_index, loop_forever=False, loop_enabled=self.loop_enabled, loop_count=self.loop_count)
                                total_samples_rendered = 0
                                continue
                            else:
                                try:
                                    if self.proc and self.proc.stdin:
                                        self.proc.stdin.close()
                                    if self.proc:
                                        self.proc.wait(timeout=0.5)
                                except Exception:
                                    pass
                                
                                self.ui_queue.put(("ended", None))
                                break

                        if not self.loop_forever and self.loop_enabled and total_samples_rendered > 0 and total_samples_rendered % single_base_samples == 0 and total_samples_rendered < total_base_samples:
                            ctx.seek(0)

                        samples_to_render = chunk_samples
                        if not self.loop_forever:
                            samples_to_render = min(chunk_samples, int(max_samples - total_samples_rendered))

                        sub_buf = (ctypes.c_short * (samples_to_render * 2))()
                        success = ctx.render(samples_to_render, sub_buf)
                        
                        pcm_data = bytes(sub_buf)
                        if not pcm_data or not success:
                            if not self.loop_forever:
                                try:
                                    if self.proc and self.proc.stdin:
                                        self.proc.stdin.close()
                                    if self.proc:
                                        self.proc.wait(timeout=0.5)
                                except Exception:
                                    pass
                                self.ui_queue.put(("ended", None))
                                break
                            else:
                                continue

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

                        try:
                            if self.proc and self.proc.stdin:
                                self.proc.stdin.write(pcm_data)
                                self.proc.stdin.flush()
                        except Exception:
                            break

                        total_samples_rendered += samples_to_render

                        curr_pos_sec = total_samples_rendered / sample_rate
                        effective_duration_sec = ctx.duration_ms / 1000.0 if ctx.duration_ms > 0 else 0.0

                        now = time.time()
                        if now - last_pos_time >= 0.2:
                            last_pos_time = now
                            pct = 0 if self.loop_forever else (int((curr_pos_sec / effective_duration_sec) * 100) if effective_duration_sec > 0 else 0)
                            self.ui_queue.put(("position", (curr_pos_sec, effective_duration_sec, pct)))
                finally:
                    if self.proc:
                        try:
                            if self.proc.stdin:
                                self.proc.stdin.close()
                            self.proc.wait(timeout=1.0)
                        except Exception:
                            try:
                                self.proc.terminate()
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

class GMEPlayerWindow(tb.Window):
    def __init__(self, parent=None, audio_file=None, playlist=None, start_index=0):
        # Load theme settings for standalone root window
        initial_settings = {}
        try:
            settings_path = get_shared_settings_path()
            if settings_path.exists():
                initial_settings = json.loads(settings_path.read_text(encoding="utf-8"))
        except Exception:
            pass
        saved_theme = initial_settings.get("theme", "cosmo")

        super().__init__(themename=saved_theme)
        
        # Load font size on startup and apply scaling
        self.font_size = load_saved_font_size()
        apply_global_font_scale(self.font_size)

        # Track settings file modification time for live polling
        self.settings_mtime = self.get_settings_mtime()

        self.parent = parent
        self.playlist = []
        self.current_index = 0
        self.playlist_dir = ""

        # Standalone playlist handling and path expansion
        if playlist:
            self.playlist = [os.path.abspath(p) for p in playlist]
            self.current_index = start_index if start_index is not None and 0 <= start_index < len(self.playlist) else 0
            if self.playlist:
                first_clean = self.playlist[0].rsplit("#", 1)[0]
                self.playlist_dir = os.path.dirname(first_clean)
        elif audio_file:
            audio_path = Path(audio_file).resolve()
            self.playlist_dir = str(audio_path.parent)
            resolved_audio = generate_gme_m3u_if_needed(str(audio_path))
            if resolved_audio.endswith(".m3u"):
                self.load_playlist_files(provided_m3u=resolved_audio)
            else:
                target_dir = audio_path.parent
                gme_exts = {".nsf", ".nsfe", ".spc", ".gbs", ".hes", ".kss", ".vgm", ".vgz", ".ay", ".sap", ".gym"}
                found_files = sorted([
                    str(p) for p in target_dir.glob("*") 
                    if p.is_file() and p.suffix.lower() in gme_exts
                ])
                if found_files and str(audio_path) in found_files:
                    self.playlist = found_files
                    self.current_index = found_files.index(str(audio_path))
                else:
                    self.playlist = [str(audio_path)]
                    self.current_index = 0

        first_entry = self.playlist[self.current_index] if self.playlist else ""
        self.audio_file = first_entry.rsplit("#", 1)[0] if "#" in first_entry else first_entry

        self.worker = None
        self.is_paused = False
        self.current_position_seconds = 0.0
        self.total_duration_seconds = 180.0
        self.is_scrubbing = False
        self.ui_queue = queue.Queue()
        
        self.track_index = 0
        self.total_subtracks = 1

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
        self.proc = None
        self.wpctl_id = None

        self.build_ui()

        try:
            icon_file = resource_path("assets/icons/gmeicon.png")
            if icon_file.exists():
                self.iconphoto(False, tk.PhotoImage(file=str(icon_file)))
        except Exception:
            pass

        self.protocol("WM_DELETE_WINDOW", self.close)
        self.start_queue_polling()
        self.start_settings_polling()
        attach_settings_sync(self)  # theme (incl. browser hover previews)

        if self.playlist:
            self.play_current_track()

    def get_settings_mtime(self):
        try:
            settings_path = get_shared_settings_path()
            return settings_path.stat().st_mtime if settings_path.exists() else 0
        except Exception:
            return 0

    def start_settings_polling(self):
        def check_settings():
            if not self.winfo_exists():
                return
            mtime = self.get_settings_mtime()
            if mtime != self.settings_mtime:
                self.settings_mtime = mtime
                new_size = load_saved_font_size()
                if new_size != self.font_size:
                    self.apply_live_font_size(new_size)
            self.after(500, check_settings)

        self.after(500, check_settings)

    def apply_live_font_size(self, new_size):
        self.font_size = new_size
        apply_global_font_scale(new_size)

        fs = self.font_size
        filename = os.path.basename(self.audio_file) if self.audio_file else "No file"

        if hasattr(self, "title_label") and self.title_label.winfo_exists():
            self.title_label.configure(font=("Arial", fs, "bold"))
        if hasattr(self, "playlist_listbox") and self.playlist_listbox.winfo_exists():
            self.playlist_listbox.configure(font=("Arial", fs))

        base_w = max(550, int(480 * (fs / 10)))
        base_h = max(520, int(640 * (fs / 10)))
        self.geometry(f"{base_w}x{base_h}")
        self.minsize(base_w, base_h)

    def format_time(self, seconds):
        if seconds is None or seconds < 0:
            return "00:00:00"
        s = int(seconds)
        return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"

    def supports_subtracks(self, filepath):
        ext = os.path.splitext(filepath)[1].lower()
        return ext in (".nsf", ".nsfe", ".gbs", ".hes", ".kss")

    def load_playlist_files(self, provided_playlist=None, provided_index=None, provided_m3u=None):
        m3u_file = provided_m3u or (self.audio_file if self.audio_file.endswith(".m3u") else None)
        
        if m3u_file:
            folder = os.path.dirname(m3u_file)
            self.playlist_dir = folder
            loaded_tracks = []
            try:
                with open(m3u_file, "r", encoding="utf-8", errors="ignore") as f_m3u:
                    for line in f_m3u:
                        line = line.strip()
                        if line and not line.startswith("#"):
                            if "#" in line:
                                fname, t_idx = line.rsplit("#", 1)
                                abs_fname = os.path.abspath(os.path.join(folder, fname))
                                resolved_path = f"{abs_fname}#{t_idx}"
                            else:
                                resolved_path = os.path.abspath(os.path.join(folder, line))
                            loaded_tracks.append(resolved_path)
            except Exception:
                pass

            if loaded_tracks:
                self.playlist = loaded_tracks
                self.current_index = 0
                return

        if provided_playlist:
            self.playlist = [os.path.abspath(p) for p in provided_playlist]
            if self.playlist:
                first_clean = self.playlist[0].rsplit("#", 1)[0]
                self.playlist_dir = os.path.dirname(first_clean)
            if provided_index is not None and 0 <= provided_index < len(self.playlist):
                self.current_index = provided_index
            else:
                self.current_index = 0

    def update_track_label_display(self):
        if self.supports_subtracks(self.audio_file) and self.total_subtracks > 1:
            self.track_var.set(f"Subtrack: {self.track_index + 1} / {self.total_subtracks}")
        else:
            self.track_var.set(f"Track: {self.current_index + 1} / {len(self.playlist)}")

    def build_ui(self):
        filename = os.path.basename(self.audio_file) if self.audio_file else "No file"
        self.title(f"GME Player - {filename}")
        
        base_w = max(550, int(480 * (self.font_size / 10)))
        base_h = max(520, int(640 * (self.font_size / 10)))
        self.geometry(f"{base_w}x{base_h}")
        self.minsize(base_w, base_h)

        fs = self.font_size

        padding = tb.Frame(self, padding=12)
        padding.pack(fill="both", expand=True)

        header_frame = tb.Frame(padding)
        header_frame.pack(side="top", fill="x", pady=(0, 6))

        browse_btn = tb.Button(header_frame, text="📁", width=3, bootstyle="outline-primary", command=self.on_browse_clicked)
        browse_btn.pack(side="left", anchor="center", padx=(0, 8))

        self.banner_canvas = tb.Canvas(header_frame, height=85, highlightthickness=0)
        self.banner_canvas.pack(side="left", fill="x", expand=True)

        try:
            banner_path = resource_path("assets/icons/gmebanner.png")
            if banner_path.exists():
                self.raw_banner_img = Image.open(str(banner_path)).convert("RGBA")
                self.banner_image_item = self.banner_canvas.create_image(0, 0, anchor="nw")

                def resize_banner(event):
                    if event.width > 1 and event.height > 1:
                        resized_pil = self.raw_banner_img.resize((event.width, event.height), Image.Resampling.LANCZOS)
                        self.banner_photo = ImageTk.PhotoImage(resized_pil)
                        self.banner_canvas.itemconfig(self.banner_image_item, image=self.banner_photo)

                self.banner_canvas.bind("<Configure>", resize_banner)
        except Exception as e:
            print(f"[USFPlayer] Could not load banner image: {e}")

        self.title_label = tb.Label(padding, text=f"Now Playing:\n{filename}", font=("Arial", fs, "bold"), wraplength=440, justify="center")
        self.title_label.pack(pady=(0, 6))

        info_frame = tb.Frame(padding)
        info_frame.pack(fill="x", pady=(0, 4))
        
        status_sub = tb.Frame(info_frame)
        status_sub.pack(side="left", padx=5)
        tb.Label(status_sub, textvariable=self.status_var, font=("Arial", fs)).pack(side="left")
        tb.Label(status_sub, text=" (", font=("Arial", fs)).pack(side="left")
        tb.Label(status_sub, textvariable=self.seek_pct_var, font=("Arial", fs, "bold")).pack(side="left")
        tb.Label(status_sub, text=")", font=("Arial", fs)).pack(side="left")

        tb.Label(info_frame, textvariable=self.position_var, font=("Arial", fs)).pack(side="right", padx=5)

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
        tb.Label(meta_frame, textvariable=self.track_var, font=("Arial", fs, "bold")).pack(side="left", padx=5)
        tb.Label(meta_frame, textvariable=self.duration_var, font=("Arial", fs)).pack(side="right", padx=5)

        style = tb.Style()
        style.configure("Thick.Horizontal.TScale", sliderthickness=50, troughrelief="flat")

        vol_frame = tb.Frame(padding)
        vol_frame.pack(fill="x", pady=(0, 8))
        tb.Label(vol_frame, textvariable=self.volume_label_var, font=("Arial", fs)).pack(side="left", padx=5)
        self.volume_slider = tb.Scale(vol_frame, from_=0, to=100, orient="horizontal", command=self.on_volume_slide, style="Thick.Horizontal.TScale")
        self.volume_slider.set(self.volume_var.get())
        self.volume_slider.pack(side="right", fill="x", expand=True, padx=5)

        list_frame = tb.LabelFrame(padding, text="Playlist Files", padding=6)
        list_frame.pack(fill="both", expand=True, pady=(0, 10))

        self.playlist_listbox = tb.Listbox(list_frame, font=("Arial", fs), selectmode="single", exportselection=False)
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

        bottom_sub_frame = tb.Frame(padding)
        bottom_sub_frame.pack(fill="x", pady=(6, 0))

        left_loop_frame = tb.Frame(bottom_sub_frame)
        left_loop_frame.pack(side="left")
        tb.Checkbutton(
            left_loop_frame, 
            variable=self.loop_enabled_var,
            command=self.on_loop_controls_changed
        ).pack(side="left", padx=(0, 2))
        tb.Spinbox(
            left_loop_frame, 
            from_=1, 
            to=99, 
            width=3, 
            textvariable=self.loop_count_var,
            command=self.on_loop_controls_changed
        ).pack(side="left")

        center_rand_frame = tb.Frame(bottom_sub_frame)
        center_rand_frame.pack(side="left", expand=True)
        tb.Button(center_rand_frame, text="Random", width=8, command=self.play_random_track).pack()

        right_loop_frame = tb.Frame(bottom_sub_frame)
        right_loop_frame.pack(side="right")
        tb.Checkbutton(
            right_loop_frame, 
            text="Loop forever", 
            variable=self.loop_forever_var, 
            command=self.on_loop_forever_toggled
        ).pack(side="right")

    def on_loop_controls_changed(self):
        if self.loop_enabled_var.get() and self.loop_forever_var.get():
            self.loop_forever_var.set(False)
        if self.playlist:
            self.play_current_track()

    def on_loop_forever_toggled(self):
        if self.loop_forever_var.get() and self.loop_enabled_var.get():
            self.loop_enabled_var.set(False)
        if self.loop_forever_var.get():
            self.duration_var.set("Duration: forever")
        else:
            self.duration_var.set(f"Duration: {self.format_time(self.total_duration_seconds)}")
        if self.playlist:
            self.play_current_track()

    def populate_playlist_listbox(self):
        if not self.winfo_exists():
            return
        self.playlist_listbox.delete(0, tk.END)
        for p in self.playlist:
            if "#" in p:
                _, t_idx = p.rsplit("#", 1)
                display_label = f"Track {int(t_idx) + 1}"
            else:
                display_label = os.path.basename(p)
            self.playlist_listbox.insert(tk.END, display_label)
        self.update_listbox_selection()

    def get_dynamic_hover_color(self):
        try:
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
            if self.loop_forever_var.get():
                self.duration_var.set("Duration: forever")
            else:
                self.duration_var.set(f"Duration: {self.format_time(self.total_duration_seconds)}")
            self.update_track_label_display()
        elif m_type == "position":
            curr, tot, pct = data
            if not self.is_scrubbing:
                self.current_position_seconds = curr
                self.position_var.set(f"Position: {self.format_time(curr)}")
                if self.loop_forever_var.get():
                    self.duration_var.set("Duration: forever")
                else:
                    self.duration_var.set(f"Duration: {self.format_time(self.total_duration_seconds)}")
                self.seek_pct_var.set(f"{pct}%" if not self.loop_forever_var.get() else "0%")
                self.progress_bar_widget.update_progress(curr, self.total_duration_seconds if not self.loop_forever_var.get() else 0.0)
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

        if not self.playlist or not (0 <= self.current_index < len(self.playlist)):
            return

        raw_entry = self.playlist[self.current_index]

        if "#" in raw_entry:
            base_file, track_str = raw_entry.rsplit("#", 1)
            if not os.path.isabs(base_file) and self.playlist_dir:
                target_audio_file = os.path.join(self.playlist_dir, base_file)
            else:
                target_audio_file = base_file
            try:
                self.track_index = int(track_str)
            except ValueError:
                self.track_index = 0
        else:
            if not os.path.isabs(raw_entry) and self.playlist_dir:
                target_audio_file = os.path.join(self.playlist_dir, raw_entry)
            else:
                target_audio_file = raw_entry
            self.track_index = 0

        target_audio_file = os.path.abspath(target_audio_file)

        # Assign the actual audio file path to self.audio_file so GMEContext gets a valid audio container
        self.audio_file = target_audio_file
        filename = os.path.basename(self.audio_file)
        
        self.title(f"GME Player - {filename} (Track {self.track_index + 1})")
        if hasattr(self, "title_label") and self.winfo_exists():
            self.title_label.configure(text=f"Now Playing:\n{filename} [Track {self.track_index + 1}]")
        
        self.update_listbox_selection()
        self.update_track_label_display()

        vol_gain = max(0, min(100, self.volume_var.get())) / 100.0
        
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
        try:
            val_int = int(float(val))
            self.volume_var.set(val_int)
            self.volume_label_var.set(f"Volume: {val_int}%")
        except Exception:
            pass
        self.apply_live_volume()

    def apply_live_volume(self):
        vol_gain = max(0, min(100, self.volume_var.get())) / 100.0
        if self.worker:
            self.worker.set_volume(vol_gain)

    def on_browse_clicked(self):
        initial_dir = os.path.dirname(self.audio_file) if self.audio_file else os.path.expanduser("~")
        file_path = filedialog.askopenfilename(
            title="Select GME Music File",
            initialdir=initial_dir,
            filetypes=[("GME Supported Files", "*.nsf *.nsfe *.spc *.gbs *.hes *.kss *.vgm *.vgz"), ("All Files", "*.*")]
        )
        if file_path:
            resolved = generate_gme_m3u_if_needed(file_path)
            self.audio_file = resolved
            self.load_files_and_play(resolved)

    def load_files_and_play(self, file_path):
        target_dir = Path(file_path).parent
        self.playlist_dir = str(target_dir)
        gme_exts = {".nsf", ".nsfe", ".spc", ".gbs", ".hes", ".kss", ".vgm", ".vgz", ".ay", ".sap", ".gym"}
        
        if file_path.endswith(".m3u"):
            self.load_playlist_files(provided_m3u=file_path)
        else:
            found_files = sorted([
                str(p) for p in target_dir.glob("*") 
                if p.is_file() and p.suffix.lower() in gme_exts
            ])
            self.playlist = found_files if found_files else [file_path]

        try:
            self.current_index = self.playlist.index(file_path)
        except ValueError:
            self.current_index = 0

        self.populate_playlist_listbox()
        self.play_current_track()

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
            sys.exit(0)

GMEPlayer = GMEPlayerWindow

if __name__ == "__main__":
    audio_path, playlist, start_index = parse_player_args()
    app = GMEPlayerWindow(audio_file=audio_path, playlist=playlist, start_index=start_index or 0)
    if not audio_path:
        app.after(100, app.on_browse_clicked)
    app.mainloop()