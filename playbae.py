import os
import time
import shutil
import subprocess
import tempfile
import threading
import random
import signal
import json
import tkinter as tk
from tkinter import ttk, messagebox, filedialog
from mpris_helper import setup_mpris_server

from config import get_cache_directory, is_emulated_file
from players import (
    get_saved_player_volume,
    register_volume_player,
    set_player_volume,
    InteractiveProgressBar,
    ExtractedFilesBrowserWindow,
    _active_volume_players
)

def get_available_soundfonts():
    sf_files = set()
    valid_exts = {".hsb", ".zsb", ".sf2", ".sf3", ".dls"}
    
    # Check ~/.config/joshw/
    sf_dir = os.path.expanduser("~/.config/joshw")
    if os.path.isdir(sf_dir):
        try:
            for f in os.listdir(sf_dir):
                if os.path.splitext(f)[1].lower() in valid_exts:
                    sf_files.add(f)
        except Exception:
            pass

    # Check cache directory
    cache_dir = get_cache_directory()
    if os.path.isdir(cache_dir):
        try:
            for f in os.listdir(cache_dir):
                if os.path.splitext(f)[1].lower() in valid_exts:
                    sf_files.add(f)
        except Exception:
            pass

    sf_files.add("FluidR3_GM.sf2")
    return sorted(list(sf_files))

def get_saved_soundfont():
    settings_path = os.path.expanduser("~/.config/joshw/settings.json")
    try:
        if os.path.exists(settings_path):
            with open(settings_path, "r") as f:
                data = json.load(f)
                return data.get("playbae_soundfont", "FluidR3_GM.sf2")
    except Exception:
        pass
    return "FluidR3_GM.sf2"

def save_soundfont_setting(sf_name):
    settings_path = os.path.expanduser("~/.config/joshw/settings.json")
    try:
        os.makedirs(os.path.dirname(settings_path), exist_ok=True)
        data = {}
        if os.path.exists(settings_path):
            try:
                with open(settings_path, "r") as f:
                    data = json.load(f)
            except Exception:
                pass
        data["playbae_soundfont"] = sf_name
        with open(settings_path, "w") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass


class PlayBAEPlayerWindow(tk.Toplevel):
    def __init__(self, parent, audio_file, playlist=None, start_index=None):
        super().__init__(parent)
        self.parent = parent
        self.audio_file = audio_file
        self.proc = None
        self.playbae_wpctl_id = None
        self.is_paused = False
        self.is_scrubbing = False
        self.is_sliding_volume = False
        self.raw_duration_seconds = 180.0
        self.current_position_seconds = 0.0

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
        self.protocol("WM_DELETE_WINDOW", self.close)

        if self.playlist:
            self.play_current_track()
            self.start_timer_thread()

