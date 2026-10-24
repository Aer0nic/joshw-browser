import os
import json
import socket
import shutil
import subprocess
import tempfile
import threading
import random
import tkinter as tk
from tkinter import ttk, messagebox, filedialog

from config import get_cache_directory, is_emulated_file

# Import shared helper functions from players.py (NO player windows imported here)
from players import (
    get_saved_player_volume,
    register_volume_player,
    set_player_volume,
    prepare_nsf_m3u,
    parse_m3u_playlist,
    InteractiveProgressBar,
    ExtractedFilesBrowserWindow,
    _active_volume_players
)

class MPVPlayerWindow(tk.Toplevel):
    def __init__(self, parent, audio_file, playlist=None, start_index=None):
        super().__init__(parent)
        self.parent = parent
        self.audio_file = audio_file
        self.mpv = None
        self.sock = None
        self.m3u_path = None
        self.socket_path = os.path.join(tempfile.gettempdir(), f"mpv-python-{os.getpid()}-{threading.get_ident()}.sock")
        self.is_scrubbing = False
        self.is_sliding_volume = False
        self.raw_duration_seconds = 180.0
        self.raw_position_seconds = 0.0

        is_emu = is_emulated_file(self.audio_file)
        self.loop_chk_var = tk.BooleanVar(value=is_emu)
        self.loop_count_var = tk.IntVar(value=1)
        self.loop_forever_var = tk.BooleanVar(value=False)

        self.playlist = []
        self.current_index = 0
        self.current_track_index = None
        self.track_repeat_count = 0
        self.end_of_track_handled = False
        self.load_playlist_files(playlist, start_index)

        saved_volume = get_saved_player_volume()
        self.volume_var = tk.IntVar(value=saved_volume)
        self.volume_label_var = tk.StringVar(value=f"Volume: {saved_volume}%")
        register_volume_player(self)
        self.position_var = tk.StringVar(value="Position: 00:00:00")
        self.duration_var = tk.StringVar(value="Duration: 00:00:00")
        self.status_var = tk.StringVar(value="Stopped")
        self.track_var = tk.StringVar(value=f"Track: 1 / {len(self.playlist)}")
        self.seek_pct_var = tk.StringVar(value="0%")

        self.build_ui()
        self.protocol("WM_DELETE_WINDOW", self.close)

        if self.playlist:
            self.play_current_track()
            self.poll_status()

    def random_track(self):
        if len(self.playlist) > 1:
            new_index = self.current_index
            while new_index == self.current_index:
                new_index = random.randint(0, len(self.playlist) - 1)
            self.current_index = new_index
            self.play_current_track()

    def on_loop_checkbox_changed(self):
        if self.loop_chk_var.get():
            self.loop_forever_var.set(False)
        self.on_loop_settings_changed()

    def on_loop_forever_changed(self):
        if self.loop_forever_var.get():
            self.loop_chk_var.set(False)
        self.on_loop_settings_changed()

    def on_loop_spinbox_changed(self):
        self.on_loop_settings_changed()

    def format_time(self, seconds):
        if seconds is None or seconds < 0:
            return "00:00:00"
        total_seconds = int(seconds)
        return f"{total_seconds // 3600:02d}:{(total_seconds % 3600) // 60:02d}:{total_seconds % 60:02d}"

    def load_playlist_files(self, provided_playlist=None, provided_index=None):
        folder = os.path.dirname(self.audio_file)
        abs_target = os.path.normpath(os.path.abspath(self.audio_file))
        ext = os.path.splitext(self.audio_file)[1].lower()

        if ext in (".nsf", ".nsfe"):
            self.m3u_path, self.playlist = prepare_nsf_m3u(self.audio_file)
            if not self.playlist:
                self.playlist = parse_m3u_playlist(self.m3u_path, self.audio_file)
            if not self.playlist:
                self.playlist = [{"file": self.audio_file, "title": "Track 1", "track_index": 0}]
            if provided_index is not None and 0 <= provided_index < len(self.playlist):
                self.current_index = provided_index
            else:
                self.current_index = 0
            return

        if provided_playlist:
            self.playlist = provided_playlist
            norm_playlist = [os.path.normpath(os.path.abspath(p)) for p in self.playlist]
            if abs_target in norm_playlist:
                self.current_index = norm_playlist.index(abs_target)
            elif provided_index is not None and 0 <= provided_index < len(self.playlist):
                self.current_index = provided_index
            else:
                self.current_index = 0
            return

        try:
            files = sorted(os.listdir(folder))
            audio_exts = {
                ".ay", ".gbs", ".gym", ".hes", ".kss", ".nsf", ".nsfe", ".sap", ".spc", ".vgm", ".vgz",
                ".mp3", ".ogg", ".flac", ".wav", ".m4a", ".aac", ".opus", ".wma", ".ape", ".wv",
                ".mod", ".s3m", ".xm", ".it", ".mka", ".tta", ".ac3", ".dts", ".aiff", ".au", ".ra", ".mid", ".midi"
            }
            for f in files:
                full_p = os.path.join(folder, f)
                if os.path.isfile(full_p) and os.path.splitext(f)[1].lower() in audio_exts:
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

    def build_ui(self):
        filename = os.path.basename(self.audio_file)
        self.title(f"mpv Controller - {filename}")
        self.geometry("480x580")
        self.minsize(440, 500)
        self.transient(self.parent)

        padding = ttk.Frame(self, padding=12)
        padding.pack(fill="both", expand=True)

        browse_frame = ttk.Frame(padding)
        browse_frame.pack(side="top", anchor="w", pady=(0, 6))

        browse_btn = tk.Button(browse_frame, text="📁", font=("Arial", 12), width=3, command=self.on_browse_clicked)
        browse_btn.pack(side="top", anchor="w")

        self.title_label = ttk.Label(padding, text=f"Now Playing:\n{filename}", font=("Arial", 9, "bold"), wraplength=250, justify="center")
        self.title_label.pack(pady=(0, 6))

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
            get_duration_func=lambda: self.raw_duration_seconds,
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
        self.volume_slider.bind("<ButtonPress-1>", lambda e: setattr(self, "is_sliding_volume", True))
        self.volume_slider.bind("<ButtonRelease-1>", lambda e: self.after(300, lambda: setattr(self, "is_sliding_volume", False)))

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

        ttk.Button(controls, text="Stop", width=8, command=self.stop).grid(row=0, column=0, padx=2)
        ttk.Button(controls, text="|<< Prev", width=8, command=self.prev_track).grid(row=0, column=1, padx=2)
        ttk.Button(controls, text="Play/Pause", width=10, command=self.toggle_play_pause).grid(row=0, column=2, padx=2)
        ttk.Button(controls, text="Next >>|", width=8, command=self.next_track).grid(row=0, column=3, padx=2)
        ttk.Button(controls, text="Close", width=8, command=self.close).grid(row=0, column=4, padx=2)

        btn_bottom = ttk.Frame(padding)
        btn_bottom.pack(pady=(6, 0), fill="x")

        ttk.Checkbutton(
            btn_bottom,
            variable=self.loop_chk_var,
            command=self.on_loop_checkbox_changed
        ).grid(row=0, column=0, padx=(0, 2), sticky="w")

        ttk.Spinbox(
            btn_bottom,
            from_=1,
            to=99,
            width=3,
            textvariable=self.loop_count_var,
            command=self.on_loop_spinbox_changed
        ).grid(row=0, column=1, padx=(0, 6), sticky="w")

        ttk.Button(
            btn_bottom,
            text="Random",
            width=12,
            command=self.random_track
        ).grid(row=0, column=2, padx=2)

        ttk.Checkbutton(
            btn_bottom,
            text="Loop forever",
            variable=self.loop_forever_var,
            command=self.on_loop_forever_changed
        ).grid(row=0, column=3, padx=2, sticky="e")

        btn_bottom.grid_columnconfigure(2, weight=1)
    
    def seek_to_position(self, target_time):
        self.command("seek", float(target_time), "absolute")

    def on_browse_clicked(self):
        folder = os.path.dirname(self.audio_file)
        if os.path.isdir(folder):
            ExtractedFilesBrowserWindow(self, folder)
        else:
            self.open_file_picker()

    def open_file_picker(self):
        initial_dir = os.path.dirname(self.audio_file) if os.path.isdir(os.path.dirname(self.audio_file)) else os.path.expanduser("~")
        file_path = filedialog.askopenfilename(
            title="Select Music File",
            initialdir=initial_dir,
            filetypes=[("All Files", "*.*")]
        )
        if file_path:
            self.play_audio_file(file_path)

    def play_audio_file(self, audio_file, playlist=None, start_index=None):
        ext = os.path.splitext(audio_file)[1].lower()
        if ext in (".nsf", ".nsfe"):
            self.stop()
            self.audio_file = audio_file
            self.load_playlist_files()
            self.populate_playlist_listbox()
            self.current_index = 0
            self.play_current_track()
        else:
            # Delegate all other file formats to the parent router/browser cleanly
            handler = self.parent
            while handler and not hasattr(handler, "play_audio_file"):
                handler = getattr(handler, "parent", None)
            if handler and hasattr(handler, "play_audio_file"):
                try:
                    _active_volume_players.discard(self)
                except Exception:
                    pass
                self.stop()
                handler.play_audio_file(audio_file, playlist=playlist, start_index=start_index)
                self.close()

    def on_loop_settings_changed(self):
        self.play_current_track()

    def on_volume_slide(self, val):
        set_player_volume(float(val), source_player=self)

    def apply_live_volume(self):
        self.command("set_property", "volume", float(self.volume_var.get()))

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
                if isinstance(self.mpv, subprocess.Popen):
                    if self.mpv.stdin:
                        try:
                            self.mpv.stdin.write("quit\n")
                            self.mpv.stdin.flush()
                        except Exception:
                            pass
                    self.mpv.kill()
                    self.mpv.wait(timeout=1)
            except Exception:
                pass
            self.mpv = None

        if hasattr(self, "sock") and self.sock:
            try:
                self.sock.close()
            except Exception:
                pass
            self.sock = None

        try:
            if os.path.exists(self.socket_path):
                os.unlink(self.socket_path)
        except Exception:
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
        self.track_repeat_count = 0
        self.end_of_track_handled = False
        item = self.playlist[self.current_index]
        if isinstance(item, dict):
            target_file = item["file"]
            self.current_track_index = item["track_index"]
            display_title = item["title"]
        else:
            target_file = item
            self.current_track_index = None
            display_title = os.path.basename(target_file)

        self.audio_file = target_file
        self.raw_duration_seconds = 180.0
        self.duration_var.set("Duration: 00:00:00")

        if hasattr(self, "title_label") and self.winfo_exists():
            self.title_label.configure(text=f"Now Playing:\n{display_title}")
        
        # Updates window title dynamically
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

    def advance_after_end(self):
        if self.status_var.get() != "Playing":
            return
        next_index = get_next_track_index(self.current_index, len(self.playlist), False)
        if next_index is None:
            self.stop()
            return
        self.current_index = next_index
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
        if hasattr(self, "mpv") and self.mpv:
            try:
                if isinstance(self.mpv, subprocess.Popen):
                    if self.mpv.stdin:
                        try:
                            self.mpv.stdin.write("quit\n")
                            self.mpv.stdin.flush()
                        except Exception:
                            pass
                    self.mpv.kill()
                    self.mpv.wait(timeout=1)
            except Exception:
                pass
            self.mpv = None
        self.status_var.set("Stopped")

    def poll_status(self):
        if not self.winfo_exists():
            return
        
        if not self.sock:
            if self.winfo_exists():
                self.after(500, self.poll_status)
            return
        
        self.request_property("time-pos")
        self.request_property("duration")
        self.request_property("pause")
        self.request_property("eof-reached")
        
        if not self.is_scrubbing and not self.is_sliding_volume:
            self.request_property("volume")

        if self.winfo_exists():
            self.after(500, self.poll_status)

    def request_property(self, property_name):
        if not self.sock:
            return
        request = {"command": ["get_property", property_name], "request_id": property_name}
        try:
            self.sock.settimeout(0.1)
            self.sock.sendall((json.dumps(request) + "\n").encode())
            data = self.sock.recv(8192).decode()
            for line in data.splitlines():
                try:
                    response = json.loads(line)
                    rid = response.get("request_id")
                    val = response.get("data")
                    if rid == "time-pos" and val is not None and self.status_var.get() != "Stopped":
                        self.raw_position_seconds = float(val)
                        self.position_var.set(f"Position: {self.format_time(self.raw_position_seconds)}")
                        if self.raw_duration_seconds > 0 and not self.is_scrubbing:
                            pct = int((self.raw_position_seconds / self.raw_duration_seconds) * 100)
                            self.seek_pct_var.set(f"{pct}%")
                        if not self.is_scrubbing:
                            self.progress_bar_widget.update_progress(self.raw_position_seconds, self.raw_duration_seconds)
                    elif rid == "duration" and val is not None:
                        self.raw_duration_seconds = float(val)
                        self.duration_var.set(f"Duration: {self.format_time(self.raw_duration_seconds)}")
                    elif rid == "pause" and val is not None and self.status_var.get() != "Stopped":
                        self.status_var.set("Paused" if val else "Playing")
                    elif rid == "eof-reached" and val is True and self.status_var.get() == "Playing":
                        if not self.loop_forever_var.get() and not self.end_of_track_handled:
                            self.end_of_track_handled = True
                            self.after(0, self.advance_after_end)
                    elif rid == "volume" and val is not None and not self.is_sliding_volume:
                        vol_val = int(float(val))
                        if vol_val != self.volume_var.get():
                            self.volume_var.set(vol_val)
                            self.volume_label_var.set(f"Volume: {vol_val}%")
                            if hasattr(self, "volume_slider"):
                                self.volume_slider.set(vol_val)
                except Exception:
                    pass
        except (socket.timeout, BlockingIOError, ConnectionResetError, OSError):
            pass

    def close(self, event=None):
        try:
            _active_volume_players.discard(self)
        except Exception:
            pass
        self.stop()
        try:
            if os.path.exists(self.socket_path):
                os.unlink(self.socket_path)
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass