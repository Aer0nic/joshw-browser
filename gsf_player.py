import os
import re
import signal
import subprocess
import threading
import time
import random
import array
import tkinter as tk
import ttkbootstrap as tb
from tkinter import ttk, messagebox
from config import resource_path
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

        try:
            icon_file = resource_path("assets/icons/player_icon.png")
            if icon_file.exists():
                self.iconphoto(False, tk.PhotoImage(file=str(icon_file)))
        except Exception:
            pass

        self.protocol("WM_DELETE_WINDOW", self.close)
        
        self.start_playgsf()
        self.start_timer_thread()

    def format_time(self, seconds):
        if seconds is None or seconds < 0:
            return "00:00:00"
        s = int(seconds)
        return f"{s // 3600:02d}:{(s % 3600) // 60:02d}:{s % 60:02d}"

    def parse_psf_tags(self, file_path):
        """Reads TAGpsf metadata block directly from .gsf/.minigsf files."""
        tags = {}
        try:
            if not os.path.exists(file_path):
                return tags
            with open(file_path, "rb") as f:
                content = f.read()
            tag_pos = content.rfind(b"TAGpsf")
            if tag_pos != -1:
                tag_bytes = content[tag_pos + 6:]
                tag_text = tag_bytes.decode("utf-8", errors="ignore")
                for line in tag_text.splitlines():
                    line = line.strip()
                    if "=" in line:
                        k, v = line.split("=", 1)
                        tags[k.strip().lower()] = v.strip()
        except Exception:
            pass
        return tags

    def parse_time_string(self, val):
        """Converts MM:SS, HH:MM:SS, ms, or raw seconds strings into floating point seconds."""
        if not val:
            return None
        try:
            val_str = str(val).strip()
            # Extract raw string value prior to parenthesis annotations
            clean_str = val_str.split("(")[0].split("+")[0].strip().replace(",", ".")
            parts = clean_str.split(":")
            if len(parts) == 3:
                return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
            elif len(parts) == 2:
                return float(parts[0]) * 60 + float(parts[1])
            elif len(parts) == 1:
                secs = float(parts[0])
                if secs > 10000:
                    secs /= 1000.0
                return secs
        except Exception:
            pass
        return None

    def load_track_metadata(self, file_path):
        """Pre-fetches PSF tags and sets precise track duration."""
        tags = self.parse_psf_tags(file_path)
        if "_lib" in tags:
            lib_file = os.path.join(os.path.dirname(file_path), tags["_lib"])
            lib_tags = self.parse_psf_tags(lib_file)
            for k, v in lib_tags.items():
                if k not in tags:
                    tags[k] = v

        filename = os.path.basename(file_path)
        title = tags.get("title", "UNKNOWN")
        artist = tags.get("artist", "UNKNOWN")
        game = tags.get("game", "UNKNOWN")
        year = tags.get("year", "UNKNOWN")
        copyright_tag = tags.get("copyright", "UNKNOWN")
        gsfby = tags.get("gsfby", "UNKNOWN")
        tagger = tags.get("tagger", "UNKNOWN")
        comment = tags.get("comment", "UNKNOWN")
        length_str = tags.get("length") or tags.get("_length") or "UNKNOWN"
        fade_str = tags.get("fade") or tags.get("_fade") or ""

        raw_base_sec = self.parse_time_string(length_str) or 180.0
        fade_sec = self.parse_time_string(fade_str) or 0.0

        if self.loop_forever_var.get():
            self.total_duration_seconds = 0.0
        else:
            try:
                loop_count = int(self.loop_count_var.get())
            except Exception:
                loop_count = 1
            multiplier = (1 + loop_count) if self.loop_enabled_var.get() else 1
            self.total_duration_seconds = (raw_base_sec * multiplier) + fade_sec

        if self.winfo_exists():
            if self.loop_forever_var.get():
                self.duration_var.set("Duration: forever")
            else:
                self.duration_var.set(f"Duration: {self.format_time(self.total_duration_seconds)}")
            
            disp_length = f"{length_str} (+{fade_sec:.1f}s fade)" if fade_sec > 0 else length_str
            formatted_text = (
                f"Filename: {filename}\n"
                f"Title: {title}\n"
                f"Artist: {artist}\n"
                f"Game: {game}\n"
                f"Year: {year}\n"
                f"Copyright: {copyright_tag}\n"
                f"GSF By: {gsfby}\n"
                f"Tagger: {tagger}\n"
                f"Comment: {comment}\n"
                f"Length: {disp_length}"
            )
            self.update_textbox(formatted_text)

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
        self.minsize(520, 520)
        self.transient(self.parent)

        padding = tb.Frame(self, padding=12)
        padding.pack(fill="both", expand=True)

        browse_frame = tb.Frame(padding)
        browse_frame.pack(side="top", anchor="w", pady=(0, 6))

        browse_btn = tk.Button(browse_frame, text="📁", font=("Arial", 12), width=3, command=self.on_browse_clicked)
        browse_btn.pack(side="top", anchor="w")

        self.title_label = tb.Label(padding, text=f"Now Playing (GSF):\n{filename}", font=("Arial", 9, "bold"), justify="center", wraplength=440)
        self.title_label.pack(pady=(0, 6))
        
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
        self.track_label = tb.Label(meta_frame, textvariable=self.track_var, font=("Arial", 9, "bold"))
        self.track_label.pack(side="left", padx=5)
        self.update_track_label()
        
        self.duration_label = tb.Label(meta_frame, textvariable=self.duration_var, font=("Arial", 9))
        self.duration_label.pack(side="right", padx=5)

        vol_frame = tb.Frame(padding)
        vol_frame.pack(fill="x", pady=(0, 8))
        tb.Label(vol_frame, textvariable=self.volume_label_var, font=("Arial", 9)).pack(side="left", padx=5)
        self.volume_slider = tb.Scale(vol_frame, from_=0, to=100, orient="horizontal", command=self.on_volume_slide)
        self.volume_slider.set(self.volume_var.get())
        self.volume_slider.pack(side="right", fill="x", expand=True, padx=5)

        term_container = tb.LabelFrame(padding, text="playgsf Track Status", padding=4)
        term_container.pack(fill="both", expand=True, pady=(4, 8))

        log_frame = tb.Frame(term_container)
        log_frame.pack(fill="both", expand=True)

        self.log_text = tk.Text(log_frame, wrap="word", bg="black", fg="yellow", font=("Monospace", 9), state="disabled", height=8)
        self.log_text.pack(side="left", fill="both", expand=True)
        
        scrollbar = tb.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scrollbar.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=scrollbar.set)

        list_frame = tb.LabelFrame(padding, text="Playlist Files", padding=6)
        list_frame.pack(fill="x", pady=(0, 8))

        self.playlist_listbox = tb.Listbox(list_frame, font=("Arial", 9), selectmode="single", exportselection=False, height=4)
        self.playlist_listbox.pack(side="left", fill="both", expand=True)
        list_scrollbar = tb.Scrollbar(list_frame, orient="vertical", command=self.playlist_listbox.yview)
        list_scrollbar.pack(side="right", fill="y")
        self.playlist_listbox.configure(yscrollcommand=list_scrollbar.set)
        self.playlist_listbox.bind("<<ListboxSelect>>", self.on_listbox_select)
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
        tb.Checkbutton(left_loop_frame, variable=self.loop_enabled_var, command=self.on_loop_controls_changed).pack(side="left", padx=(0, 2))
        tb.Spinbox(left_loop_frame, from_=1, to=99, width=3, textvariable=self.loop_count_var, command=self.on_loop_controls_changed).pack(side="left")

        center_rand_frame = tb.Frame(bottom_sub_frame)
        center_rand_frame.pack(side="left", expand=True)
        tb.Button(center_rand_frame, text="Random", width=8, command=self.play_random_track).pack()

        right_loop_frame = tb.Frame(bottom_sub_frame)
        right_loop_frame.pack(side="right")
        tb.Checkbutton(right_loop_frame, text="Loop forever", variable=self.loop_forever_var, command=self.on_loop_forever_toggled).pack(side="right")

    def on_loop_controls_changed(self):
        if self.loop_enabled_var.get() and self.loop_forever_var.get():
            self.loop_forever_var.set(False)
        self.start_playgsf(preserve_time=False)

    def on_loop_forever_toggled(self):
        if self.loop_forever_var.get() and self.loop_enabled_var.get():
            self.loop_enabled_var.set(False)
        self.start_playgsf(preserve_time=False)

    def on_volume_slide(self, val):
        set_player_volume(float(val), source_player=self)

    def apply_live_volume(self):
        """Applies volume dynamically to playgsf audio sink input via pactl."""
        vol = self.volume_var.get()
        self.volume_label_var.set(f"Volume: {vol}%")
        if getattr(self, "gsf_proc", None) and self.gsf_proc.poll() is None:
            pid = self.gsf_proc.pid
            threading.Thread(target=self._set_process_volume, args=(pid, vol), daemon=True).start()

    def _set_process_volume(self, pid, volume_pct):
        vol_str = f"{int(volume_pct)}%"
        try:
            out = subprocess.check_output(["pactl", "list", "sink-inputs"], text=True, stderr=subprocess.DEVNULL)
            current_input = None
            for line in out.splitlines():
                line_str = line.strip()
                if line_str.startswith("Sink Input #"):
                    current_input = line_str.split("#")[1]
                elif "application.process.id =" in line_str and current_input:
                    proc_id = line_str.split("=")[1].strip().strip('"')
                    if proc_id == str(pid):
                        subprocess.run(["pactl", "set-sink-input-volume", current_input, vol_str], stderr=subprocess.DEVNULL)
                        return
        except Exception:
            pass

        try:
            subprocess.run(["amixer", "-q", "sset", "Master", vol_str], stderr=subprocess.DEVNULL)
        except Exception:
            pass

    def start_timer_thread(self):
        def timer_loop():
            while self.winfo_exists():
                if self.status_var.get() == "Playing" and not self.is_scrubbing:
                    self.current_position_seconds += 1.0
                    self.position_var.set(f"Position: {self.format_time(self.current_position_seconds)}")
                    
                    if self.loop_forever_var.get():
                        self.seek_pct_var.set("0%")
                        if self.winfo_exists():
                            self.after(0, lambda: self.progress_bar_widget.update_progress(self.current_position_seconds, 0.0))
                    elif self.total_duration_seconds > 0:
                        pct = int((self.current_position_seconds / self.total_duration_seconds) * 100)
                        self.seek_pct_var.set(f"{pct}%")
                        if self.winfo_exists():
                            self.after(0, lambda: self.progress_bar_widget.update_progress(self.current_position_seconds, self.total_duration_seconds))
                        if self.current_position_seconds >= self.total_duration_seconds:
                            if self.winfo_exists():
                                self.after(0, self.handle_track_completion)
                time.sleep(1.0)
        threading.Thread(target=timer_loop, daemon=True).start()

    def handle_track_completion(self):
        if self.status_var.get() != "Playing":
            return
        if self.loop_forever_var.get():
            return
            
        next_index = get_next_track_index(self.current_index, len(self.playlist), False)
        if next_index is None:
            self.stop_current_playback()
            self.status_var.set("Stopped")
        else:
            self.current_index = next_index
            self.loops_completed = 0
            self.start_playgsf(preserve_time=False)

    def seek_to_position(self, target_time):
        if self.loop_forever_var.get():
            return
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

        playgsf_bin = str(get_playgsf_path())
        self.audio_file = self.playlist[self.current_index]

        # Read PSF tags and compute precise base + fade duration
        self.load_track_metadata(self.audio_file)

        env = os.environ.copy()
        env["SDL_VIDEODRIVER"] = "dummy"

        cmd_args = [playgsf_bin]
        
        if self.loop_forever_var.get():
            cmd_args.append("-e")
        elif self.total_duration_seconds > 0:
            cmd_args.extend(["-t", str(int(round(self.total_duration_seconds * 1000)))])

        if self.current_position_seconds > 0:
            cmd_args.append(f"-b{int(round(self.current_position_seconds * 1000))}")

        cmd_args.append(self.audio_file)

        try:
            self.gsf_proc = subprocess.Popen(
                cmd_args,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            self.is_paused = False
            self.status_var.set("Playing")
            self.update_track_label()
            self.update_listbox_selection()
            
            filename = os.path.basename(self.audio_file)
            
            self.title(f"playgsf Controller - {filename}")
            if hasattr(self, "title_label") and self.winfo_exists():
                self.title_label.configure(text=f"Now Playing:\n{filename}")
                
            threading.Thread(target=self.read_gsf_output, args=(self.gsf_proc,), daemon=True).start()
            self.after(300, self.apply_live_volume)
        except Exception as e:
            messagebox.showerror("playgsf Error", f"Could not launch playgsf:\n\n{e}")

    def read_gsf_output(self, proc):
        if not proc or not proc.stdout:
            return
        
        ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')

        info = {
            "Filename": os.path.basename(self.audio_file),
            "Title": "UNKNOWN",
            "Artist": "UNKNOWN",
            "Game": "UNKNOWN",
            "Year": "UNKNOWN",
            "Copyright": "UNKNOWN",
            "GSF By": "UNKNOWN",
            "Tagger": "UNKNOWN",
            "Comment": "UNKNOWN",
            "Channels": "UNKNOWN",
            "Sample rate": "UNKNOWN",
            "Length": "UNKNOWN",
            "Fade": "UNKNOWN"
        }

        raw_base_sec = None
        fade_sec = 0.0

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
                    key_raw = parts[0].strip().lower()
                    val = parts[1].strip()

                    if "filename" in key_raw: info["Filename"] = val
                    elif "title" in key_raw: info["Title"] = val
                    elif "artist" in key_raw: info["Artist"] = val
                    elif "game" in key_raw: info["Game"] = val
                    elif "year" in key_raw: info["Year"] = val
                    elif "copyright" in key_raw: info["Copyright"] = val
                    elif "gsf by" in key_raw or "gsfby" in key_raw: info["GSF By"] = val
                    elif "tagger" in key_raw: info["Tagger"] = val
                    elif "comment" in key_raw: info["Comment"] = val
                    elif "channels" in key_raw: info["Channels"] = val
                    elif "sample rate" in key_raw: info["Sample rate"] = val
                    elif "length" in key_raw:
                        info["Length"] = val
                        parsed_len = self.parse_time_string(val)
                        if parsed_len:
                            raw_base_sec = parsed_len
                    elif "fade" in key_raw:
                        info["Fade"] = val
                        parsed_fade = self.parse_time_string(val)
                        if parsed_fade:
                            fade_sec = parsed_fade

                    if raw_base_sec is not None and not self.loop_forever_var.get():
                        try:
                            loop_count = int(self.loop_count_var.get())
                        except Exception:
                            loop_count = 1
                        multiplier = (1 + loop_count) if self.loop_enabled_var.get() else 1
                        self.total_duration_seconds = (raw_base_sec * multiplier) + fade_sec

                        if self.winfo_exists():
                            self.after(0, lambda: self.duration_var.set(f"Duration: {self.format_time(self.total_duration_seconds)}"))

                    lines_to_show = []
                    for k, v in info.items():
                        if v != "UNKNOWN" or k in ["Filename", "Title", "Artist", "Game", "Length"]:
                            lines_to_show.append(f"{k}: {v}")
                    formatted_text = "\n".join(lines_to_show)

                    if self.winfo_exists():
                        self.after(0, lambda t=formatted_text: self.update_textbox(t))
        except Exception:
            pass

        return_code = proc.wait()
        if self.winfo_exists() and self.gsf_proc == proc:
            if return_code == 0 and self.status_var.get() == "Playing" and not self.loop_forever_var.get():
                self.after(0, self.handle_track_completion)

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