class PlayBAEPlayerWindow(tk.Toplevel):
    def __init__(self, parent, audio_file, playlist=None, start_index=None):
        # ... existing __init__ setup ...
        
        # Initialize MPRIS integration for Cinnamon sound applet
        self.mpris_server, self.mpris_adapter = setup_mpris_server(self, player_name="PlayBAE")

    def format_time(self, seconds):
        if seconds is None or seconds < 0:
            return "00:00:00"
        total_seconds = int(seconds)
        return f"{total_seconds // 3600:02d}:{(total_seconds % 3600) // 60:02d}:{total_seconds % 60:02d}"

    def load_playlist_files(self, provided_playlist=None, provided_index=None):
        abs_target = os.path.normpath(os.path.abspath(self.audio_file))
        playbae_exts = {".mid", ".midi", ".kar", ".rmi", ".rmf", ".zmf", ".xmf", ".mxmf", ".mthc"}

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
                if os.path.isfile(full_p) and os.path.splitext(f)[1].lower() in playbae_exts:
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
        self.title(f"NeoBAE playbae Controller - {filename}")
        self.geometry("520$580" if False else "520x580")
        self.minsize(460, 500)
        self.transient(self.parent)

        padding = ttk.Frame(self, padding=12)
        padding.pack(fill="both", expand=True)

        # Top header frame (Browse button left, Soundfont selector right)
        top_header_frame = ttk.Frame(padding)
        top_header_frame.pack(side="top", fill="x", pady=(0, 6))

        browse_btn = tk.Button(top_header_frame, text="📁", font=("Arial", 12), width=3, command=self.on_browse_clicked)
        browse_btn.pack(side="left", anchor="w")

        sf_frame = ttk.Frame(top_header_frame)
        sf_frame.pack(side="right", anchor="e")

        ttk.Label(sf_frame, text="Soundfont:", font=("Arial", 9)).pack(side="left", padx=(0, 4))
        
        available_sfs = get_available_soundfonts()
        sf_choices = ["(None)"] + available_sfs
        saved_sf = get_saved_soundfont()
        
        initial_sf = saved_sf if saved_sf in sf_choices else "FluidR3_GM.sf2"
        if initial_sf not in sf_choices and "(None)" in sf_choices:
            initial_sf = "FluidR3_GM.sf2" if "FluidR3_GM.sf2" in sf_choices else "(None)"

        self.soundfont_var = tk.StringVar(value=initial_sf)
        self.soundfont_combo = ttk.Combobox(sf_frame, textvariable=self.soundfont_var, values=sf_choices, state="readonly", width=20)
        self.soundfont_combo.pack(side="left")
        self.soundfont_combo.bind("<<ComboboxSelected>>", self.on_soundfont_changed)

        self.title_label = ttk.Label(padding, text=f"Now Playing:\n{filename}", font=("Arial", 9, "bold"), wraplength=300, justify="center")
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
            command=self.on_loop_settings_changed
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
        self.current_position_seconds = target_time
        self.start_playbae()

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

    def on_soundfont_changed(self, event=None):
        selected_sf = self.soundfont_var.get()
        save_soundfont_setting(selected_sf)
        self.start_playbae()

    def on_loop_checkbox_changed(self):
        if self.loop_chk_var.get():
            self.loop_forever_var.set(False)
        self.play_current_track()

    def on_loop_forever_changed(self):
        if self.loop_forever_var.get():
            self.loop_chk_var.set(False)
        self.play_current_track()

    def on_loop_spinbox_changed(self):
        if self.loop_chk_var.get():
            self.play_current_track()

    def on_loop_settings_changed(self):
        self.play_current_track()

    def on_volume_slide(self, val):
        set_player_volume(float(val), source_player=self)
        try:
            self.volume_var.set(int(float(val)))
            self.volume_label_var.set(f"Volume: {int(float(val))}%")
        except Exception:
            pass
        self.apply_live_volume()

    def apply_live_volume(self):
        if not self.playbae_wpctl_id and self.proc:
            threading.Thread(target=self._resolve_and_apply_volume, daemon=True).start()
            return

        if self.playbae_wpctl_id:
            try:
                percent = max(0, min(150, int(self.volume_var.get())))
                subprocess.run(
                    ["wpctl", "set-volume", str(self.playbae_wpctl_id), f"{percent}%"],
                    check=True,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            except Exception:
                self.playbae_wpctl_id = None
                threading.Thread(target=self._resolve_and_apply_volume, daemon=True).start()

    def _resolve_and_apply_volume(self):
        awk_cmd = "wpctl status | awk '/Streams:/ {in_streams=1; next} in_streams && /playbae/ {sub(/\\.$/, \"\", $1); print $1; exit} in_streams && /^[^[:space:]]/ {exit}'"
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
                        self.playbae_wpctl_id = stream_id
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
                    if self.raw_duration_seconds > 0:
                        pct = int((self.current_position_seconds / self.raw_duration_seconds) * 100)
                        self.seek_pct_var.set(f"{pct}%")
                    self.position_var.set(f"Position: {self.format_time(self.current_position_seconds)}")
                    if self.winfo_exists():
                        self.after(0, lambda: self.progress_bar_widget.update_progress(self.current_position_seconds, self.raw_duration_seconds))
                time.sleep(1.0)
        threading.Thread(target=timer_loop, daemon=True).start()

    def start_playbae(self):
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
        self.playbae_wpctl_id = None

        playbae_bin = shutil.which("playbae") or os.path.join(get_cache_directory(), "playbae")
        ext = os.path.splitext(self.audio_file)[1].lower()
        soundfont_supported = {".mid", ".midi", ".kar", ".xmf", ".mxmf", ".mthc"}

        cmd = [playbae_bin]

        # Handle Soundfont selection
        if ext in soundfont_supported:
            selected_sf = self.soundfont_var.get() if hasattr(self, "soundfont_var") else get_saved_soundfont()
            if selected_sf and selected_sf != "(None)":
                sf_path1 = os.path.expanduser(f"~/.config/joshw/{selected_sf}")
                sf_path2 = os.path.join(get_cache_directory(), selected_sf)
                if os.path.exists(sf_path1):
                    cmd.extend(["-p", sf_path1])
                elif os.path.exists(sf_path2):
                    cmd.extend(["-p", sf_path2])
                else:
                    cmd.extend(["-p", sf_path1]) # Fallback path

        cmd.extend(["-f", self.audio_file])

        if self.loop_forever_var.get():
            cmd.extend(["-l", "-1"])
        elif self.loop_chk_var.get():
            num = self.loop_count_var.get()
            cmd.extend(["-l", str(num)])

        try:
            self.proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            threading.Thread(target=self._resolve_and_apply_volume, daemon=True).start()
        except Exception as e:
            messagebox.showerror("playbae Error", f"Could not launch playbae process:\n\n{e}")
            return

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
        self.position_var.set("Position: 00:00:00")

        ext = os.path.splitext(self.audio_file)[1].lower()
        if ext in {".mid", ".midi", ".kar"}:
            try:
                import mido
                mid = mido.MidiFile(self.audio_file)
                base_duration = float(mid.length)
            except Exception as e:
                print(f"Could not parse MIDI duration with mido: {e}")
                base_duration = 180.0
        else:
            base_duration = 180.0

        if self.loop_forever_var.get():
            self.raw_duration_seconds = base_duration
            self.duration_var.set("Duration: Forever")
        elif self.loop_chk_var.get():
            num = max(1, self.loop_count_var.get())
            self.raw_duration_seconds = base_duration * num
            self.duration_var.set(f"Duration: {self.format_time(self.raw_duration_seconds)}")
        else:
            self.raw_duration_seconds = base_duration
            self.duration_var.set(f"Duration: {self.format_time(self.raw_duration_seconds)}")

        if hasattr(self, "title_label") and self.winfo_exists():
            self.title_label.configure(text=f"Now Playing:\n{display_title}")
        self.title(f"NeoBAE playbae Controller - {display_title}")
        self.track_var.set(f"Track: {self.current_index + 1} / {len(self.playlist)}")
        self.start_playbae()
        self.status_var.set("Playing")
        self.update_listbox_selection()

    def toggle_play_pause(self):
        if not self.proc or self.proc.poll() is not None:
            self.play_current_track()
            return
        try:
            if self.is_paused:
                if hasattr(os, "kill") and hasattr(signal, "SIGCONT"):
                    os.kill(self.proc.pid, signal.SIGCONT)
                self.is_paused = False
                self.status_var.set("Playing")
            else:
                if hasattr(os, "kill") and hasattr(signal, "SIGSTOP"):
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
        self.playbae_wpctl_id = None
        self.status_var.set("Stopped")

    def close(self, event=None):
        try:
            _active_volume_players.discard(self)
        except Exception:
            pass
        self.stop()
        try:
            self.destroy()
        except Exception:
            pass

PlayBAEPlayer = PlayBAEPlayerWindow