import json
import os
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from config import get_cache_directory, is_emulated_file


def get_vgmstream_path():
    for p in [shutil.which("vgmstream123"), "/usr/local/bin/vgmstream123", "/usr/bin/vgmstream123", os.path.join(get_cache_directory(), "vgmstream123")]:
        if p and os.path.exists(p):
            return p
    return "vgmstream123"


def get_lazyusf_path():
    for p in [shutil.which("lazyusf"), "/usr/local/bin/lazyusf", "/usr/bin/lazyusf", os.path.join(get_cache_directory(), "lazyusf")]:
        if p and os.path.exists(p):
            return p
    return "lazyusf"


class ExtractedFilesBrowserWindow(tk.Toplevel):
    def __init__(self, parent, folder_path):
        super().__init__(parent)
        self.parent = parent
        self.folder_path = folder_path
        self.title(f"Extracted Files - {os.path.basename(folder_path)}")
        self.geometry("520x450")
        self.minsize(400, 300)
        self.transient(parent)

        self.files_list = []
        self.populate_files()
        self.build_ui()

    def populate_files(self):
        self.files_list = []
        supported_extensions = {
            ".ay", ".gbs", ".gym", ".hes", ".kss", ".nsf", ".nsfe", ".sap", ".spc", ".vgm", ".vgz",
            ".usf", ".miniusf", ".mp3", ".ogg", ".flac", ".wav", ".m4a", ".aac", ".opus", ".wma",
            ".ape", ".wv", ".mod", ".s3m", ".xm", ".it", ".mka", ".tta", ".ac3", ".dts", ".aiff", ".au", ".ra"
        }
        for root, dirs, files in os.walk(self.folder_path):
            for f in sorted(files):
                ext = os.path.splitext(f)[1].lower()
                if ext in supported_extensions or not supported_extensions:
                    full_p = os.path.join(root, f)
                    rel_p = os.path.relpath(full_p, self.folder_path)
                    self.files_list.append((rel_p, full_p))
        self.files_list.sort(key=lambda x: x[0].lower())

    def build_ui(self):
        padding = ttk.Frame(self, padding=10)
        padding.pack(fill="both", expand=True)

        ttk.Label(padding, text=f"Directory: {self.folder_path}", font=("Arial", 9, "bold"), wraplength=500).pack(anchor="w", pady=(0, 6))

        list_frame = ttk.Frame(padding)
        list_frame.pack(fill="both", expand=True, pady=(0, 10))

        self.listbox = tk.Listbox(list_frame, font=("Arial", 9), selectmode="single", exportselection=False)
        self.listbox.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.listbox.yview)
        scrollbar.pack(side="right", fill="y")
        self.listbox.configure(yscrollcommand=scrollbar.set)

        for rel_p, _ in self.files_list:
            self.listbox.insert(tk.END, rel_p)

        self.listbox.bind("<Double-1>", lambda e: self.play_selected())

        btn_frame = ttk.Frame(padding)
        btn_frame.pack(fill="x")

        ttk.Button(btn_frame, text="Play / Stream Selected", command=self.play_selected).pack(side="left", padx=5)
        ttk.Button(btn_frame, text="Close", command=self.destroy).pack(side="right", padx=5)

    def play_selected(self):
        selection = self.listbox.curselection()
        if not selection:
            return
        idx = selection[0]
        _, audio_file = self.files_list[idx]
        self.parent.play_audio_file(audio_file)


