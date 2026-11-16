import os
import time
import shutil
import subprocess
import tempfile
import threading
import random
import signal
import json
import re
import tkinter as tk
import ttkbootstrap as tb
from PIL import Image, ImageTk
from config import apply_global_font_scale
from platform_support import IS_WINDOWS, hidden_popen_kwargs, resume_process, set_process_volume, suspend_process, windows_safe_path
from player_launcher import attach_settings_sync, run_player
from tkinter import ttk, messagebox, filedialog

from config import get_cache_directory, is_emulated_file, resource_path, get_binary_path
from players import (
    get_saved_player_volume,
    register_volume_player,
    set_player_volume,
    InteractiveProgressBar,
    ExtractedFilesBrowserWindow,
    _active_volume_players
)


class ArcadePlayerWindow(tb.Window):
    def __init__(self, parent=None, audio_file=None, playlist=None, start_index=None):
        saved_volume = get_saved_player_volume()
        initial_settings = {}
        try:
            with open(os.path.join(get_cache_directory(), "settings.json"), "r", encoding="utf-8") as f:
                initial_settings = json.load(f)
        except Exception:
            pass
        saved_theme = initial_settings.get("theme", "cosmo")

        super().__init__(themename=saved_theme)
        self.parent = parent
        self.audio_file = audio_file
        self.proc = None
        self.arcade_wpctl_id = None
        self.is_paused = False
        self.is_scrubbing = False
        self.is_sliding_volume = False
        
        self.base_duration_seconds = 180.0
        self.raw_duration_seconds = 180.0
        self.current_position_seconds = 0.0
        self.has_loop = False
        self.loop_seconds = 0.0      # length of the looped section ("Loop: Yes (mm:ss)")
        self.fade_seconds = 5.0      # VGMPlay's fade-out after the last loop
        self.proc_max_loops = 1      # MaxLoops the running vgmplay was started with
        self.proc_started_at = 0.0
        self.ini_dir = None          # per-window folder holding the generated VGMPlay.ini

        is_emu = is_emulated_file(self.audio_file)
        self.loop_chk_var = tk.BooleanVar(value=is_emu)
        self.loop_count_var = tk.IntVar(value=1)
        self.loop_forever_var = tk.BooleanVar(value=False)

        self.playlist = []
        self.current_index = 0
        self.track_repeat_count = 0
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

        try:
            icon_file = resource_path("assets/icons/arcadeicon.png")
            if icon_file.exists():
                self.iconphoto(False, tk.PhotoImage(file=str(icon_file)))
        except Exception:
            pass

        self.protocol("WM_DELETE_WINDOW", self.close)
        attach_settings_sync(self, on_font_changed=apply_global_font_scale)

        if self.playlist:
            self.play_current_track()
            self.start_timer_thread()

    def format_time(self, seconds):
        if seconds is None or seconds < 0:
            return "00:00:00"
        total_seconds = int(seconds)
        return f"{total_seconds // 3600:02d}:{(total_seconds % 3600) // 60:02d}:{total_seconds % 60:02d}"

    def parse_leftmost_mm_ss(self, time_part_str):
        """
        Parses time string like '01:41:54' or '01:41.54'.
        Takes the first two components from the left (MM:SS) and converts to total seconds.
        """
        try:
            # Replace dot separator before seconds/milliseconds with colon for uniform splitting
            normalized = time_part_str.strip().replace(".", ":")
            parts = [p for p in normalized.split(":") if p]
            if len(parts) >= 2:
                mins = float(parts[0])
                secs = float(parts[1])
                return mins * 60.0 + secs
        except Exception:
            pass
        return None

    def load_playlist_files(self, provided_playlist=None, provided_index=None):
        abs_target = os.path.normpath(os.path.abspath(self.audio_file))
        arcade_exts = {".vgm", ".vgz"}

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

        folder = os.path.dirname(self.audio_file)
        try:
            files = sorted(os.listdir(folder))
            for f in files:
                full_p = os.path.join(folder, f)
                if os.path.isfile(full_p) and os.path.splitext(f)[1].lower() in arcade_exts:
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

    def build_ui(self):
        filename = os.path.basename(self.audio_file)
        self.title(f"Arcade Player - {filename}")
        self.geometry("520x580")
        self.minsize(520, 580)

        padding = tb.Frame(self, padding=12)
        padding.pack(fill="both", expand=True)

        top_header_frame = tb.Frame(padding)
        top_header_frame.pack(side="top", fill="x", pady=(0, 6))

        browse_btn = tb.Button(top_header_frame, text="📁", width=3, bootstyle="outline-primary", command=self.on_browse_clicked)
        browse_btn.pack(side="left", anchor="center", padx=(0, 8))

        self.banner_canvas = tb.Canvas(top_header_frame, height=85, highlightthickness=0)
        self.banner_canvas.pack(side="left", fill="x", expand=True)

        try:
            banner_path = resource_path("assets/icons/arcadebanner.png")
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
            print(f"[ArcadePlayer] Could not load banner image: {e}")

        self.title_label = tb.Label(padding, text=f"Now Playing:\n{filename}", font=("Arial", 9, "bold"), wraplength=440, justify="center")
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
            get_duration_func=lambda: self.raw_duration_seconds,
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
        self.volume_slider.bind("<ButtonPress-1>", lambda e: setattr(self, "is_sliding_volume", True))
        self.volume_slider.bind("<ButtonRelease-1>", lambda e: self.after(300, lambda: setattr(self, "is_sliding_volume", False)))

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

        btn_bottom = tb.Frame(padding)
        btn_bottom.pack(pady=(6, 0), fill="x")

        tb.Checkbutton(
            btn_bottom,
            variable=self.loop_chk_var,
            command=self.on_loop_checkbox_changed
        ).grid(row=0, column=0, padx=(0, 2), sticky="w")

        tb.Spinbox(
            btn_bottom,
            from_=1,
            to=99,
            width=3,
            textvariable=self.loop_count_var,
            command=self.on_loop_spinbox_changed
        ).grid(row=0, column=1, padx=(0, 6), sticky="w")

        tb.Button(
            btn_bottom,
            text="Random",
            width=12,
            command=self.random_track
        ).grid(row=0, column=2, padx=2)

        tb.Checkbutton(
            btn_bottom,
            text="Loop forever",
            variable=self.loop_forever_var,
            command=self.on_loop_forever_changed
        ).grid(row=0, column=3, padx=2, sticky="e")

        btn_bottom.grid_columnconfigure(2, weight=1)

    def seek_to_position(self, target_time):
        self.current_position_seconds = target_time
        self.start_vgmplay()

    def on_browse_clicked(self):
        folder = os.path.dirname(self.audio_file)
        if os.path.isdir(folder):
            ExtractedFilesBrowserWindow(self, folder)
        else:
            self.open_file_picker()

    def open_file_picker(self):
        initial_dir = os.path.dirname(self.audio_file) if os.path.isdir(os.path.dirname(self.audio_file)) else os.path.expanduser("~")
        file_path = filedialog.askopenfilename(
            title="Select Arcade Music File",
            initialdir=initial_dir,
            filetypes=[("VGZ/VGM Files", "*.vgz *.vgm"), ("All Files", "*.*")]
        )
        if file_path:
            self.play_audio_file(file_path)

    def play_audio_file(self, audio_file, playlist=None, start_index=None):
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

    def on_loop_checkbox_changed(self):
        if self.loop_chk_var.get():
            self.loop_forever_var.set(False)
        self.recalculate_duration()

    def on_loop_forever_changed(self):
        if self.loop_forever_var.get():
            self.loop_chk_var.set(False)
        self.recalculate_duration()

    def on_loop_spinbox_changed(self):
        if self.loop_chk_var.get():
            self.recalculate_duration()

    def on_loop_settings_changed(self):
        self.recalculate_duration()

    def loop_count(self):
        """How many times the loop checkbox asks for (1 when it is off)."""
        if not self.loop_chk_var.get():
            return 1
        try:
            return max(1, int(self.loop_count_var.get()))
        except (tk.TclError, ValueError):
            return 1

    def recalculate_duration(self):
        if self.loop_forever_var.get():
            self.raw_duration_seconds = self.base_duration_seconds
            self.duration_var.set("Duration: Forever")
            return
        if self.has_loop:
            # VGMPlay plays the whole track once, repeats the looped section
            # until it has looped MaxLoops times, then fades out
            self.raw_duration_seconds = (self.base_duration_seconds
                                         + (self.loop_count() - 1) * self.loop_seconds
                                         + self.fade_seconds)
            text = self.format_time(self.raw_duration_seconds)
        else:
            # No loop point: each play is the plain track; repeats restart it
            self.raw_duration_seconds = self.base_duration_seconds
            text = self.format_time(self.raw_duration_seconds)
            if self.loop_count() > 1:
                text += f" (play {self.track_repeat_count + 1} of {self.loop_count()})"
        self.duration_var.set(f"Duration: {text}")

    def on_volume_slide(self, val):
        set_player_volume(float(val), source_player=self)
        try:
            self.volume_var.set(int(float(val)))
            self.volume_label_var.set(f"Volume: {int(float(val))}%")
        except Exception:
            pass
        self.apply_live_volume()

    def apply_live_volume(self):
        if IS_WINDOWS:
            set_process_volume(self.proc, max(0, min(150, int(self.volume_var.get()))) / 100.0)
            return
        if not self.arcade_wpctl_id and self.proc:
            threading.Thread(target=self._resolve_and_apply_volume, daemon=True).start()
            return

        if self.arcade_wpctl_id:
            try:
                percent = max(0, min(150, int(self.volume_var.get())))
                subprocess.run(
                    ["wpctl", "set-volume", str(self.arcade_wpctl_id), f"{percent}%"],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            except Exception:
                self.arcade_wpctl_id = None
                threading.Thread(target=self._resolve_and_apply_volume, daemon=True).start()

    def _resolve_and_apply_volume(self):
        if IS_WINDOWS:
            set_process_volume(self.proc, max(0, min(150, float(self.volume_var.get()))) / 100.0)
            return
        awk_cmd = "wpctl status | awk '/Streams:/ {in_streams=1; next} in_streams && /vgmplay/ {sub(/\\.$/, \"\", $1); print $1; exit} in_streams && /^[^[:space:]]/ {exit}'"
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
                        self.arcade_wpctl_id = stream_id
                        percent = max(0, min(150, int(self.volume_var.get())))
                        subprocess.run(
                            ["wpctl", "set-volume", str(stream_id), f"{percent}%"],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL
                        )
                        return
            except Exception:
                pass
            time.sleep(0.15)

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

    def start_timer_thread(self):
        def timer_loop():
            while self.winfo_exists():
                if self.status_var.get() == "Playing" and not self.is_scrubbing:
                    self.current_position_seconds += 1.0

                    if self.loop_forever_var.get():
                        # Show the position inside the loop, like VGMPlay does
                        if (self.has_loop and self.loop_seconds > 0
                                and self.current_position_seconds >= self.base_duration_seconds):
                            self.current_position_seconds -= self.loop_seconds
                    elif (self.proc_max_loops == 0 and self.raw_duration_seconds > 0
                          and self.current_position_seconds >= self.raw_duration_seconds):
                        # vgmplay was started to loop forever but Loop forever has
                        # since been switched off: end the track ourselves.
                        # (Otherwise vgmplay ends the track and _watch_process
                        # moves on.)
                        self.after(0, self.finish_running_track)

                    if self.raw_duration_seconds > 0:
                        pct = int((self.current_position_seconds / self.raw_duration_seconds) * 100)
                        self.seek_pct_var.set(f"{pct}%")
                    self.position_var.set(f"Position: {self.format_time(self.current_position_seconds)}")
                    if self.winfo_exists():
                        self.after(0, lambda: self.progress_bar_widget.update_progress(self.current_position_seconds, self.raw_duration_seconds))
                time.sleep(1.0)
        threading.Thread(target=timer_loop, daemon=True).start()

    def _read_vgmplay_stdout(self, proc):
        meta_info = {}
        for line in iter(proc.stdout.readline, ''):
            if not line:
                break
            line_str = line.strip()

            if line_str.startswith("Track Title:"):
                meta_info["title"] = line_str.split("Track Title:", 1)[1].strip()
            elif line_str.startswith("Game Name:"):
                meta_info["game"] = line_str.split("Game Name:", 1)[1].strip()

            if any(k in meta_info for k in ("title", "game")):
                t_parts = []
                if meta_info.get("title"):
                    t_parts.append(meta_info["title"])
                if meta_info.get("game"):
                    t_parts.append(f"({meta_info['game']})")
                display_str = " ".join(t_parts)
                if hasattr(self, "title_label") and self.winfo_exists():
                    self.after(0, lambda ds=display_str: self.title_label.configure(text=f"Now Playing:\n{ds}"))
                    self.after(0, lambda ds=display_str: self.title(f"Arcade Player - {ds}"))

            m_loop = re.search(r"Loop: Yes \(([\d:.]+)\)", line_str)
            if m_loop:
                self.has_loop = True
                self.loop_seconds = self.parse_leftmost_mm_ss(m_loop.group(1)) or 0.0
                self.after(0, self.recalculate_duration)
            elif "Loop: No" in line_str:
                self.has_loop = False
                self.after(0, self.recalculate_duration)

            # Explicitly capture XX:XX:XX or XX:XX.XX directly before 'seconds'
            # Examples matched: "01:41.54 seconds", "00:01:41.54 seconds", "01:41:54 seconds"
            m_sec = re.search(r'([\d\:\.]+)\s+seconds', line_str, re.IGNORECASE)
            if m_sec:
                time_str = m_sec.group(1)
                parsed_tot = self.parse_leftmost_mm_ss(time_str)
                if parsed_tot and parsed_tot > 0 and parsed_tot != self.base_duration_seconds:
                    self.base_duration_seconds = parsed_tot
                    self.after(0, self.recalculate_duration)

    def start_vgmplay(self):
        if self.proc:
            try:
                if hasattr(os, "kill") and hasattr(signal, "SIGKILL"):
                    os.kill(self.proc.pid, signal.SIGKILL)
                else:
                    self.proc.kill()
                self.proc.wait(timeout=0.5)
            except Exception:
                pass
            self.proc = None
        self.arcade_wpctl_id = None

        vgmplay_bin = str(get_binary_path("vgmplay"))
        cmd = [vgmplay_bin, windows_safe_path(self.audio_file)]

        # 0 = loop forever, otherwise how many times the looped section plays
        max_loops = 0 if self.loop_forever_var.get() else self.loop_count()
        try:
            ini_dir = self.write_vgmplay_ini(vgmplay_bin, max_loops)
        except OSError as e:
            print(f"[arcade] could not write VGMPlay.ini: {e}")
            ini_dir = None

        try:
            # vgmplay reads VGMPlay.ini from the working directory before its own folder
            self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                                         cwd=ini_dir or os.path.dirname(vgmplay_bin) or None, **hidden_popen_kwargs())
            self.proc_max_loops = max_loops if ini_dir else 2
            self.proc_started_at = time.time()
            threading.Thread(target=self._read_vgmplay_stdout, args=(self.proc,), daemon=True).start()
            threading.Thread(target=self._resolve_and_apply_volume, daemon=True).start()
            self.after(300, self._watch_process, self.proc)
        except Exception as e:
            messagebox.showerror("vgmplay Error", f"Could not launch vgmplay process:\n\n{e}")
            return

    def write_vgmplay_ini(self, vgmplay_bin, max_loops):
        """Write a VGMPlay.ini with MaxLoops set to max_loops into this window's
        own folder and return that folder.

        Starts from the ini next to vgmplay (the bundled Windows one) when
        there is one, so its other settings are kept.
        """
        base_text = ""
        bin_dir = os.path.dirname(vgmplay_bin)
        for name in ("VGMPlay.ini", "vgmplay.ini"):
            candidate = os.path.join(bin_dir, name)
            if bin_dir and os.path.isfile(candidate):
                with open(candidate, "r", encoding="latin-1") as f:
                    base_text = f.read()
                break

        lines = base_text.splitlines()
        setting = f"MaxLoops = {max_loops}"
        replaced = False
        for i, line in enumerate(lines):
            if re.match(r"\s*MaxLoops\s*=", line, re.IGNORECASE):
                lines[i] = setting
                replaced = True
        if not replaced:
            general = next((i for i, line in enumerate(lines)
                            if line.strip().lower() == "[general]"), None)
            if general is None:
                lines[:0] = ["[General]", setting]
            else:
                lines.insert(general + 1, setting)

        self.fade_seconds = 5.0  # VGMPlay's default FadeTime
        for line in lines:
            m = re.match(r"\s*FadeTime\s*=\s*(\S+)", line, re.IGNORECASE)
            if m:
                try:
                    self.fade_seconds = int(m.group(1), 0) / 1000.0
                except ValueError:
                    pass

        if not self.ini_dir or not os.path.isdir(self.ini_dir):
            self.ini_dir = tempfile.mkdtemp(prefix="vgmplay_", dir=get_cache_directory())
        text = "\n".join(lines) + "\n"
        # Linux vgmplay looks for "<program name>.ini" before "VGMPlay.ini"
        names = {"VGMPlay.ini", os.path.splitext(os.path.basename(vgmplay_bin))[0] + ".ini"}
        for name in names:
            with open(os.path.join(self.ini_dir, name), "w", encoding="latin-1") as f:
                f.write(text)
        return self.ini_dir

    # ------------------------------------------------------------------
    # End of track: loop again, or move on
    # ------------------------------------------------------------------
    def _watch_process(self, proc):
        try:
            if not self.winfo_exists():
                return
        except tk.TclError:
            return
        if proc is not self.proc:
            return  # stopped, skipped or restarted in the meantime
        if proc.poll() is None:
            self.after(300, self._watch_process, proc)
            return
        self._on_track_finished(ran_for=time.time() - self.proc_started_at)

    def finish_running_track(self):
        """End the current track now and continue as if vgmplay had finished it."""
        if not self.proc or self.proc.poll() is not None or self.loop_forever_var.get():
            return
        self.stop()
        self._on_track_finished()

    def _on_track_finished(self, ran_for=None):
        self.proc = None
        self.arcade_wpctl_id = None
        if ran_for is not None and ran_for < 1.0:
            # vgmplay quit straight away: the file could not be played.
            # Don't restart it in a tight loop.
            self.status_var.set("Error")
            return

        if self.loop_forever_var.get():
            self._replay_current_track()
            return
        if not self.has_loop and self.loop_count() > 1:
            # No loop point in the file, so vgmplay can't loop it: play it again
            self.track_repeat_count += 1
            if self.track_repeat_count < self.loop_count():
                self._replay_current_track()
                return

        if self.current_index + 1 < len(self.playlist):
            self.current_index += 1
            self.play_current_track()
        else:
            self.stop()
            self.current_position_seconds = 0.0
            self.position_var.set("Position: 00:00:00")
            self.seek_pct_var.set("0%")
            try:
                self.progress_bar_widget.update_progress(0.0, self.raw_duration_seconds)
            except Exception:
                pass
            self.status_var.set("Finished")

    def _replay_current_track(self):
        self.current_position_seconds = 0.0
        self.position_var.set("Position: 00:00:00")
        self.recalculate_duration()
        self.start_vgmplay()
        self.is_paused = False
        self.status_var.set("Playing")

    def play_current_track(self):
        self.track_repeat_count = 0
        item = self.playlist[self.current_index]
        if isinstance(item, dict):
            target_file = item["file"]
            display_title = item["title"]
        else:
            target_file = item
            display_title = os.path.basename(target_file)

        self.audio_file = target_file
        self.current_position_seconds = 0.0
        self.base_duration_seconds = 180.0
        self.has_loop = False
        self.loop_seconds = 0.0
        self.is_paused = False
        self.position_var.set("Position: 00:00:00")

        self.recalculate_duration()

        if hasattr(self, "title_label") and self.winfo_exists():
            self.title_label.configure(text=f"Now Playing:\n{display_title}")
        self.title(f"Arcade Player - {display_title}")
        self.track_var.set(f"Track: {self.current_index + 1} / {len(self.playlist)}")
        self.start_vgmplay()
        self.status_var.set("Playing")
        self.update_listbox_selection()

    def toggle_play_pause(self):
        if not self.proc or self.proc.poll() is not None:
            self.play_current_track()
            return
        try:
            if self.is_paused:
                resume_process(self.proc)
                self.is_paused = False
                self.status_var.set("Playing")
            else:
                suspend_process(self.proc)
                self.is_paused = True
                self.status_var.set("Paused")
        except Exception:
            pass

    def prev_track(self):
        if len(self.playlist) > 1:
            self.current_index = (self.current_index - 1) % len(self.playlist)
            self.play_current_track()

    def next_track(self):
        if len(self.playlist) > 1:
            self.current_index = (self.current_index + 1) % len(self.playlist)
            self.play_current_track()

    def random_track(self):
        if len(self.playlist) > 1:
            new_index = self.current_index
            while new_index == self.current_index:
                new_index = random.randint(0, len(self.playlist) - 1)
            self.current_index = new_index
            self.play_current_track()

    def on_playlist_select(self, event):
        selection = self.playlist_listbox.curselection()
        if not selection:
            return
        idx = selection[0]
        if idx != self.current_index:
            self.current_index = idx
            self.play_current_track()

    def stop(self):
        if self.proc:
            try:
                if hasattr(os, "kill") and hasattr(signal, "SIGKILL"):
                    os.kill(self.proc.pid, signal.SIGKILL)
                else:
                    self.proc.kill()
                self.proc.wait(timeout=0.5)
            except Exception:
                pass
            self.proc = None
        self.arcade_wpctl_id = None
        self.status_var.set("Stopped")

    def close(self, event=None):
        try:
            _active_volume_players.discard(self)
        except Exception:
            pass
        self.stop()
        if self.ini_dir:
            shutil.rmtree(self.ini_dir, ignore_errors=True)
            self.ini_dir = None
        try:
            self.destroy()
        except Exception:
            pass

ArcadePlayer = ArcadePlayerWindow

if __name__ == "__main__":
    run_player(ArcadePlayerWindow)