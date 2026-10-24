import os
import re
import signal
import subprocess
import threading
import time
import random
import array
import tkinter as tk
from tkinter import ttk, messagebox
from players import (
    get_saved_player_volume,
    register_volume_player,
    set_player_volume,
    InteractiveProgressBar,
    ExtractedFilesBrowserWindow,
    get_playgsf_path,
    get_next_track_index
)

class GSFPlayerWindow(tk.Toplevel):
    def __init__(self, parent, audio_file, playlist=None, start_index=None):
        super().__init__(parent)
        self.parent = parent
        self.audio_file = audio_file
        self.playlist = playlist or [audio_file]
        self.current_index = start_index if start_index is not None else 0
        self.gsf_proc = None
        self.last_displayed_text = ""
        self.current_position_seconds = 0.0
        self.total_duration_seconds = 180.0
        self.is_scrubbing = False
        self.is_paused = False

        self.loop_forever_var = tk.BooleanVar(value=False)
        self.loop_enabled_var = tk.BooleanVar(value=False)
        self.loop_count_var = tk.IntVar(value=1)
        self.loops_completed = 0

        self.status_var = tk.StringVar(value="Playing")
        self.track_var = tk.StringVar()
        self.position_var = tk.StringVar(value="Position: 00:00:00")
        self.duration_var = tk.StringVar(value="Duration: 00:00:00")
        self.seek_pct_var = tk.StringVar(value="0%")
        
        saved_volume = get_saved_player_volume()
        self.volume_var = tk.IntVar(value=saved_volume)
        self.volume_label_var = tk.StringVar(value=f"Volume: {saved_volume}%")
        register_volume_player(self)

        self.load_playlist_files()
        self.build_ui()
        self.protocol("WM_DELETE_WINDOW", self.close)
        
        self.start_playgsf()
        self.start_timer_thread()

    def format_time(self, seconds):
        if seconds is None or seconds < 0:
            return "00:00:00"
        s = int(seconds)
        return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"

    def load_playlist_files(self):
        folder = os.path.dirname(self.audio_file)
        abs_target = os.path.normpath(os.path.abspath(self.audio_file))
        self.playlist = []
        try:
            for f in sorted(os.listdir(folder)):
                if f.lower().endswith((".gsf", ".minigsf")):
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
        self.title(f"playgsf Controller - {filename}")
        self.geometry("480x640")
        self.minsize(440, 520)
        self.transient(self.parent)

        padding = ttk.Frame(self, padding=12)
        padding.pack(fill="both", expand=True)

        browse_frame = ttk.Frame(padding)
        browse_frame.pack(side="top", anchor="w", pady=(0, 6))

        browse_btn = tk.Button(browse_frame, text="📁", font=("Arial", 12), width=3, command=self.on_browse_clicked)
        browse_btn.pack(side="top", anchor="w")

        self.title_label = ttk.Label(padding, text=f"Now Playing (GSF):\n{filename}", font=("Arial", 9, "bold"), justify="center", wraplength=440)
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
            get_duration_func=lambda: self.total_duration_seconds,
            seek_func=self.seek_to_position,
            seek_start_func=lambda: setattr(self, "is_scrubbing", True),
            seek_release_func=lambda: setattr(self, "is_scrubbing", False)
        )
        self.progress_bar_widget.frame.pack(fill="x", expand=True)

        meta_frame = ttk.Frame(padding)
        meta_frame.pack(fill="x", pady=(0, 8))
        self.track_label = ttk.Label(meta_frame, textvariable=self.track_var, font=("Arial", 9, "bold"))
        self.track_label.pack(side="left", padx=5)
        self.update_track_label()
        
        self.duration_label = ttk.Label(meta_frame, textvariable=self.duration_var, font=("Arial", 9))
        self.duration_label.pack(side="right", padx=5)

        vol_frame = ttk.Frame(padding)
        vol_frame.pack(fill="x", pady=(0, 8))
        ttk.Label(vol_frame, textvariable=self.volume_label_var, font=("Arial", 9)).pack(side="left", padx=5)
        self.volume_slider = ttk.Scale(vol_frame, from_=0, to=150, orient="horizontal", command=self.on_volume_slide)
        self.volume_slider.set(self.volume_var.get())
        self.volume_slider.pack(side="right", fill="x", expand=True, padx=5)

        term_container = ttk.LabelFrame(padding, text="playgsf Track Status", padding=4)
        term_container.pack(fill="both", expand=True, pady=(4, 8))

        log_frame = ttk.Frame(term_container)
        log_frame.pack(fill="both", expand=True)

        self.log_text = tk.Text(log_frame, wrap="word", bg="black", fg="yellow", font=("Monospace", 9), state="disabled", height=8)
        self.log_text.pack(side="left", fill="both", expand=True)
        
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scrollbar.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=scrollbar.set)

        list_frame = ttk.LabelFrame(padding, text="Playlist Files", padding=6)
        list_frame.pack(fill="x", pady=(0, 8))

        self.playlist_listbox = tk.Listbox(list_frame, font=("Arial", 9), selectmode="single", exportselection=False, height=4)
        self.playlist_listbox.pack(side="left", fill="both", expand=True)
        list_scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.playlist_listbox.yview)
        list_scrollbar.pack(side="right", fill="y")
        self.playlist_listbox.configure(yscrollcommand=list_scrollbar.set)
        self.playlist_listbox.bind("<<ListboxSelect>>", self.on_listbox_select)
        self.populate_playlist_listbox()

        # Main Control Buttons Row
        controls = ttk.Frame(padding)
        controls.pack(pady=4)

        ttk.Button(controls, text="Stop", width=8, command=self.stop).grid(row=0, column=0, padx=2)
        ttk.Button(controls, text="|<< Prev", width=8, command=self.prev_track).grid(row=0, column=1, padx=2)
        ttk.Button(controls, text="Play/Pause", width=10, command=self.toggle_play_pause).grid(row=0, column=2, padx=2)
        ttk.Button(controls, text="Next >>|", width=8, command=self.next_track).grid(row=0, column=3, padx=2)
        ttk.Button(controls, text="Close", width=8, command=self.close).grid(row=0, column=4, padx=2)

        # Bottom Sub-Row: Loop settings on left, Random in center, Loop forever on right
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
        ttk.Checkbutton(right_loop_frame, text="Loop forever", variable=self.loop_forever_var).pack(side="right")

    def on_volume_slide(self, val):
        # Calls set_player_volume which updates settings.json and syncs across all players
        set_player_volume(float(val), source_player=self)

    def apply_live_volume(self):
        # Called instantly when volume changes locally or globally — zero track interruption!
        pass

    def start_timer_thread(self):
        def timer_loop():
            while self.winfo_exists():
                if self.status_var.get() == "Playing" and not self.is_scrubbing:
                    self.current_position_seconds += 1.0
                    self.position_var.set(f"Position: {self.format_time(self.current_position_seconds)}")
                    if self.total_duration_seconds > 0:
                        pct = int((self.current_position_seconds / self.total_duration_seconds) * 100)
                        self.seek_pct_var.set(f"{pct}%")
                    if self.winfo_exists():
                        self.after(0, lambda: self.progress_bar_widget.update_progress(self.current_position_seconds, self.total_duration_seconds))
                time.sleep(1.0)
        threading.Thread(target=timer_loop, daemon=True).start()

    def seek_to_position(self, target_time):
        target_time = max(0.0, min(target_time, self.total_duration_seconds))
        self.current_position_seconds = target_time
        self.position_var.set(f"Position: {self.format_time(self.current_position_seconds)}")
        if self.total_duration_seconds > 0:
            pct = int((self.current_position_seconds / self.total_duration_seconds) * 100)
            self.seek_pct_var.set(f"{pct}%")
        self.progress_bar_widget.update_progress(self.current_position_seconds, self.total_duration_seconds)
        self.start_playgsf(preserve_time=True)

    def on_browse_clicked(self):
        ExtractedFilesBrowserWindow(self, os.path.dirname(self.audio_file))

    def populate_playlist_listbox(self):
        if not self.winfo_exists():
            return
        self.playlist_listbox.delete(0, tk.END)
        for p in self.playlist:
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

    def on_listbox_select(self, event):
        selection = self.playlist_listbox.curselection()
        if not selection:
            return
        idx = selection[0]
        if idx != self.current_index:
            self.current_index = idx
            self.loops_completed = 0
            self.start_playgsf()

    def update_track_label(self):
        self.track_var.set(f"Track: {self.current_index + 1} / {len(self.playlist)}")

    def update_textbox(self, text):
        if not self.winfo_exists() or text == self.last_displayed_text:
            return
        self.last_displayed_text = text
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.insert(tk.END, text)
        self.log_text.configure(state="disabled")

    def stop_current_playback(self):
        if getattr(self, "gsf_proc", None):
            try:
                if isinstance(self.gsf_proc, subprocess.Popen):
                    self.gsf_proc.terminate()
                    self.gsf_proc.wait(timeout=1)
            except Exception:
                try:
                    self.gsf_proc.kill()
                except Exception:
                    pass
            self.gsf_proc = None

    def start_playgsf(self, preserve_time=False):
        self.stop_current_playback()
        if not preserve_time:
            self.current_position_seconds = 0.0

        playgsf_bin = get_playgsf_path()
        self.audio_file = self.playlist[self.current_index]

        env = os.environ.copy()
        env["SDL_VIDEODRIVER"] = "dummy"

        cmd_args = [playgsf_bin]
        if self.current_position_seconds > 0:
            cmd_args.append(f"-b{int(round(self.current_position_seconds * 1000))}")
        cmd_args.append(self.audio_file)

        try:
            self.gsf_proc = subprocess.Popen(...)
            self.is_paused = False
            self.status_var.set("Playing")
            self.update_track_label()
            self.update_listbox_selection()
            
            filename = os.path.basename(self.audio_file)
            
            # Update window title to the currently playing GSF track
            self.title(f"playgsf Controller - {filename}")
            if hasattr(self, "title_label") and self.winfo_exists():
                self.title_label.configure(text=f"Now Playing:\n{filename}")
                
            threading.Thread(target=self.read_gsf_output, args=(self.gsf_proc,), daemon=True).start()
        except Exception as e:
            messagebox.showerror("playgsf Error", f"Could not launch playgsf:\n\n{e}")

    def read_gsf_output(self, proc):
        if not proc or not proc.stdout:
            return
        
        info = {
            "Filename": os.path.basename(self.audio_file),
            "Channels": "UNKNOWN", "Sample rate": "UNKNOWN", "Title": "UNKNOWN",
            "Artist": "UNKNOWN", "Game": "UNKNOWN", "Year": "UNKNOWN",
            "Copyright": "UNKNOWN", "GSF By": "UNKNOWN", "Tagger": "UNKNOWN",
            "Fade": "UNKNOWN", "Length": "UNKNOWN", "Time": "00:00", "GBA Cpu": "UNKNOWN"
        }

        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

        try:
            while proc.poll() is None:
                line = proc.stdout.readline()
                if not line:
                    break
                clean_line = ansi_escape.sub('', line)
                line_str = clean_line.strip().replace(" [0m", "")
                if not line_str:
                    continue
                
                if ":" in line_str:
                    parts = line_str.split(":", 1)
                    key, val = parts[0].strip().lower(), parts[1].strip()
                    if "length" in key:
                        info["Length"] = val
                        try:
                            sub_parts = val.split(":")
                            if len(sub_parts) == 2:
                                self.total_duration_seconds = int(sub_parts[0]) * 60 + float(sub_parts[1])
                                if self.winfo_exists():
                                    self.after(0, lambda: self.duration_var.set(f"Duration: {self.format_time(self.total_duration_seconds)}"))
                        except Exception:
                            pass
                    elif "title" in key: info["Title"] = val
                    elif "artist" in key: info["Artist"] = val
                    elif "game" in key: info["Game"] = val

                formatted_text = f"Filename: {info['Filename']}\nTitle: {info['Title']}\nArtist: {info['Artist']}\nLength: {info['Length']}"
                if self.winfo_exists():
                    self.after(0, lambda t=formatted_text: self.update_textbox(t))
        except Exception:
            pass

        return_code = proc.wait()
        if self.winfo_exists() and self.gsf_proc == proc:
            if return_code == 0 and self.status_var.get() == "Playing":
                should_loop = False
                if self.loop_forever_var.get():
                    should_loop = True
                elif self.loop_enabled_var.get():
                    try:
                        max_loops = int(self.loop_count_var.get())
                    except Exception:
                        max_loops = 1
                    if self.loops_completed < max_loops - 1:
                        self.loops_completed += 1
                        should_loop = True
                    else:
                        self.loops_completed = 0

                if should_loop:
                    self.after(0, lambda: self.start_playgsf(preserve_time=False))
                else:
                    next_index = get_next_track_index(self.current_index, len(self.playlist), False)
                    if next_index is None:
                        self.gsf_proc = None
                        self.status_var.set("Stopped")
                    else:
                        self.current_index = next_index
                        self.loops_completed = 0
                        self.after(0, lambda: self.start_playgsf(preserve_time=False))

    def toggle_play_pause(self):
        if not self.gsf_proc or self.status_var.get() == "Stopped":
            self.start_playgsf()
            return
        try:
            if self.is_paused:
                os.kill(self.gsf_proc.pid, signal.SIGCONT)
                self.is_paused = False
                self.status_var.set("Playing")
            else:
                os.kill(self.gsf_proc.pid, signal.SIGSTOP)
                self.is_paused = True
                self.status_var.set("Paused")
        except Exception:
            pass

    def prev_track(self):
        if len(self.playlist) > 1:
            self.current_index = (self.current_index - 1) % len(self.playlist)
            self.loops_completed = 0
            self.start_playgsf()

    def next_track(self):
        if len(self.playlist) > 1:
            self.current_index = (self.current_index + 1) % len(self.playlist)
            self.loops_completed = 0
            self.start_playgsf()

    def play_random_track(self):
        if not self.playlist:
            return
        if len(self.playlist) == 1:
            self.loops_completed = 0
            self.start_playgsf()
            return
        new_index = self.current_index
        while new_index == self.current_index:
            new_index = random.randint(0, len(self.playlist) - 1)
        self.current_index = new_index
        self.loops_completed = 0
        self.start_playgsf()

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