class VGMStreamPlayerWindow(tk.Toplevel):
    def __init__(self, parent, audio_file):
        super().__init__(parent)
        self.parent = parent
        self.audio_file = audio_file
        self.vgmstream_proc = None
        self.is_paused = False
        self.current_position_seconds = 0.0

        is_emu = is_emulated_file(self.audio_file)
        self.loop_chk_var = tk.BooleanVar(value=is_emu)
        self.loop_count_var = tk.IntVar(value=1 if is_emu else 1)
        self.loop_forever_var = tk.BooleanVar(value=False)

        self.playlist = []
        self.current_index = 0
        self.total_chapters = 1
        self.current_chapter = 0

        self.load_playlist_files()
        self.generate_m3u_playlist()

        self.position_var = tk.StringVar(value="Position: 0.0 s")
        self.duration_var = tk.StringVar(value="Duration: unknown")
        self.status_var = tk.StringVar(value="Stopped")
        self.chapter_var = tk.StringVar(value=f"Subsong: 1 / {self.total_chapters}")

        self.build_ui()
        self.protocol("WM_DELETE_WINDOW", self.close)

        if self.playlist:
            self.play_current_track()

    def generate_m3u_playlist(self):
        folder = os.path.dirname(self.audio_file)
        filename = os.path.basename(self.audio_file)
        m3u_path = os.path.join(folder, f"{os.path.splitext(filename)[0]}_vgm.m3u")
        try:
            with open(m3u_path, "w", encoding="utf-8") as f:
                f.write("#EXTM3U\n")
                for p in self.playlist:
                    f.write(f"{os.path.basename(p)}\n")
            self.m3u_path = m3u_path
        except Exception:
            self.m3u_path = self.audio_file

    def load_playlist_files(self):
        self.total_chapters = 1
        self.playlist = []
        vgm_bin = get_vgmstream_path()
        if vgm_bin and (shutil.which(vgm_bin) or os.path.exists(vgm_bin)):
            try:
                cmd = [vgm_bin, "-m", self.audio_file]
                result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=5)
                for line in result.stdout.splitlines():
                    if "subsongs:" in line.lower() or "tracks:" in line.lower():
                        m = re.search(r'(\d+)', line)
                        if m:
                            self.total_chapters = int(m.group(1))
                            break
            except Exception:
                pass

        if self.total_chapters > 1:
            for _ in range(1, self.total_chapters + 1):
                self.playlist.append(self.audio_file)
            self.current_index = 0
        else:
            folder = os.path.dirname(self.audio_file)
            try:
                for f in sorted(os.listdir(folder)):
                    full_p = os.path.join(folder, f)
                    if os.path.isfile(full_p) and not f.endswith(".m3u"):
                        self.playlist.append(full_p)
                if self.audio_file in self.playlist:
                    self.current_index = self.playlist.index(self.audio_file)
                else:
                    self.playlist = [self.audio_file]
                    self.current_index = 0
            except Exception:
                self.playlist = [self.audio_file]
                self.current_index = 0

    def build_ui(self):
        filename = os.path.basename(self.audio_file)
        self.title(f"vgmstream123 Controller - {filename}")
        self.geometry("480x440")
        self.minsize(440, 380)
        self.transient(self.parent)

        padding = ttk.Frame(self, padding=12)
        padding.pack(fill="both", expand=True)

        self.title_label = ttk.Label(padding, text=f"Now Playing:\n{filename}", font=("Arial", 9, "bold"), wraplength=450, justify="center")
        self.title_label.pack(pady=(0, 6))

        info_frame = ttk.Frame(padding)
        info_frame.pack(fill="x", pady=(0, 4))
        ttk.Label(info_frame, textvariable=self.status_var, font=("Arial", 9)).pack(side="left", padx=5)
        ttk.Label(info_frame, textvariable=self.position_var, font=("Arial", 9)).pack(side="right", padx=5)

        meta_frame = ttk.Frame(padding)
        meta_frame.pack(fill="x", pady=(0, 8))
        ttk.Label(meta_frame, textvariable=self.chapter_var, font=("Arial", 9, "bold")).pack(side="left", padx=5)
        ttk.Label(meta_frame, textvariable=self.duration_var, font=("Arial", 9)).pack(side="right", padx=5)

        list_frame = ttk.LabelFrame(padding, text="Playlist Files / Subsongs", padding=6)
        list_frame.pack(fill="both", expand=True, pady=(0, 10))

        self.chapter_listbox = tk.Listbox(list_frame, font=("Arial", 9), selectmode="single", exportselection=False)
        self.chapter_listbox.pack(side="left", fill="both", expand=True)
        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.chapter_listbox.yview)
        scrollbar.pack(side="right", fill="y")
        self.chapter_listbox.configure(yscrollcommand=scrollbar.set)
        self.chapter_listbox.bind("<<ListboxSelect>>", self.on_chapter_select)
        self.populate_playlist_listbox()

        controls = ttk.Frame(padding)
        controls.pack(pady=4)

        ttk.Button(controls, text="|<< Prev", width=8, command=self.prev_track).grid(row=0, column=0, padx=2)
        ttk.Button(controls, text="-10s", width=6, command=lambda: self.seek_relative(-10)).grid(row=0, column=1, padx=2)
        ttk.Button(controls, text="Play/Pause", width=10, command=self.toggle_play_pause).grid(row=0, column=2, padx=2)
        ttk.Button(controls, text="+10s", width=6, command=lambda: self.seek_relative(10)).grid(row=0, column=3, padx=2)
        ttk.Button(controls, text="Next >>|", width=8, command=self.next_track).grid(row=0, column=4, padx=2)

        btn_bottom = ttk.Frame(padding)
        btn_bottom.pack(pady=(6, 0), fill="x")

        ttk.Checkbutton(btn_bottom, variable=self.loop_chk_var, command=self.on_loop_settings_changed).pack(side="left", padx=(0, 2))
        ttk.Spinbox(btn_bottom, from_=1, to=99, width=3, textvariable=self.loop_count_var, command=self.on_loop_settings_changed).pack(side="left", padx=(0, 6))
        ttk.Button(btn_bottom, text="Stop", width=8, command=self.stop).pack(side="left", padx=2)
        ttk.Checkbutton(btn_bottom, text="Loop forever", variable=self.loop_forever_var, command=self.on_loop_settings_changed).pack(side="right", padx=(4, 0))
        ttk.Button(btn_bottom, text="Close", width=8, command=self.close).pack(side="right", padx=2)

    def on_loop_settings_changed(self):
        self.play_current_track(start_time=self.current_position_seconds)

    def populate_playlist_listbox(self):
        if not self.winfo_exists():
            return
        self.chapter_listbox.delete(0, tk.END)
        if self.total_chapters > 1:
            for i in range(1, self.total_chapters + 1):
                self.chapter_listbox.insert(tk.END, f"Subsong {i}")
        else:
            for p in self.playlist:
                self.chapter_listbox.insert(tk.END, os.path.basename(p))
        self.update_listbox_selection()

    def update_listbox_selection(self):
        if not self.winfo_exists():
            return
        try:
            self.chapter_listbox.selection_clear(0, tk.END)
            if 0 <= self.current_index < self.chapter_listbox.size():
                self.chapter_listbox.selection_set(self.current_index)
                self.chapter_listbox.see(self.current_index)
        except Exception:
            pass

    def start_vgmstream(self, start_time=0.0):
        if self.vgmstream_proc:
            try:
                self.vgmstream_proc.terminate()
            except Exception:
                pass

        vgm_bin = get_vgmstream_path()
        cmd = [vgm_bin, "-m"]
        if self.loop_forever_var.get():
            cmd.extend(["-l", "0"])
        elif self.loop_chk_var.get():
            cmd.extend(["-l", str(self.loop_count_var.get())])

        if start_time > 0:
            cmd.extend(["-f", str(start_time)])
        if self.total_chapters > 1:
            cmd.extend(["-s", str(self.current_chapter + 1)])
        cmd.append(self.audio_file)

        try:
            self.vgmstream_proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            self.is_paused = False
            self.status_var.set("Playing")
            threading.Thread(target=self.read_output, daemon=True).start()
        except Exception as e:
            messagebox.showerror("vgmstream123 Error", f"Could not launch vgmstream123:\n\n{e}")

    def read_output(self):
        proc = self.vgmstream_proc
        if not proc:
            return
        try:
            while proc.poll() is None:
                line = proc.stdout.readline()
                if not line:
                    break
                line_str = line.strip()
                if line_str:
                    match = re.search(r'([\d\.]+)\s*/\s*([\d\.]+)', line_str)
                    if match and self.winfo_exists():
                        curr, dur = match.groups()
                        self.current_position_seconds = float(curr)
                        self.position_var.set(f"Position: {curr} s")
        except Exception:
            pass

    def play_current_track(self, start_time=0.0):
        if self.total_chapters > 1:
            self.current_chapter = self.current_index
            self.chapter_var.set(f"Subsong: {self.current_chapter + 1} / {self.total_chapters}")
            filename = f"{os.path.basename(self.audio_file)} (Subsong {self.current_chapter + 1})"
        else:
            self.audio_file = self.playlist[self.current_index]
            filename = os.path.basename(self.audio_file)
            self.chapter_var.set("Subsong: 1 / 1")

        if hasattr(self, "title_label") and self.winfo_exists():
            self.title_label.configure(text=f"Now Playing:\n{filename}")
        self.current_position_seconds = start_time
        self.start_vgmstream(start_time=start_time)
        self.update_listbox_selection()

    def toggle_play_pause(self):
        if not self.vgmstream_proc:
            self.play_current_track()
            return
        try:
            if self.is_paused:
                os.kill(self.vgmstream_proc.pid, signal.SIGCONT)
                self.is_paused = False
                self.status_var.set("Playing")
            else:
                os.kill(self.vgmstream_proc.pid, signal.SIGSTOP)
                self.is_paused = True
                self.status_var.set("Paused")
        except Exception:
            pass

    def seek_relative(self, seconds):
        new_pos = max(0.0, self.current_position_seconds + seconds)
        self.play_current_track(start_time=new_pos)

    def prev_track(self):
        if len(self.playlist) > 1:
            self.current_index = (self.current_index - 1) % len(self.playlist)
            self.play_current_track()

    def next_track(self):
        if len(self.playlist) > 1:
            self.current_index = (self.current_index + 1) % len(self.playlist)
            self.play_current_track()

    def on_chapter_select(self, event):
        selection = self.chapter_listbox.curselection()
        if not selection:
            return
        idx = selection[0]
        if idx != self.current_index:
            self.current_index = idx
            self.play_current_track()

    def stop(self):
        if self.vgmstream_proc:
            try:
                self.vgmstream_proc.terminate()
            except Exception:
                pass
            self.vgmstream_proc = None
        self.status_var.set("Stopped")

    def close(self):
        self.stop()
        try:
            self.destroy()
        except Exception:
            pass


