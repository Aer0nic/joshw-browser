import os
import re
import shutil
import signal
import subprocess
import threading
import time
import random
import tkinter as tk
import ttkbootstrap as tb
from tkinter import ttk, messagebox
from config import is_emulated_file
from players import (
    get_saved_player_volume,
    register_volume_player,
    set_player_volume,
    InteractiveProgressBar,
    ExtractedFilesBrowserWindow,
    get_vgmstream_path,
    get_next_track_index
)

class VGMStreamPlayerWindow(tk.Toplevel):
    def __init__(self, parent, audio_file, playlist=None, start_index=None):
        super().__init__(parent)
        self.parent = parent
        self.audio_file = audio_file
        self.vgmstream_proc = None
        self.pactl_stream_id = None
        self.is_paused = False
        self.current_position_seconds = 0.0
        self.total_duration_seconds = 180.0
        self.is_scrubbing = False
        self.m3u_path = None
        
        saved_volume = get_saved_player_volume()
        self.volume_var = tk.IntVar(value=saved_volume)
        self.volume_label_var = tk.StringVar(value=f"Volume: {saved_volume}%")
        register_volume_player(self)

        is_emu = is_emulated_file(self.audio_file)
        self.loop_chk_var = tk.BooleanVar(value=is_emu)
        self.loop_count_var = tk.IntVar(value=1)
        self.loop_forever_var = tk.BooleanVar(value=False)

        self.playlist = []
        self.current_index = 0
        self.total_chapters = 1
        self.current_chapter = 0

        self.load_playlist_files(playlist, start_index)

        self.position_var = tk.StringVar(value="Position: 0.0 s")
        self.duration_var = tk.StringVar(value="Duration: unknown")
        self.status_var = tk.StringVar(value="Stopped")
        self.chapter_var = tk.StringVar(value=f"Subsong: 1 / {self.total_chapters}")
        self.seek_pct_var = tk.StringVar(value="0%")

        self.build_ui()
        self.protocol("WM_DELETE_WINDOW", self.close)

        if self.playlist:
            self.play_current_track()

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
        idx = self.chapter_listbox.nearest(event.y)
        hover_color = self.get_dynamic_hover_color()
        for i in range(self.chapter_listbox.size()):
            if i == idx:
                self.chapter_listbox.itemconfig(i, background=hover_color)
            else:
                self.chapter_listbox.itemconfig(i, background="")

    def on_listbox_leave(self, event):
        for i in range(self.chapter_listbox.size()):
            self.chapter_listbox.itemconfig(i, background="")

    def load_playlist_files(self, provided_playlist=None, provided_index=None):
        if provided_playlist:
            self.playlist = provided_playlist
            abs_target = os.path.normpath(os.path.abspath(self.audio_file))
            norm_playlist = [os.path.normpath(os.path.abspath(p)) for p in self.playlist]
            if abs_target in norm_playlist:
                self.current_index = norm_playlist.index(abs_target)
            elif provided_index is not None and 0 <= provided_index < len(self.playlist):
                self.current_index = provided_index
            else:
                self.current_index = 0
            return

        self.total_chapters = 1
        self.playlist = []
        folder = os.path.dirname(self.audio_file)
        abs_target = os.path.normpath(os.path.abspath(self.audio_file))

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
            self.playlist = [self.audio_file] * self.total_chapters
            self.current_index = 0
        else:
            try:
                for f in sorted(os.listdir(folder)):
                    full_p = os.path.join(folder, f)
                    if os.path.isfile(full_p) and not f.endswith(".m3u"):
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
        self.title(f"vgmstream123 Controller - {filename}")
        self.geometry("480x680")
        self.minsize(440, 560)
        self.transient(self.parent)

        padding = tb.Frame(self, padding=12)
        padding.pack(fill="both", expand=True)

        browse_frame = tb.Frame(padding)
        browse_frame.pack(side="top", anchor="w", pady=(0, 6))

        browse_btn = tk.Button(browse_frame, text="📁", font=("Arial", 12), width=3, command=self.on_browse_clicked)
        browse_btn.pack(side="top", anchor="w")

        self.title_label = tb.Label(padding, text=f"Now Playing:\n{filename}", font=("Arial", 9, "bold"), wraplength=450, justify="center")
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

        vol_frame = ttk.Frame(padding)
        vol_frame.pack(fill="x", pady=(0, 8))
        tb.Label(vol_frame, textvariable=self.volume_label_var, font=("Arial", 9)).pack(side="left", padx=5)
        self.volume_slider = tb.Scale(vol_frame, from_=0, to=150, orient="horizontal", command=self.on_volume_slide)
        self.volume_slider.set(self.volume_var.get())
        self.volume_slider.pack(side="right", fill="x", expand=True, padx=5)

        meta_frame = tb.Frame(padding)
        meta_frame.pack(fill="x", pady=(0, 8))
        tb.Label(meta_frame, textvariable=self.chapter_var, font=("Arial", 9, "bold")).pack(side="left", padx=5)
        tb.Label(meta_frame, textvariable=self.duration_var, font=("Arial", 9)).pack(side="right", padx=5)

        meta_box_frame = tb.LabelFrame(padding, text="Stream Info", padding=4)
        meta_box_frame.pack(fill="x", pady=(0, 8))

        self.metadata_text = tk.Text(
            meta_box_frame, 
            height=11, 
            wrap="none", 
            font=("Ubuntu Mono", 10), 
            bg="#000000", 
            fg="#ffff00", 
            insertbackground="yellow"
        )
        # Explicitly enforce colors to prevent any theme overrides
        self.metadata_text.configure(bg="#000000", fg="#ffff00")
        self.metadata_text.pack(side="left", fill="x", expand=True)
        meta_scroll = tb.Scrollbar(meta_box_frame, orient="vertical", command=self.metadata_text.yview)
        meta_scroll.pack(side="right", fill="y")
        self.metadata_text.configure(yscrollcommand=meta_scroll.set)
        self.metadata_text.config(state="disabled")

        list_frame = tb.LabelFrame(padding, text="Playlist Files / Subsongs", padding=6)
        list_frame.pack(fill="both", expand=True, pady=(0, 10))

        self.chapter_listbox = tb.Listbox(list_frame, height=4, font=("Arial", 9), selectmode="single", exportselection=False)
        self.chapter_listbox.pack(side="left", fill="both", expand=True)
        scrollbar = tb.Scrollbar(list_frame, orient="vertical", command=self.chapter_listbox.yview)
        scrollbar.pack(side="right", fill="y")
        self.chapter_listbox.configure(yscrollcommand=scrollbar.set)
        self.chapter_listbox.bind("<<ListboxSelect>>", self.on_chapter_select)
        self.chapter_listbox.bind("<Motion>", self.on_listbox_motion)
        self.chapter_listbox.bind("<Leave>", self.on_listbox_leave)
        self.populate_playlist_listbox()

        controls = tb.Frame(padding)
        controls.pack(pady=4)
        
        tb.Button(controls, text="Stop", width=8, command=self.stop).grid(row=0, column=0, padx=2)
        tb.Button(controls, text="|<< Prev", width=8, command=self.prev_track).grid(row=0, column=1, padx=2)
        tb.Button(controls, text="Play/Pause", width=10, command=self.toggle_play_pause).grid(row=0, column=2, padx=2)
        tb.Button(controls, text="Next >>|", width=8, command=self.next_track).grid(row=0, column=3, padx=2)
        tb.Button(controls, text="Close", width=8, command=self.close).grid(row=0, column=4, padx=2)

        # Bottom Sub-Row matching GSFPlayerWindow layout exactly:
        bottom_sub_frame = tb.Frame(padding)
        bottom_sub_frame.pack(fill="x", pady=(6, 0))

        left_loop_frame = tb.Frame(bottom_sub_frame)
        left_loop_frame.pack(side="left")
        tb.Checkbutton(
            left_loop_frame,
            variable=self.loop_chk_var,
            command=self.on_loop_checkbox_changed
        ).pack(side="left", padx=(0, 2))
        tb.Spinbox(
            left_loop_frame,
            from_=1,
            to=99,
            width=3,
            textvariable=self.loop_count_var,
            command=self.on_loop_spinbox_changed
        ).pack(side="left")

        center_rand_frame = tb.Frame(bottom_sub_frame)
        center_rand_frame.pack(side="left", expand=True)
        tb.Button(
            center_rand_frame,
            text="Random",
            width=8,
            command=self.play_random_track
        ).pack()

        right_loop_frame = tb.Frame(bottom_sub_frame)
        right_loop_frame.pack(side="right")
        tb.Checkbutton(
            right_loop_frame,
            text="Loop forever",
            variable=self.loop_forever_var,
            command=self.on_loop_forever_changed
        ).pack(side="right")

    def on_loop_checkbox_changed(self):
        self.on_loop_settings_changed()

    def on_loop_spinbox_changed(self):
            # Dynamically recalculate visual duration on spinbox change without restarting the process
            if not self.loop_forever_var.get() and self.audio_file:
                parsed_length, parsed_fade = self.parse_length_and_fade_from_strings(self.audio_file)
                multiplier = 1 + self.loop_count_var.get() if self.loop_chk_var.get() else 1
                effective_length = parsed_length * multiplier
                self.raw_duration_seconds = effective_length + parsed_fade
                formatted_dur = self.format_time(self.raw_duration_seconds)
                self.duration_var.set(formatted_dur)

    def on_loop_forever_changed(self):
        self.on_loop_settings_changed()

    def on_browse_clicked(self):
        ExtractedFilesBrowserWindow(self, os.path.dirname(self.audio_file))

    def on_loop_settings_changed(self):
        self.play_current_track(start_time=self.current_position_seconds)

    def on_volume_slide(self, value):
        set_player_volume(float(value), source_player=self)

    def apply_live_volume(self):
        if not self.pactl_stream_id and self.vgmstream_proc:
            self.pactl_stream_id = self.find_sink_input_id(self.vgmstream_proc.pid)
        
        if self.pactl_stream_id:
            try:
                percent = max(0, min(150, int(self.volume_var.get())))
                subprocess.run(
                    ["pactl", "set-sink-input-volume", str(self.pactl_stream_id), f"{percent}%"],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            except Exception:
                pass

    def find_sink_input_id(self, pid):
        try:
            result = subprocess.run(["pactl", "list", "sink-inputs"], stdout=subprocess.PIPE, text=True, check=True)
            current_index = None
            for line in result.stdout.splitlines():
                line = line.strip()
                if line.startswith("Sink Input #"):
                    current_index = line.split("#")[1]
                elif "application.process.id" in line and str(pid) in line:
                    return current_index
        except Exception:
            pass
        return None

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

    def parse_time_to_seconds(self, time_str):
        parts = time_str.split(':')
        try:
            if len(parts) == 3:
                return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
            elif len(parts) == 2:
                return float(parts[0]) * 60 + float(parts[1])
            elif len(parts) == 1:
                return float(parts[0])
        except Exception:
            pass
        return 0.0

    def start_vgmstream(self, start_time=0.0):
        if self.vgmstream_proc:
            try:
                self.vgmstream_proc.terminate()
                self.vgmstream_proc.wait(timeout=0.5)
            except Exception:
                try:
                    self.vgmstream_proc.kill()
                except Exception:
                    pass
            self.vgmstream_proc = None
        self.pactl_stream_id = None

        vgm_bin = get_vgmstream_path()
        if not vgm_bin or not (shutil.which(vgm_bin) or os.path.exists(vgm_bin)):
            messagebox.showerror("vgmstream123 Not Found", "vgmstream123 binary not found!")
            return

        cmd = [vgm_bin, "-m"]
        if self.loop_forever_var.get():
            cmd.append("-c")

        if self.loop_forever_var.get():
            cmd.extend(["-l", "0"])
        elif self.loop_chk_var.get():
            cmd.extend(["-l", str(self.loop_count_var.get())])

        if start_time > 0:
            cmd.extend(["-f", str(start_time)])
        if self.total_chapters > 1:
            cmd.extend(["-s", str(self.current_chapter + 1)])

        cmd.append(self.audio_file)

        if hasattr(self, "metadata_text") and self.winfo_exists():
            self.metadata_text.config(state="normal")
            self.metadata_text.delete("1.0", tk.END)
            self.metadata_text.config(state="disabled")

        try:
            self.vgmstream_proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
            self.is_paused = False
            self.status_var.set("Playing")
            
            def resolve_stream_id():
                time.sleep(0.15)
                if self.vgmstream_proc and not self.pactl_stream_id:
                    self.pactl_stream_id = self.find_sink_input_id(self.vgmstream_proc.pid)
                    if self.pactl_stream_id:
                        self.apply_live_volume()
            
            threading.Thread(target=resolve_stream_id, daemon=True).start()
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
                    if line_str.startswith("Time:"):
                        match = re.search(r'Time:\s*([0-9:\.]+).*?of\s*([0-9:\.]+)', line_str)
                        if match and self.winfo_exists() and not self.is_scrubbing:
                            curr_str, total_str = match.groups()
                            self.current_position_seconds = self.parse_time_to_seconds(curr_str)
                            dur = self.parse_time_to_seconds(total_str)
                            if dur > 0:
                                self.total_duration_seconds = dur
                            
                            self.position_var.set(f"Position: {curr_str} s")
                            self.duration_var.set(f"Duration: {total_str} s")
                            if self.total_duration_seconds > 0:
                                pct = int((self.current_position_seconds / self.total_duration_seconds) * 100)
                                self.seek_pct_var.set(f"{pct}%")
                            self.progress_bar_widget.update_progress(self.current_position_seconds, self.total_duration_seconds)
                    else:
                        if not line_str.startswith("Playing stream:"):
                            if self.winfo_exists():
                                self.after(0, lambda l=line_str: self.append_metadata_line(l))
        except Exception:
            pass

        return_code = proc.wait()
        if self.winfo_exists() and self.vgmstream_proc == proc and self.status_var.get() == "Playing":
            if return_code != 0:
                self.status_var.set("Playback Error")
            else:
                self.after(0, self.advance_after_end, proc)

    def append_metadata_line(self, line):
        if not self.winfo_exists():
            return
        try:
            self.metadata_text.config(state="normal")
            self.metadata_text.insert(tk.END, line + "\n")
            self.metadata_text.see(tk.END)
            self.metadata_text.config(state="disabled")
        except Exception:
            pass

    def advance_after_end(self, proc):
        if not self.winfo_exists() or self.vgmstream_proc != proc or self.status_var.get() != "Playing":
            return
        if self.loop_forever_var.get():
            self.play_current_track(start_time=0.0)
            return
        next_index = get_next_track_index(self.current_index, len(self.playlist), False)
        if next_index is None:
            self.vgmstream_proc = None
            self.pactl_stream_id = None
            self.status_var.set("Stopped")
            return
        self.current_index = next_index
        self.play_current_track()

    def seek_to_position(self, target_time):
        self.play_current_track(start_time=target_time)

    def play_current_track(self, start_time=0.0):
        if self.total_chapters > 1:
            self.current_chapter = self.current_index
            self.chapter_var.set(f"Subsong: {self.current_chapter + 1} / {self.total_chapters}")
            filename = f"{os.path.basename(self.audio_file)} (Subsong {self.current_chapter + 1})"
        else:
            self.audio_file = self.playlist[self.current_index]
            filename = os.path.basename(self.audio_file)
            self.chapter_var.set("Subsong: 1 / 1")

        # Update both the window title and the UI label
        self.title(f"vgmstream123 Controller - {filename}")
        if hasattr(self, "title_label") and self.winfo_exists():
            self.title_label.configure(text=f"Now Playing:\n{filename}")
            
        self.current_position_seconds = start_time
        if self.total_duration_seconds > 0:
            pct = int((start_time / self.total_duration_seconds) * 100)
            self.seek_pct_var.set(f"{pct}%")
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
                self.vgmstream_proc.wait(timeout=0.5)
            except Exception:
                try:
                    self.vgmstream_proc.kill()
                except Exception:
                    pass
            self.vgmstream_proc = None
        self.pactl_stream_id = None
        self.status_var.set("Stopped")

    def close(self):
        self.stop()
        try:
            self.destroy()
        except Exception:
            pass