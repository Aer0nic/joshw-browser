import os
import signal
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from config import is_emulated_file, resource_path
from players import (
    get_saved_player_volume,
    register_volume_player,
    set_player_volume,
    InteractiveProgressBar,
    ExtractedFilesBrowserWindow,
    get_2sfplay_path,
    get_next_track_index
)

class TwoSFPlayerWindow(tk.Toplevel):
    def __init__(self, parent, audio_file, playlist=None, start_index=None):
        super().__init__(parent)
        self.parent = parent
        self.audio_file = audio_file
        self.playlist = playlist or [audio_file]
        self.current_index = start_index if start_index is not None else 0
        self.current_position_seconds = 0.0
        self.total_duration_seconds = 180.0
        self.is_scrubbing = False
        self.track_repeat_count = 0
        self.proc = None

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

        self.load_playlist_files()
        self.build_ui()

        try:
            icon_file = resource_path("assets/icons/player_icon.png")
            if icon_file.exists():
                self.iconphoto(False, tk.PhotoImage(file=str(icon_file)))
        except Exception:
            pass

        self.protocol("WM_DELETE_WINDOW", self.close)

        if self.playlist:
            self.play_current_track()
            self.start_timer_thread()

    def format_time(self, seconds):
        if seconds is None or seconds < 0:
            return "00:00:00"
        total_seconds = int(seconds)
        return f"{total_seconds // 3600:02d}:{(total_seconds % 3600) // 60:02d}:{total_seconds % 60:02d}"

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
                if f.lower().endswith((".mini2sf", ".2sf")):
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
        self.title(f"2sfPlay Controller - {filename}")
        self.geometry("480x580")
        self.minsize(440, 500)
        self.transient(self.parent)

        padding = ttk.Frame(self, padding=12)
        padding.pack(fill="both", expand=True)

        browse_frame = ttk.Frame(padding)
        browse_frame.pack(side="top", anchor="w", pady=(0, 6))

        browse_btn = tk.Button(browse_frame, text="📁", font=("Arial", 12), width=3, command=self.on_browse_clicked)
        browse_btn.pack(side="top", anchor="w")

        self.title_label = ttk.Label(padding, text=f"Now Playing:\n{filename}", font=("Arial", 9, "bold"), wraplength=450, justify="center")
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
        self.playlist_listbox.bind("<Motion>", self.on_listbox_motion)
        self.playlist_listbox.bind("<Leave>", self.on_listbox_leave)
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

    def seek_to_position(self, target_time):
        self.current_position_seconds = max(0.0, min(target_time, self.total_duration_seconds))
        if self.total_duration_seconds > 0:
            pct = int((self.current_position_seconds / self.total_duration_seconds) * 100)
            self.seek_pct_var.set(f"{pct}%")
        self.progress_bar_widget.update_progress(self.current_position_seconds, self.total_duration_seconds)
        self.start_2sfplay(preserve_time=True)

    def seek_relative(self, seconds):
        new_pos = max(0.0, min(self.current_position_seconds + seconds, self.total_duration_seconds))
        self.seek_to_position(new_pos)

    def on_browse_clicked(self):
        ExtractedFilesBrowserWindow(self, os.path.dirname(self.audio_file))

    def on_loop_settings_changed(self):
        self.start_2sfplay(preserve_time=True)

    def on_volume_slide(self, val):
        set_player_volume(float(val), source_player=self)

    def apply_live_volume(self):
        pass

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

    def on_playlist_select(self, event):
        selection = self.playlist_listbox.curselection()
        if not selection:
            return
        idx = selection[0]
        if idx != self.current_index:
            self.current_index = idx
            self.play_current_track()

    def stop_current_playback(self):
        if self.proc:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=1)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            self.proc = None

    def start_2sfplay(self, preserve_time=False):
        self.stop_current_playback()
        if not preserve_time:
            self.current_position_seconds = 0.0

        twosf_bin = get_2sfplay_path()
        self.audio_file = self.playlist[self.current_index]

        env = os.environ.copy()
        env["SDL_VIDEODRIVER"] = "dummy"

        cmd_args = [str(twosf_bin)]
        if self.current_position_seconds > 0:
            cmd_args.append(f"-b{int(round(self.current_position_seconds * 1000))}")
        cmd_args.append(self.audio_file)

        try:
            self.proc = subprocess.Popen(
                cmd_args,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            self.is_paused = False
            self.status_var.set("Playing")
            self.track_var.set(f"Track: {self.current_index + 1} / {len(self.playlist)}")
            filename = os.path.basename(self.audio_file)
            if hasattr(self, "title_label") and self.winfo_exists():
                self.title_label.configure(text=f"Now Playing:\n{filename}")
            self.title(f"2sfPlay Controller - {filename}")
            self.update_listbox_selection()
            threading.Thread(target=self.read_output, daemon=True).start()
        except Exception as e:
            messagebox.showerror("2sfPlay Error", f"Could not launch 2sfPlay:\n\n{e}")

    def play_current_track(self):
        self.track_repeat_count = 0
        self.start_2sfplay(preserve_time=False)

    def start_timer_thread(self):
        def timer_loop():
            while self.winfo_exists():
                if self.status_var.get() == "Playing" and not self.is_scrubbing:
                    self.current_position_seconds = min(
                        self.current_position_seconds + 1.0,
                        self.total_duration_seconds
                    )
                    if self.total_duration_seconds > 0:
                        pct = int((self.current_position_seconds / self.total_duration_seconds) * 100)
                        self.seek_pct_var.set(f"{pct}%")
                    self.position_var.set(f"Position: {self.format_time(self.current_position_seconds)}")
                    if self.winfo_exists():
                        self.after(0, lambda: self.progress_bar_widget.update_progress(self.current_position_seconds, self.total_duration_seconds))
                time.sleep(1.0)
        threading.Thread(target=timer_loop, daemon=True).start()

    def toggle_play_pause(self):
        if not self.proc:
            return
        try:
            if self.is_paused:
                os.kill(self.proc.pid, signal.SIGCONT)
                self.is_paused = False
                self.status_var.set("Playing")
            else:
                os.kill(self.proc.pid, signal.SIGSTOP)
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

    def read_output(self):
        proc = self.proc
        if not proc or not proc.stdout:
            return
        try:
            while proc.poll() is None:
                line = proc.stdout.readline()
                if not line:
                    break
                if "Remaining:" in line:
                    import re
                    m = re.search(r'Remaining:\s*(\d+)s\s*/\s*(\d+)s', line)
                    if m and self.winfo_exists():
                        rem, tot = int(m.group(1)), int(m.group(2))
                        self.total_duration_seconds = float(tot)
                        curr = float(tot - rem)
                        if not self.is_scrubbing:
                            self.current_position_seconds = max(0.0, curr)
                            self.duration_var.set(f"Duration: {self.format_time(tot)}")
        except Exception:
            pass

        return_code = proc.wait()
        if not self.winfo_exists() or self.proc is not proc or self.status_var.get() != "Playing":
            return
        if return_code != 0:
            self.status_var.set("Playback Error")
            return
        self.after(0, self.advance_after_end)

    def advance_after_end(self):
        if self.status_var.get() != "Playing":
            return
        if self.loop_forever_var.get():
            self.start_2sfplay(preserve_time=False)
            return
        repeat_limit = max(1, int(self.loop_count_var.get()))
        if self.loop_chk_var.get() and self.track_repeat_count < repeat_limit - 1:
            self.track_repeat_count += 1
            self.start_2sfplay(preserve_time=False)
            return

        next_index = get_next_track_index(self.current_index, len(self.playlist), False)
        if next_index is None:
            self.proc = None
            self.status_var.set("Stopped")
            return
        self.current_index = next_index
        self.play_current_track()

    def stop(self):
        self.stop_current_playback()
        self.status_var.set("Stopped")

    def close(self):
        self.stop()
        try:
            self.destroy()
        except Exception:
            pass