class MPVPlayerWindow(tk.Toplevel):
    def __init__(self, parent, audio_file):
        super().__init__(parent)
        self.parent = parent
        self.audio_file = audio_file
        self.mpv = None
        self.sock = None
        self.socket_path = os.path.join(tempfile.gettempdir(), f"mpv-python-{os.getpid()}-{threading.get_ident()}.sock")

        is_emu = is_emulated_file(self.audio_file)
        self.loop_chk_var = tk.BooleanVar(value=is_emu)
        self.loop_count_var = tk.IntVar(value=1 if is_emu else 1)
        self.loop_forever_var = tk.BooleanVar(value=False)

        self.playlist = []
        self.current_index = 0
        self.current_track_index = None
        self.load_playlist_files()

        self.songs_folder = os.path.dirname(self.audio_file)
        self.duration_cache_path = os.path.join(self.songs_folder, "track_durations.json")
        self.duration_cache = self.load_duration_cache()

        self.volume_var = tk.IntVar(value=100)
        self.volume_label_var = tk.StringVar(value="Volume: 100%")
        self.position_var = tk.StringVar(value="Position: 00:00:00")
        self.duration_var = tk.StringVar(value="Duration: 00:00:00")
        self.status_var = tk.StringVar(value="Stopped")
        self.track_var = tk.StringVar(value=f"Track: 1 / {len(self.playlist)}")

        self.build_ui()
        self.protocol("WM_DELETE_WINDOW", self.close)

        ext = os.path.splitext(self.audio_file)[1].lower()
        if ext not in (".hes", ".nsf", ".nsfe", ".miniusf", ".usf"):
            self.generate_m3u_playlist()

        if self.playlist:
            self.play_current_track()
            self.poll_status()
            threading.Thread(target=self.background_duration_worker, daemon=True).start()

    def format_time(self, seconds):
        if seconds is None or seconds < 0:
            return "00:00:00"
        total_seconds = int(seconds)
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        secs = total_seconds % 60
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    def load_duration_cache(self):
        if os.path.exists(self.duration_cache_path):
            try:
                with open(self.duration_cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        return data
            except Exception:
                pass
        return {}

    def save_duration_cache(self):
        try:
            with open(self.duration_cache_path, "w", encoding="utf-8") as f:
                json.dump(self.duration_cache, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def background_duration_worker(self):
        updated = False
        for idx, item in enumerate(self.playlist):
            if not self.winfo_exists():
                break
            
            if isinstance(item, dict):
                f_path = item["file"]
                t_idx = item.get("track_index")
                cache_key = f"{os.path.basename(f_path)}::idx={t_idx}"
            else:
                f_path = item
                cache_key = os.path.basename(f_path)

            if cache_key in self.duration_cache:
                continue

            try:
                probe_cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", f_path]
                res = subprocess.run(probe_cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, timeout=3)
                if res.returncode == 0 and res.stdout.strip():
                    dur_val = float(res.stdout.strip())
                    self.duration_cache[cache_key] = dur_val
                    updated = True
                    if idx == self.current_index and self.winfo_exists():
                        self.duration_var.set(f"Duration: {self.format_time(dur_val)}")
            except Exception:
                pass

        if updated:
            self.save_duration_cache()

    def load_playlist_files(self):
        folder = os.path.dirname(self.audio_file)
        ext = os.path.splitext(self.audio_file)[1].lower()
        audio_basename = os.path.splitext(os.path.basename(self.audio_file))[0]
        audio_filename = os.path.basename(self.audio_file)

        if ext in (".hes", ".nsf", ".nsfe", ".gbs", ".miniusf", ".usf"):
            self.playlist = []
            if ext in (".miniusf", ".usf"):
                try:
                    for f in sorted(os.listdir(folder)):
                        if f.lower().endswith((".miniusf", ".usf")):
                            self.playlist.append(os.path.join(folder, f))
                    if not self.playlist:
                        self.playlist = [self.audio_file]
                    if self.audio_file in self.playlist:
                        self.current_index = self.playlist.index(self.audio_file)
                    else:
                        self.current_index = 0
                except Exception:
                    self.playlist = [self.audio_file]
                    self.current_index = 0
            elif ext == ".gbs":
                gbs_tracks = []
                try:
                    for f in os.listdir(folder):
                        if f.lower().endswith(".m3u"):
                            m3u_basename = os.path.splitext(f)[0]
                            m = re.match(r'^(\d+)\s*(.*)$', m3u_basename)
                            if m:
                                track_num = int(m.group(1))
                                track_title = m.group(2).strip() or f"Track {track_num}"
                                gbs_tracks.append((track_num - 1, track_title))
                            else:
                                gbs_tracks.append((len(gbs_tracks), m3u_basename))
                except Exception:
                    pass
                gbs_tracks.sort(key=lambda x: x[0])
                for t_idx, t_title in gbs_tracks:
                    self.playlist.append({"file": self.audio_file, "title": t_title, "track_index": t_idx})
            else:
                existing_m3u = None
                primary_m3u_path = os.path.join(folder, f"{audio_basename}.m3u")
                if os.path.exists(primary_m3u_path):
                    existing_m3u = primary_m3u_path
                else:
                    try:
                        for f in os.listdir(folder):
                            if f.lower().endswith(".m3u"):
                                existing_m3u = os.path.join(folder, f)
                                break
                    except Exception:
                        pass

                target_m3u = existing_m3u or primary_m3u_path
                if os.path.exists(target_m3u):
                    try:
                        with open(target_m3u, "r", encoding="utf-8", errors="replace") as f:
                            line_counter = 0
                            for line in f:
                                line_str = line.strip()
                                if not line_str or line_str.startswith("#"):
                                    continue
                                match_idx = re.search(r'track_index=(\d+)', line_str)
                                if match_idx:
                                    t_idx = int(match_idx.group(1))
                                    self.playlist.append({"file": self.audio_file, "title": f"track {t_idx + 1}", "track_index": t_idx})
                                else:
                                    self.playlist.append({"file": self.audio_file, "title": f"track {line_counter + 1}", "track_index": line_counter})
                                line_counter += 1
                    except Exception:
                        pass

            if not self.playlist:
                self.playlist = [self.audio_file]
            self.current_index = 0
        else:
            try:
                files = sorted(os.listdir(folder))
                audio_exts = {
                    ".ay", ".gbs", ".gym", ".hes", ".kss", ".nsf", ".nsfe", ".sap", ".spc", ".vgm", ".vgz",
                    ".mp3", ".ogg", ".flac", ".wav", ".m4a", ".aac", ".opus", ".wma", ".ape", ".wv",
                    ".mod", ".s3m", ".xm", ".it", ".mka", ".tta", ".ac3", ".dts", ".aiff", ".au", ".ra", ".miniusf", ".usf"
                }
                for f in files:
                    full_p = os.path.join(folder, f)
                    if os.path.isfile(full_p) and os.path.splitext(f)[1].lower() in audio_exts:
                        self.playlist.append(full_p)
                if self.audio_file in self.playlist:
                    self.current_index = self.playlist.index(self.audio_file)
                else:
                    self.playlist = [self.audio_file]
                    self.current_index = 0
            except Exception:
                self.playlist = [self.audio_file]
                self.current_index = 0

    def generate_m3u_playlist(self):
        folder = os.path.dirname(self.audio_file)
        filename = os.path.basename(self.audio_file)
        m3u_path = os.path.join(folder, f"{os.path.splitext(filename)[0]}.m3u")
        try:
            with open(m3u_path, "w", encoding="utf-8") as f:
                f.write("#EXTM3U\n")
                for p in self.playlist:
                    if isinstance(p, str):
                        f.write(f"{os.path.basename(p)}\n")
        except Exception:
            pass

    def build_ui(self):
        filename = os.path.basename(self.audio_file)
        self.title(f"mpv Controller - {filename}")
        self.geometry("480x500")
        self.minsize(440, 420)
        self.transient(self.parent)

        padding = ttk.Frame(self, padding=12)
        padding.pack(fill="both", expand=True)

        self.title_label = ttk.Label(padding, text=f"Now Playing:\n{filename}", font=("Arial", 9, "bold"), wraplength=450, justify="center")
        self.title_label.pack(pady=(0, 6))

        info_frame = ttk.Frame(padding)
        info_frame.pack(fill="x", pady=(0, 4))
        ttk.Label(info_frame, textvariable=self.status_var, font=("Arial", 9)).pack(side="left", padx=5)
        ttk.Label(info_frame, textvariable=self.position_var, font=("Arial", 9)).pack(side="right", padx=5)

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
        ttk.Button(controls, text="-10s", width=6, command=lambda: self.command("seek", -10, "relative")).grid(row=0, column=1, padx=2)
        ttk.Button(controls, text="Play/Pause", width=10, command=self.toggle_play_pause).grid(row=0, column=2, padx=2)
        ttk.Button(controls, text="+10s", width=6, command=lambda: self.command("seek", 10, "relative")).grid(row=0, column=3, padx=2)
        ttk.Button(controls, text="Next >>|", width=8, command=self.next_track).grid(row=0, column=4, padx=2)

        btn_bottom = ttk.Frame(padding)
        btn_bottom.pack(pady=(6, 0), fill="x")

        ttk.Checkbutton(btn_bottom, variable=self.loop_chk_var, command=self.on_loop_settings_changed).pack(side="left", padx=(0, 2))
        ttk.Spinbox(btn_bottom, from_=1, to=99, width=3, textvariable=self.loop_count_var, command=self.on_loop_settings_changed).pack(side="left", padx=(0, 6))
        ttk.Button(btn_bottom, text="Stop", width=8, command=self.stop).pack(side="left", padx=2)
        ttk.Checkbutton(btn_bottom, text="Loop forever", variable=self.loop_forever_var, command=self.on_loop_settings_changed).pack(side="right", padx=(4, 0))
        ttk.Button(btn_bottom, text="Close", width=8, command=self.close).pack(side="right", padx=2)

    def on_loop_settings_changed(self):
        self.play_current_track()

    def on_volume_slide(self, val):
        vol_int = int(float(val))
        self.volume_var.set(vol_int)
        self.volume_label_var.set(f"Volume: {vol_int}%")
        self.command("set_property", "volume", float(vol_int))

    def populate_playlist_listbox(self):
        if not self.winfo_exists():
            return
        self.playlist_listbox.delete(0, tk.END)
        for p in self.playlist:
            if isinstance(p, dict):
                self.playlist_listbox.insert(tk.END, p.get("title", os.path.basename(p["file"])))
            else:
                self.playlist_listbox.insert(tk.END, os.path.basename(p))
        self.update_listbox_selection()

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

    def start_mpv(self):
        if self.mpv:
            try:
                self.mpv.terminate()
            except Exception:
                pass
        try:
            if os.path.exists(self.socket_path):
                os.unlink(self.socket_path)
        except FileNotFoundError:
            pass

        mpv_bin = "/bin/mpv" if os.path.exists("/bin/mpv") else (shutil.which("mpv") or "mpv")
        cmd = [
            mpv_bin,
            "--no-video",
            f"--input-ipc-server={self.socket_path}",
            f"--volume={self.volume_var.get()}",
        ]

        if self.loop_forever_var.get():
            cmd.append("--loop-file=inf")
        elif self.loop_chk_var.get():
            cmd.append(f"--loop-file={self.loop_count_var.get()}")
        else:
            cmd.append("--loop-file=1")

        if self.current_track_index is not None:
            cmd.append("--demuxer-lavf-format=libgme")
            cmd.append(f"--demuxer-lavf-o=track_index={self.current_track_index}")

        cmd.append(self.audio_file)

        try:
            self.mpv = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            messagebox.showerror("mpv Error", f"Could not launch mpv process:\n\n{e}")
            return

        for _ in range(200):
            if os.path.exists(self.socket_path):
                try:
                    self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                    self.sock.connect(self.socket_path)
                    self.sock.setblocking(False)
                    break
                except Exception:
                    if self.sock:
                        try:
                            self.sock.close()
                        except Exception:
                            pass
                        self.sock = None
            try:
                self.update_idletasks()
                self.after(15)
            except Exception:
                break

    def play_current_track(self):
        item = self.playlist[self.current_index]
        if isinstance(item, dict):
            f_path = item["file"]
            t_idx = item["track_index"]
            display_title = item["title"]
            cache_key = f"{os.path.basename(f_path)}::idx={t_idx}"
            target_file = f_path
            self.current_track_index = t_idx
        else:
            f_path = item
            ext = os.path.splitext(f_path)[1].lower()
            if ext in (".miniusf", ".usf"):
                lazyusf_bin = get_lazyusf_path()
                if lazyusf_bin and (shutil.which(lazyusf_bin) or os.path.exists(lazyusf_bin)):
                    folder = os.path.dirname(f_path)
                    filename = os.path.basename(f_path)
                    au_filepath = os.path.join(folder, filename + ".au")
                    try:
                        subprocess.run([lazyusf_bin, filename], cwd=folder, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    except Exception:
                        pass
                    if os.path.exists(au_filepath):
                        target_file = au_filepath
                    else:
                        target_file = f_path
                else:
                    target_file = f_path
            else:
                target_file = f_path

            cache_key = os.path.basename(target_file)
            self.current_track_index = None

        self.audio_file = target_file
        display_title = os.path.basename(self.audio_file)

        if cache_key in self.duration_cache:
            self.duration_var.set(f"Duration: {self.format_time(self.duration_cache[cache_key])}")
        else:
            self.duration_var.set("Duration: 00:00:00")

        if hasattr(self, "title_label") and self.winfo_exists():
            self.title_label.configure(text=f"Now Playing:\n{display_title}")
        self.title(f"mpv Controller - {display_title}")
        self.track_var.set(f"Track: {self.current_index + 1} / {len(self.playlist)}")
        self.start_mpv()
        self.status_var.set("Playing")
        self.update_listbox_selection()

    def toggle_play_pause(self):
        if self.status_var.get() == "Stopped":
            self.play_current_track()
        else:
            self.command("cycle", "pause")

    def prev_track(self):
        if len(self.playlist) > 1:
            self.current_index = (self.current_index - 1) % len(self.playlist)
            self.play_current_track()

    def next_track(self):
        if len(self.playlist) > 1:
            self.current_index = (self.current_index + 1) % len(self.playlist)
            self.play_current_track()

    def on_playlist_select(self, event):
        selection = self.playlist_listbox.curselection()
        if not selection:
            return
        idx = selection[0]
        if idx != self.current_index:
            self.current_index = idx
            self.play_current_track()

    def command(self, *args):
        if not self.sock:
            return
        message = {"command": list(args), "request_id": 1}
        try:
            self.sock.sendall((json.dumps(message) + "\n").encode())
        except (BrokenPipeError, OSError):
            self.sock = None

    def stop(self):
        if self.mpv:
            try:
                self.mpv.terminate()
            except Exception:
                pass
        self.status_var.set("Stopped")

    def poll_status(self):
        if not self.winfo_exists() or not self.sock:
            return
        try:
            data = self.sock.recv(8192).decode()
            for line in data.splitlines():
                try:
                    response = json.loads(line)
                    if response.get("request_id") != 1:
                        continue
                except Exception:
                    pass
        except BlockingIOError:
            pass
        except (ConnectionResetError, OSError):
            self.sock = None
            return

        self.request_property("time-pos")
        self.request_property("pause")
        self.request_property("volume")

        if self.winfo_exists():
            self.after(500, self.poll_status)

    def request_property(self, property_name):
        if not self.sock:
            return
        request = {"command": ["get_property", property_name], "request_id": property_name}
        try:
            self.sock.sendall((json.dumps(request) + "\n").encode())
            data = self.sock.recv(8192).decode()
            for line in data.splitlines():
                try:
                    response = json.loads(line)
                    rid = response.get("request_id")
                    val = response.get("data")
                    if rid == "time-pos" and val is not None and self.status_var.get() != "Stopped":
                        self.position_var.set(f"Position: {self.format_time(float(val))}")
                    elif rid == "pause" and val is not None and self.status_var.get() != "Stopped":
                        self.status_var.set("Paused" if val else "Playing")
                    elif rid == "volume" and val is not None:
                        vol_val = int(float(val))
                        if vol_val != self.volume_var.get():
                            self.volume_var.set(vol_val)
                            self.volume_label_var.set(f"Volume: {vol_val}%")
                            if hasattr(self, "volume_slider"):
                                self.volume_slider.set(vol_val)
                except Exception:
                    pass
        except (BlockingIOError, ConnectionResetError, OSError):
            pass

    def close(self):
        if self.mpv:
            try:
                self.mpv.terminate()
            except Exception:
                pass
        try:
            if os.path.exists(self.socket_path):
                os.unlink(self.socket_path)
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass


def start_vgmstream_installation():
    messagebox.showinfo("VGMstream Installation", "VGMstream installation helper:\nPlease build/install vgmstream123 and place it in your PATH or ~/.config/joshw/.")


def start_lazyusf_installation():
    install_dir = get_cache_directory()
    os.makedirs(install_dir, exist_ok=True)
    bin_path = os.path.join(install_dir, "lazyusf")

    progress_win = tk.Toplevel()
    progress_win.title("Installing lazyusf")
    progress_win.geometry("420x160")
    progress_win.resizable(False, False)
    progress_win.transient()
    progress_win.grab_set()

    status_var = tk.StringVar(value="Preparing to build lazyusf...")
    ttk.Label(progress_win, textvariable=status_var, wraplength=390, font=("Arial", 9, "bold")).pack(pady=20)
    p_bar = ttk.Progressbar(progress_win, orient="horizontal", mode="indeterminate", length=360)
    p_bar.pack(pady=5)
    p_bar.start(10)

    def build_worker():
        temp_dir = tempfile.mkdtemp()
        try:
            env = os.environ.copy()
            status_var.set("Cloning lazyusf repository...")
            git_res = subprocess.run(
                ["git", "clone", "https://github.com/derselbst/lazyusf.git", temp_dir],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env
            )
            if git_res.returncode != 0:
                raise RuntimeError(f"git clone failed:\n{git_res.stderr.strip()}")
            
            build_dir = os.path.join(temp_dir, "build")
            os.makedirs(build_dir, exist_ok=True)
            
            status_var.set("Running cmake...")
            cmake_res = subprocess.run(
                ["cmake", ".."],
                cwd=build_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env
            )
            if cmake_res.returncode != 0:
                raise RuntimeError(f"cmake failed:\n{cmake_res.stderr.strip()}")
            
            status_var.set("Compiling lazyusf...")
            make_res = subprocess.run(
                ["make"],
                cwd=build_dir, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env
            )
            if make_res.returncode != 0:
                raise RuntimeError(f"make failed:\n{make_res.stderr.strip()}")
            
            built_binary = None
            for root, dirs, files in os.walk(temp_dir):
                if "lazyusf" in files:
                    built_binary = os.path.join(root, "lazyusf")
                    break
            
            if built_binary and os.path.exists(built_binary):
                shutil.copy2(built_binary, bin_path)
                os.chmod(bin_path, os.stat(bin_path).st_mode | stat.S_IXUSR)
                progress_win.destroy()
                messagebox.showinfo("Success", "lazyusf was successfully built and installed!")
            else:
                raise RuntimeError("Build finished, but the 'lazyusf' binary could not be found.")
        except Exception as e:
            progress_win.destroy()
            messagebox.showerror(
                "Installation Error",
                f"Failed to build lazyusf:\n\n{e}\n\nMake sure 'build-essential', 'cmake', and 'git' are installed."
            )
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

    threading.Thread(target=build_worker, daemon=True).start()
