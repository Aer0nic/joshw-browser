import json
import os
import re
import shutil
import signal
import socket
import subprocess
import tempfile
import threading
import time
import tkinter as tk
import weakref
from tkinter import ttk, messagebox, filedialog
from config import get_cache_directory, is_emulated_file


_active_volume_players = weakref.WeakSet()
_syncing_player_volume = False


def get_saved_player_volume():
    try:
        with open(os.path.join(get_cache_directory(), "settings.json"), "r", encoding="utf-8") as settings_file:
            settings = json.load(settings_file)
        if not isinstance(settings, dict):
            return 100
        volume = int(settings.get("player_volume", 100))
        return max(0, min(150, volume))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return 100


def register_volume_player(player):
    _active_volume_players.add(player)


def set_player_volume(value):
    global _syncing_player_volume
    if _syncing_player_volume:
        return

    volume = max(0, min(150, int(value)))
    settings_path = os.path.join(get_cache_directory(), "settings.json")
    try:
        try:
            with open(settings_path, "r", encoding="utf-8") as settings_file:
                settings = json.load(settings_file)
            if not isinstance(settings, dict):
                settings = {}
        except (OSError, ValueError, TypeError, json.JSONDecodeError):
            settings = {}
        settings["player_volume"] = volume
        temporary_path = settings_path + ".tmp"
        with open(temporary_path, "w", encoding="utf-8") as settings_file:
            json.dump(settings, settings_file, ensure_ascii=False, indent=2)
        os.replace(temporary_path, settings_path)
    except OSError:
        pass

    _syncing_player_volume = True
    try:
        for player in list(_active_volume_players):
            try:
                if not player.winfo_exists():
                    _active_volume_players.discard(player)
                    continue
                player.volume_var.set(volume)
                player.volume_label_var.set(f"Volume: {volume}%")
                if hasattr(player, "volume_slider"):
                    player.volume_slider.set(volume)
                player.apply_live_volume()
            except (AttributeError, tk.TclError):
                _active_volume_players.discard(player)
    finally:
        _syncing_player_volume = False


class ToolTip:
    def __init__(self, widget, text, delay=500):
        self.widget = widget
        self.text = text
        self.delay = delay
        self.tip_window = None
        self.id = None
        self.widget.bind("<Enter>", self.enter)
        self.widget.bind("<Leave>", self.leave)
        self.widget.bind("<ButtonPress>", self.leave)

    def enter(self, event=None):
        self.schedule()

    def leave(self, event=None):
        self.unschedule()
        self.hide_tip()

    def schedule(self):
        self.unschedule()
        self.id = self.widget.after(self.delay, self.show_tip)

    def unschedule(self):
        id_val = self.id
        self.id = None
        if id_val:
            self.widget.after_cancel(id_val)

    def show_tip(self):
        if self.tip_window or not self.widget.winfo_exists():
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5
        self.tip_window = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")
        label = ttk.Label(
            tw, text=self.text, justify="left",
            background="#ffffe0", relief="solid", borderwidth=1,
            font=("Arial", 9), padding=(4, 2)
        )
        label.pack(ipadx=1)

    def hide_tip(self):
        tw = self.tip_window
        self.tip_window = None
        if tw:
            tw.destroy()


class InteractiveProgressBar:
    """Draggable interactive progress seek bar styled similarly to a scale/slider with live percentage updating."""
    def __init__(self, parent, get_duration_func, seek_func, seek_start_func, seek_release_func, length=380):
        self.parent = parent
        self.get_duration = get_duration_func
        self.seek_to = seek_func
        self.seek_start = seek_start_func
        self.seek_release = seek_release_func
        
        self.frame = ttk.Frame(parent)
        self.canvas = tk.Canvas(self.frame, width=length, height=18, bg="#e0e0e0", highlightthickness=1, relief="sunken")
        self.canvas.pack(fill="x", expand=True)
        
        self.progress_rect = self.canvas.create_rectangle(0, 0, 0, 18, fill="#3a7ebf", outline="")
        self.thumb = self.canvas.create_rectangle(0, 0, 6, 18, fill="#204a87", outline="")
        
        self.is_dragging = False
        self.current_pct = 0.0
        self.tooltip_win = None
        
        self.canvas.bind("<Button-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.canvas.bind("<Motion>", self.on_mouse_motion)
        self.canvas.bind("<Leave>", self.on_mouse_leave)

    def format_time_short(self, seconds):
        if seconds is None or seconds < 0:
            return "00:00"
        m = int(seconds) // 60
        s = int(seconds) % 60
        return f"{m}:{s:02d}"

    def update_progress(self, current_seconds, duration_seconds):
        if self.is_dragging or not self.canvas.winfo_exists():
            return
        width = self.canvas.winfo_width()
        if width <= 1:
            width = 380
        
        if duration_seconds and duration_seconds > 0:
            ratio = max(0.0, min(1.0, current_seconds / duration_seconds))
        else:
            ratio = 0.0
            
        self.current_pct = ratio
        fill_width = int(width * ratio)
        self.canvas.coords(self.progress_rect, 0, 0, fill_width, 18)
        thumb_x = max(0, fill_width - 3)
        self.canvas.coords(self.thumb, thumb_x, 0, thumb_x + 6, 18)

    def calculate_pos_from_event(self, event):
        width = self.canvas.winfo_width()
        if width <= 1:
            width = 380
        x = max(0, min(event.x, width))
        pct = (x / width) if width > 0 else 0.0
        return pct, x

    def on_press(self, event):
        self.is_dragging = True
        if self.seek_start:
            self.seek_start()
        pct, _ = self.calculate_pos_from_event(event)
        self.update_visuals_pct(pct)

    def on_drag(self, event):
        if self.is_dragging:
            pct, _ = self.calculate_pos_from_event(event)
            self.update_visuals_pct(pct)

    def on_release(self, event):
        if self.is_dragging:
            self.is_dragging = False
            pct, _ = self.calculate_pos_from_event(event)
            dur = self.get_duration()
            if dur and dur > 0:
                target_time = pct * dur
                self.seek_to(target_time)
            if self.seek_release:
                self.seek_release()

    def update_visuals_pct(self, pct):
        self.current_pct = pct
        width = self.canvas.winfo_width()
        if width <= 1:
            width = 380
        fill_width = int(width * pct)
        self.canvas.coords(self.progress_rect, 0, 0, fill_width, 18)
        thumb_x = max(0, fill_width - 3)
        self.canvas.coords(self.thumb, thumb_x, 0, thumb_x + 6, 18)

    def on_mouse_motion(self, event):
        pct, x = self.calculate_pos_from_event(event)
        dur = self.get_duration()
        time_at_cursor = pct * dur if dur and dur > 0 else 0.0
        
        pct_str = f"{int(pct * 100)}%"
        time_str = self.format_time_short(time_at_cursor)
        tip_text = f"{pct_str} - {time_str}"
        
        self.show_tooltip(event, tip_text)

    def on_mouse_leave(self, event):
        self.hide_tooltip()

    def show_tooltip(self, event, text):
        self.hide_tooltip()
        root_x = event.x_root + 10
        root_y = event.y_root + 20
        self.tooltip_win = tw = tk.Toplevel(self.canvas)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{root_x}+{root_y}")
        label = ttk.Label(tw, text=text, background="#ffffe0", relief="solid", borderwidth=1, font=("Arial", 9), padding=(3, 1))
        label.pack()

    def hide_tooltip(self):
        if self.tooltip_win:
            try:
                self.tooltip_win.destroy()
            except Exception:
                pass
            self.tooltip_win = None


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


def get_playgsf_path():
    for p in [os.path.join(get_cache_directory(), "playgsf"), shutil.which("playgsf"), "/usr/local/bin/playgsf", "/usr/bin/playgsf"]:
        if p and os.path.exists(p):
            return p
    return os.path.join(get_cache_directory(), "playgsf")


def get_2sfplay_path():
    for p in [os.path.join(get_cache_directory(), "2sfPlay"), shutil.which("2sfPlay"), "/usr/local/bin/2sfPlay", "/usr/bin/2sfPlay"]:
        if p and os.path.exists(p):
            return p
    return os.path.join(get_cache_directory(), "2sfPlay")


def get_next_track_index(current_index, track_count, loop_forever):
    if loop_forever:
        return current_index if track_count else None
    next_index = current_index + 1
    if next_index < track_count:
        return next_index
    return None


def get_midi_info(file_path):
    try:
        with open(file_path, "rb") as f:
            header = f.read(14)
            if not header.startswith(b"MThd"):
                return 180.0, 480, 500000
            num_tracks = int.from_bytes(header[10:12], "big")
            division = int.from_bytes(header[12:14], "big")
            if division & 0x8000:
                return 180.0, 480, 500000
            ticks_per_quarter = division
            default_tempo = 500000
            max_duration_us = 0
            
            for _ in range(num_tracks):
                trk_header = f.read(8)
                if len(trk_header) < 8:
                    break
                if not trk_header.startswith(b"MTrk"):
                    chunk_len = int.from_bytes(trk_header[4:8], "big")
                    f.read(chunk_len)
                    continue
                chunk_len = int.from_bytes(trk_header[4:8], "big")
                chunk_data = f.read(chunk_len)
                
                pos = 0
                current_tick = 0
                current_tempo = default_tempo
                track_us = 0
                
                def read_vlq(data, p):
                    val = 0
                    while p < len(data):
                        b = data[p]
                        p += 1
                        val = (val << 7) | (b & 0x7F)
                        if not (b & 0x80):
                            break
                    return val, p

                while pos < len(chunk_data):
                    delta, pos = read_vlq(chunk_data, pos)
                    current_tick += delta
                    track_us += delta * (current_tempo / ticks_per_quarter)
                    if pos >= len(chunk_data):
                        break
                    status = chunk_data[pos]
                    if status == 0xFF:
                        pos += 1
                        if pos >= len(chunk_data): break
                        meta_type = chunk_data[pos]
                        pos += 1
                        length, pos = read_vlq(chunk_data, pos)
                        if meta_type == 0x51 and length == 3:
                            if pos + 3 <= len(chunk_data):
                                current_tempo = int.from_bytes(chunk_data[pos:pos+3], "big")
                        pos += length
                    elif status == 0xF0 or status == 0xF7:
                        pos += 1
                        length, pos = read_vlq(chunk_data, pos)
                        pos += length
                    else:
                        if status & 0x80:
                            cmd = status & 0xF0
                            pos += 1
                        if cmd in (0x80, 0x90, 0xA0, 0xB0, 0xE0):
                            pos += 2
                        elif cmd in (0xc0, 0xd0):
                            pos += 1
                if track_us > max_duration_us:
                    max_duration_us = track_us
            
            dur = (max_duration_us / 1000000.0) if max_duration_us > 0 else 180.0
            return dur, ticks_per_quarter, default_tempo
    except Exception:
        pass
    return 180.0, 480, 500000


class SIDPlayerWindow(tk.Toplevel):
    def __init__(self, parent, audio_file):
        super().__init__(parent)
        self.parent = parent
        self.audio_file = audio_file
        self.sid_proc = None
        self.is_paused = False
        self.subtracks = [1]
        self.current_subtrack_idx = 0
        self.last_displayed_text = ""
        self.current_position_seconds = 0.0
        self.total_duration_seconds = 180.0
        self.is_dragging_volume = False
        self.volume_update_id = None
        self.loop_forever_var = tk.BooleanVar(value=False)

        self.status_var = tk.StringVar(value="Playing")
        self.track_var = tk.StringVar()
        self.seek_pct_var = tk.StringVar(value="0%")
        saved_volume = get_saved_player_volume()
        self.volume_var = tk.IntVar(value=saved_volume)
        self.volume_label_var = tk.StringVar(value=f"Volume: {saved_volume}%")
        register_volume_player(self)

        self.parse_sid_subtracks()
        self.build_ui()
        self.protocol("WM_DELETE_WINDOW", self.close)
        
        self.start_sidplay()
        self.start_timer_thread()

    def parse_sid_subtracks(self):
        try:
            with open(self.audio_file, "rb") as f:
                header = f.read(0x7e)
                if header.startswith(b'PSID') or header.startswith(b'RSID'):
                    songs_count = int.from_bytes(header[0x0e:0x10], byteorder='big')
                    if songs_count > 1:
                        self.subtracks = list(range(1, songs_count + 1))
        except Exception:
            pass

    def build_ui(self):
        filename = os.path.basename(self.audio_file)
        self.title(f"sidplayfp Controller - {filename}")
        self.geometry("560x610")
        self.minsize(520, 540)
        self.transient(self.parent)

        padding = ttk.Frame(self, padding=12)
        padding.pack(fill="both", expand=True)

        browse_frame = ttk.Frame(padding)
        browse_frame.pack(side="top", anchor="w", pady=(0, 6))

        browse_btn = tk.Button(browse_frame, text="📁", font=("Arial", 12), width=3, command=self.on_browse_clicked)
        browse_btn.pack(side="top", anchor="w")
        ToolTip(browse_btn, "Find and play a local file", delay=500)

        browse_lbl = ttk.Label(browse_frame, text="Browse", font=("Arial", 8))
        browse_lbl.pack(side="top", anchor="w")

        ttk.Label(padding, text=f"Now Playing (SID):\n{filename}", font=("Arial", 9, "bold"), justify="center", wraplength=520).pack(pady=(0, 6))
        
        info_frame = ttk.Frame(padding)
        info_frame.pack(fill="x", pady=(0, 4))
        
        status_sub_frame = ttk.Frame(info_frame)
        status_sub_frame.pack(side="left", padx=5)
        ttk.Label(status_sub_frame, textvariable=self.status_var, font=("Arial", 9)).pack(side="left")
        ttk.Label(status_sub_frame, text=" (", font=("Arial", 9)).pack(side="left")
        ttk.Label(status_sub_frame, textvariable=self.seek_pct_var, font=("Arial", 9, "bold")).pack(side="left")
        ttk.Label(status_sub_frame, text=")", font=("Arial", 9)).pack(side="left")

        ttk.Label(info_frame, textvariable=self.track_var, font=("Arial", 9, "bold")).pack(side="right", padx=5)
        self.update_track_label()

        progress_container = ttk.Frame(padding)
        progress_container.pack(fill="x", pady=(4, 8))
        self.progress_bar_widget = InteractiveProgressBar(
            progress_container,
            get_duration_func=lambda: self.total_duration_seconds,
            seek_func=self.seek_to_position,
            seek_start_func=lambda: None,
            seek_release_func=lambda: None
        )
        self.progress_bar_widget.frame.pack(fill="x", expand=True)

        vol_frame = ttk.Frame(padding)
        vol_frame.pack(fill="x", pady=(0, 8))
        ttk.Label(vol_frame, textvariable=self.volume_label_var, font=("Arial", 9)).pack(side="left", padx=5)
        self.volume_slider = ttk.Scale(vol_frame, from_=0, to=150, orient="horizontal", command=self.on_volume_slide)
        self.volume_slider.set(self.volume_var.get())
        self.volume_slider.pack(side="right", fill="x", expand=True, padx=5)
        self.volume_slider.bind("<ButtonPress-1>", self.on_volume_press)
        self.volume_slider.bind("<ButtonRelease-1>", self.on_volume_release)

        term_container = ttk.LabelFrame(padding, text="sidplayfp Track Status", padding=4)
        term_container.pack(fill="both", expand=True, pady=(4, 8))

        log_frame = ttk.Frame(term_container)
        log_frame.pack(fill="both", expand=True)

        self.log_text = tk.Text(log_frame, wrap="word", bg="black", fg="yellow", font=("Monospace", 10), state="disabled", height=8)
        self.log_text.pack(side="left", fill="both", expand=True)
        
        scrollbar = ttk.Scrollbar(log_frame, orient="vertical", command=self.log_text.yview)
        scrollbar.pack(side="right", fill="y")
        self.log_text.configure(yscrollcommand=scrollbar.set)

        list_frame = ttk.LabelFrame(padding, text="Subtracks Playlist", padding=6)
        list_frame.pack(fill="x", pady=(0, 8))

        self.subtrack_listbox = tk.Listbox(list_frame, font=("Arial", 9), selectmode="single", exportselection=False, height=3)
        self.subtrack_listbox.pack(side="left", fill="both", expand=True)
        list_scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.subtrack_listbox.yview)
        list_scrollbar.pack(side="right", fill="y")
        self.subtrack_listbox.configure(yscrollcommand=list_scrollbar.set)
        self.subtrack_listbox.bind("<<ListboxSelect>>", self.on_listbox_select)
        self.populate_subtrack_listbox()

        controls = ttk.Frame(padding)
        controls.pack(pady=4)

        ttk.Button(controls, text="<< Prev", width=10, command=self.prev_track).grid(row=0, column=0, padx=4)
        ttk.Button(controls, text="Play/Pause", width=12, command=self.toggle_play_pause).grid(row=0, column=1, padx=4)
        ttk.Button(controls, text="Next >>", width=10, command=self.next_track).grid(row=0, column=2, padx=4)
        ttk.Button(controls, text="Close", width=10, command=self.close).grid(row=0, column=3, padx=(16, 4))
        ttk.Checkbutton(controls, text="Loop forever", variable=self.loop_forever_var).grid(row=1, column=0, columnspan=4, pady=(6, 0))

    def on_volume_press(self, event):
        self.is_dragging_volume = True

    def on_volume_release(self, event):
        self.is_dragging_volume = False
        set_player_volume(self.volume_slider.get())

    def on_volume_slide(self, val):
        set_player_volume(float(val))

    def queue_sid_volume_update(self):
        if self.volume_update_id is None:
            self.volume_update_id = self.after(75, self.apply_sid_volume)

    def apply_live_volume(self):
        self.queue_sid_volume_update()

    def apply_sid_volume(self):
        self.volume_update_id = None
        if not self.sid_proc or self.sid_proc.poll() is not None:
            return

        pactl_bin = shutil.which("pactl")
        if not pactl_bin:
            return

        try:
            result = subprocess.run(
                [pactl_bin, "--format=json", "list", "sink-inputs"],
                check=True,
                capture_output=True,
                text=True,
                timeout=2
            )
            sink_inputs = json.loads(result.stdout)
            process_id = str(self.sid_proc.pid)
            volume = f"{self.volume_var.get()}%"
            for sink_input in sink_inputs:
                properties = sink_input.get("properties", {})
                if properties.get("application.process.id") == process_id:
                    subprocess.run(
                        [pactl_bin, "set-sink-input-volume", str(sink_input["index"]), volume],
                        check=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=2
                    )
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
            pass

    def start_timer_thread(self):
        def timer_loop():
            while self.winfo_exists():
                if self.status_var.get() == "Playing":
                    self.current_position_seconds += 1.0
                    if self.total_duration_seconds > 0:
                        pct = int((self.current_position_seconds / self.total_duration_seconds) * 100)
                        self.seek_pct_var.set(f"{pct}%")
                    if self.winfo_exists():
                        self.after(0, lambda: self.progress_bar_widget.update_progress(self.current_position_seconds, self.total_duration_seconds))
                time.sleep(1.0)
        threading.Thread(target=timer_loop, daemon=True).start()

    def seek_to_position(self, target_time):
        self.current_position_seconds = target_time
        if self.total_duration_seconds > 0:
            pct = int((self.current_position_seconds / self.total_duration_seconds) * 100)
            self.seek_pct_var.set(f"{pct}%")
        self.progress_bar_widget.update_progress(self.current_position_seconds, self.total_duration_seconds)
        self.start_sidplay(preserve_time=True)

    def on_browse_clicked(self):
        parent = self.parent
        initial_dir = getattr(parent, "_last_loaded_music_dir", None) or parent.default_load_music_dir.get()
        file_path = filedialog.askopenfilename(
            parent=self,
            title="Select Music File",
            initialdir=initial_dir,
            filetypes=[
                ("Audio & Emulated Files", "*.ay *.gbs *.gym *.hes *.kss *.nsf *.nsfe *.sap *.spc *.vgm *.vgz *.usf *.miniusf *.gsf *.minigsf *.sid *.adx *.mp3 *.ogg *.flac *.wav *.m4a *.aac *.opus *.wma *.ape *.wv *.mod *.s3m *.xm *.it *.mka *.tta *.ac3 *.dts *.aiff *.au *.ra *.mid *.MID *.xma *.XMA"),
                ("All Files", "*.*")
            ]
        )
        if file_path:
            folder = os.path.dirname(file_path)
            parent._last_loaded_music_dir = folder
            self.close()
            parent.play_audio_file(file_path)

    def populate_subtrack_listbox(self):
        if not self.winfo_exists():
            return
        self.subtrack_listbox.delete(0, tk.END)
        for idx, s in enumerate(self.subtracks, start=1):
            self.subtrack_listbox.insert(tk.END, f"Track {idx}")
        self.update_listbox_selection()

    def update_listbox_selection(self):
        if not self.winfo_exists():
            return
        try:
            self.subtrack_listbox.selection_clear(0, tk.END)
            if 0 <= self.current_subtrack_idx < self.subtrack_listbox.size():
                self.subtrack_listbox.selection_set(self.current_subtrack_idx)
                self.subtrack_listbox.see(self.current_subtrack_idx)
        except Exception:
            pass

    def on_listbox_select(self, event):
        selection = self.subtrack_listbox.curselection()
        if not selection:
            return
        idx = selection[0]
        if idx != self.current_subtrack_idx:
            self.current_subtrack_idx = idx
            self.start_sidplay()

    def update_track_label(self):
        curr_sub = self.subtracks[self.current_subtrack_idx]
        self.track_var.set(f"Subtrack: {curr_sub} / {len(self.subtracks)}")

    def update_textbox(self, text):
        if not self.winfo_exists() or text == self.last_displayed_text:
            return
        self.last_displayed_text = text
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.insert(tk.END, text)
        self.log_text.configure(state="disabled")

    def stop_current_playback(self):
        if self.sid_proc:
            try:
                self.sid_proc.terminate()
                self.sid_proc.wait(timeout=1)
            except Exception:
                try:
                    self.sid_proc.kill()
                except Exception:
                    pass
            self.sid_proc = None

    def start_sidplay(self, preserve_time=False):
        self.stop_current_playback()
        if not preserve_time:
            self.current_position_seconds = 0.0

        sidplay_bin = shutil.which("sidplayfp") or "sidplayfp"
        subtrack_num = self.subtracks[self.current_subtrack_idx]
        
        env = os.environ.copy()
        env["SDL_VIDEODRIVER"] = "dummy"

        cmd_args = [sidplay_bin]
        if len(self.subtracks) > 1:
            cmd_args.append(f"-o{subtrack_num}")
        
        if self.current_position_seconds > 0:
            minutes = int(self.current_position_seconds // 60)
            seconds = self.current_position_seconds % 60
            cmd_args.append(f"-b{minutes}:{seconds:06.3f}")

        cmd_args.append(self.audio_file)

        try:
            self.sid_proc = subprocess.Popen(
                cmd_args,
                env=env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1
            )
            self.queue_sid_volume_update()
            self.is_paused = False
            self.status_var.set("Playing")
            self.update_track_label()
            self.update_listbox_selection()
            threading.Thread(target=self.read_sid_output, daemon=True).start()
        except Exception as e:
            messagebox.showerror("sidplayfp Error", f"Could not launch sidplayfp:\n\n{e}")

    def read_sid_output(self):
        proc = self.sid_proc
        if not proc or not proc.stdout:
            return
        
        title = "UNKNOWN"
        author = "UNKNOWN"
        released = "UNKNOWN"
        song_length = "UNKNOWN"
        playtime = "00:00"

        buffer = ""
        try:
            while proc.poll() is None:
                char = proc.stdout.read(1)
                if not char:
                    break
                if char == '\r' or char == '\n':
                    line = buffer.strip().replace(" [0m", "")
                    buffer = ""
                    if line:
                        if "Title" in line and ":" in line:
                            title = line.split(":", 1)[1].strip()
                        elif "Author" in line and ":" in line:
                            author = line.split(":", 1)[1].strip()
                        elif "Released" in line and ":" in line:
                            released = line.split(":", 1)[1].strip()
                        elif "Song Length" in line and ":" in line:
                            song_length = line.split(":", 1)[1].strip()
                            try:
                                parts = song_length.split(":")
                                if len(parts) == 2:
                                    self.total_duration_seconds = int(parts[0]) * 60 + float(parts[1])
                            except Exception:
                                pass
                        
                        m_time = re.search(r'(\d{2}:\d{2})', line)
                        if m_time:
                            playtime = m_time.group(1)

                        current_playlist_str = f"{self.subtracks[self.current_subtrack_idx]}/{len(self.subtracks)}"
                        formatted_text = (
                            f"Title: {title}\n"
                            f"Author: {author}\n"
                            f"Released: {released}\n\n"
                            f"Playlist: {current_playlist_str}\n"
                            f"Song Length: {song_length}\n\n"
                            f"Playtime: {playtime}"
                        )
                        if self.winfo_exists():
                            self.after(0, lambda t=formatted_text: self.update_textbox(t))
                else:
                    buffer += char
        except Exception:
            pass

        return_code = proc.wait()
        if not self.winfo_exists() or self.sid_proc is not proc or self.status_var.get() != "Playing":
            return
        if return_code != 0:
            self.status_var.set("Playback Error")
            return
        next_index = get_next_track_index(
            self.current_subtrack_idx,
            len(self.subtracks),
            self.loop_forever_var.get()
        )
        if next_index is None:
            self.sid_proc = None
            self.status_var.set("Stopped")
            return
        self.current_subtrack_idx = next_index
        self.start_sidplay()

    def toggle_play_pause(self):
        if not self.sid_proc:
            return
        try:
            if self.is_paused:
                os.kill(self.sid_proc.pid, signal.SIGCONT)
                self.is_paused = False
                self.status_var.set("Playing")
            else:
                os.kill(self.sid_proc.pid, signal.SIGSTOP)
                self.is_paused = True
                self.status_var.set("Paused")
        except Exception:
            pass

    def prev_track(self):
        if len(self.subtracks) > 1:
            self.current_subtrack_idx = (self.current_subtrack_idx - 1) % len(self.subtracks)
            self.start_sidplay()

    def next_track(self):
        if len(self.subtracks) > 1:
            self.current_subtrack_idx = (self.current_subtrack_idx + 1) % len(self.subtracks)
            self.start_sidplay()

    def close(self):
        self.stop_current_playback()
        try:
            self.destroy()
        except Exception:
            pass


class GSFPlayerWindow(tk.Toplevel):
    def __init__(self, parent, audio_file):
        super().__init__(parent)
        self.parent = parent
        self.audio_file = audio_file
        self.gsf_proc = None
        self.is_paused = False
        self.playlist = []
        self.current_index = 0
        self.last_displayed_text = ""
        self.current_position_seconds = 0.0
        self.total_duration_seconds = 180.0
        self.is_dragging_volume = False
        self.volume_update_id = None
        self.loop_forever_var = tk.BooleanVar(value=False)

        self.status_var = tk.StringVar(value="Playing")
        self.track_var = tk.StringVar()
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

    def load_playlist_files(self):
        folder = os.path.dirname(self.audio_file)
        self.playlist = []
        try:
            for f in sorted(os.listdir(folder)):
                if f.lower().endswith((".gsf", ".minigsf")):
                    self.playlist.append(os.path.join(folder, f))
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
        self.title(f"playgsf Controller - {filename}")
        self.geometry("560x630")
        self.minsize(520, 560)
        self.transient(self.parent)

        padding = ttk.Frame(self, padding=12)
        padding.pack(fill="both", expand=True)

        browse_frame = ttk.Frame(padding)
        browse_frame.pack(side="top", anchor="w", pady=(0, 6))

        browse_btn = tk.Button(browse_frame, text="📁", font=("Arial", 12), width=3, command=self.on_browse_clicked)
        browse_btn.pack(side="top", anchor="w")
        ToolTip(browse_btn, "Find and play a local file", delay=500)

        browse_lbl = ttk.Label(browse_frame, text="Browse", font=("Arial", 8))
        browse_lbl.pack(side="top", anchor="w")

        ttk.Label(padding, text=f"Now Playing (GSF):\n{filename}", font=("Arial", 9, "bold"), justify="center", wraplength=520).pack(pady=(0, 6))
        
        info_frame = ttk.Frame(padding)
        info_frame.pack(fill="x", pady=(0, 4))
        
        status_sub_frame = ttk.Frame(info_frame)
        status_sub_frame.pack(side="left", padx=5)
        ttk.Label(status_sub_frame, textvariable=self.status_var, font=("Arial", 9)).pack(side="left")
        ttk.Label(status_sub_frame, text=" (", font=("Arial", 9)).pack(side="left")
        ttk.Label(status_sub_frame, textvariable=self.seek_pct_var, font=("Arial", 9, "bold")).pack(side="left")
        ttk.Label(status_sub_frame, text=")", font=("Arial", 9)).pack(side="left")

        ttk.Label(info_frame, textvariable=self.track_var, font=("Arial", 9, "bold")).pack(side="right", padx=5)
        self.update_track_label()

        progress_container = ttk.Frame(padding)
        progress_container.pack(fill="x", pady=(4, 8))
        self.progress_bar_widget = InteractiveProgressBar(
            progress_container,
            get_duration_func=lambda: self.total_duration_seconds,
            seek_func=self.seek_to_position,
            seek_start_func=lambda: None,
            seek_release_func=lambda: None
        )
        self.progress_bar_widget.frame.pack(fill="x", expand=True)

        vol_frame = ttk.Frame(padding)
        vol_frame.pack(fill="x", pady=(0, 8))
        ttk.Label(vol_frame, textvariable=self.volume_label_var, font=("Arial", 9)).pack(side="left", padx=5)
        self.volume_slider = ttk.Scale(vol_frame, from_=0, to=150, orient="horizontal", command=self.on_volume_slide)
        self.volume_slider.set(self.volume_var.get())
        self.volume_slider.pack(side="right", fill="x", expand=True, padx=5)
        self.volume_slider.bind("<ButtonPress-1>", self.on_volume_press)
        self.volume_slider.bind("<ButtonRelease-1>", self.on_volume_release)

        term_container = ttk.LabelFrame(padding, text="playgsf Track Status", padding=4)
        term_container.pack(fill="both", expand=True, pady=(4, 8))

        log_frame = ttk.Frame(term_container)
        log_frame.pack(fill="both", expand=True)

        self.log_text = tk.Text(log_frame, wrap="word", bg="black", fg="yellow", font=("Monospace", 9), state="disabled", height=10)
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

        controls = ttk.Frame(padding)
        controls.pack(pady=4)

        ttk.Button(controls, text="<< Prev", width=10, command=self.prev_track).grid(row=0, column=0, padx=4)
        ttk.Button(controls, text="Play/Pause", width=12, command=self.toggle_play_pause).grid(row=0, column=1, padx=4)
        ttk.Button(controls, text="Next >>", width=10, command=self.next_track).grid(row=0, column=2, padx=4)
        ttk.Button(controls, text="Close", width=10, command=self.close).grid(row=0, column=3, padx=(16, 4))
        ttk.Checkbutton(controls, text="Loop forever", variable=self.loop_forever_var).grid(row=1, column=0, columnspan=4, pady=(6, 0))

    def on_volume_press(self, event):
        self.is_dragging_volume = True

    def on_volume_release(self, event):
        self.is_dragging_volume = False
        set_player_volume(self.volume_slider.get())

    def on_volume_slide(self, val):
        set_player_volume(float(val))

    def queue_gsf_volume_update(self):
        if self.volume_update_id is None:
            self.volume_update_id = self.after(75, self.apply_gsf_volume)

    def apply_live_volume(self):
        self.queue_gsf_volume_update()

    def apply_gsf_volume(self):
        self.volume_update_id = None
        if not self.gsf_proc or self.gsf_proc.poll() is not None:
            return

        pactl_bin = shutil.which("pactl")
        if not pactl_bin:
            return

        try:
            result = subprocess.run(
                [pactl_bin, "--format=json", "list", "sink-inputs"],
                check=True,
                capture_output=True,
                text=True,
                timeout=2
            )
            sink_inputs = json.loads(result.stdout)
            process_id = str(self.gsf_proc.pid)
            volume = f"{self.volume_var.get()}%"
            for sink_input in sink_inputs:
                properties = sink_input.get("properties", {})
                if properties.get("application.process.id") == process_id:
                    subprocess.run(
                        [pactl_bin, "set-sink-input-volume", str(sink_input["index"]), volume],
                        check=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=2
                    )
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
            pass

    def start_timer_thread(self):
        def timer_loop():
            while self.winfo_exists():
                if self.status_var.get() == "Playing":
                    self.current_position_seconds += 1.0
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
        if self.total_duration_seconds > 0:
            pct = int((self.current_position_seconds / self.total_duration_seconds) * 100)
            self.seek_pct_var.set(f"{pct}%")
        self.progress_bar_widget.update_progress(self.current_position_seconds, self.total_duration_seconds)
        self.start_playgsf(preserve_time=True)

    def on_browse_clicked(self):
        parent = self.parent
        initial_dir = getattr(parent, "_last_loaded_music_dir", None) or parent.default_load_music_dir.get()
        file_path = filedialog.askopenfilename(
            parent=self,
            title="Select Music File",
            initialdir=initial_dir,
            filetypes=[
                ("Audio & Emulated Files", "*.ay *.gbs *.gym *.hes *.kss *.nsf *.nsfe *.sap *.spc *.vgm *.vgz *.usf *.miniusf *.gsf *.minigsf *.sid *.adx *.mp3 *.ogg *.flac *.wav *.m4a *.aac *.opus *.wma *.ape *.wv *.mod *.s3m *.xm *.it *.mka *.tta *.ac3 *.dts *.aiff *.au *.ra *.mid *.MID *.xma *.XMA *.2sf *.mini2sf"),
                ("All Files", "*.*")
            ]
        )
        if file_path:
            folder = os.path.dirname(file_path)
            parent._last_loaded_music_dir = folder
            self.close()
            parent.play_audio_file(file_path)

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
        if self.gsf_proc:
            try:
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
        
        vol_gain = max(0, min(200, self.volume_var.get())) / 100.0
        env["SDL_AUDIO_VOLUME"] = str(vol_gain)

        cmd_args = [playgsf_bin]
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
            self.queue_gsf_volume_update()
            self.is_paused = False
            self.status_var.set("Playing")
            self.update_track_label()
            self.update_listbox_selection()
            threading.Thread(target=self.read_gsf_output, args=(self.gsf_proc,), daemon=True).start()
        except Exception as e:
            messagebox.showerror("playgsf Error", f"Could not launch playgsf:\n\n{e}")

    def read_gsf_output(self, proc):
        if not proc or not proc.stdout:
            return
        
        info = {
            "Filename": os.path.basename(self.audio_file),
            "Channels": "UNKNOWN",
            "Sample rate": "UNKNOWN",
            "Title": "UNKNOWN",
            "Artist": "UNKNOWN",
            "Game": "UNKNOWN",
            "Year": "UNKNOWN",
            "Copyright": "UNKNOWN",
            "GSF By": "UNKNOWN",
            "Tagger": "UNKNOWN",
            "Fade": "UNKNOWN",
            "Length": "UNKNOWN",
            "Time": "00:00",
            "GBA Cpu": "UNKNOWN"
        }

        def hide_win():
            if proc.poll() is None:
                try:
                    subprocess.run(["xdotool", "search", "--pid", str(proc.pid), "windowunmap"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    subprocess.run(["xdotool", "search", "--pid", str(proc.pid), "windowlower"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                except Exception:
                    pass
        self.after(300, hide_win)

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
                    key = parts[0].strip().lower()
                    val = parts[1].strip()

                    if "channels" in key:
                        info["Channels"] = val
                    elif "sample rate" in key:
                        info["Sample rate"] = val
                    elif "title" in key:
                        info["Title"] = val
                    elif "artist" in key:
                        info["Artist"] = val
                    elif "game" in key:
                        info["Game"] = val
                    elif "year" in key:
                        info["Year"] = val
                    elif "copyright" in key:
                        info["Copyright"] = val
                    elif "gsf by" in key:
                        info["GSF By"] = val
                    elif "tagger" in key:
                        info["Tagger"] = val
                    elif "fade" in key:
                        info["Fade"] = val
                    elif "length" in key:
                        info["Length"] = val
                        try:
                            parts = val.split(":")
                            if len(parts) == 2:
                                self.total_duration_seconds = int(parts[0]) * 60 + float(parts[1])
                        except Exception:
                            pass
                    elif "time" in key:
                        time_val = val.split()[0] if val else val
                        info["Time"] = time_val
                        if "gba cpu" in line_str.lower() or "cpu" in line_str.lower():
                            sub_parts = line_str.split("GBA Cpu:")
                            if len(sub_parts) > 1:
                                info["GBA Cpu"] = sub_parts[1].strip()
                    elif "gba cpu" in key or "cpu" in key:
                        info["GBA Cpu"] = val

                formatted_text = (
                    f"Filename: {info['Filename']}\n"
                    f"Channels: {info['Channels']}\n"
                    f"Sample rate: {info['Sample rate']}\n"
                    f"Title: {info['Title']}\n"
                    f"Artist: {info['Artist']}\n"
                    f"Game: {info['Game']}\n"
                    f"Year: {info['Year']}\n"
                    f"Copyright: {info['Copyright']}\n"
                    f"GSF By: {info['GSF By']}\n"
                    f"Tagger: {info['Tagger']}\n"
                    f"Fade: {info['Fade']}\n"
                    f"Length: {info['Length']}\n"
                    f"Time: {info['Time']}\n"
                    f"GBA Cpu: {info['GBA Cpu']}"
                )
                if self.winfo_exists():
                    self.after(0, lambda t=formatted_text: self.update_textbox(t))
        except Exception:
            pass

        return_code = proc.wait()
        if self.winfo_exists() and self.gsf_proc == proc:
            if return_code == 0 and self.status_var.get() == "Playing":
                next_index = get_next_track_index(
                    self.current_index,
                    len(self.playlist),
                    self.loop_forever_var.get()
                )
                if next_index is None:
                    self.gsf_proc = None
                    self.status_var.set("Stopped")
                else:
                    self.current_index = next_index
                    self.after(0, self.start_playgsf)
            elif return_code is not None and return_code != 0:
                self.after(0, lambda: self.status_var.set("Playback Error"))

    def toggle_play_pause(self):
        if not self.gsf_proc:
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
            self.start_playgsf()

    def next_track(self):
        if len(self.playlist) > 1:
            self.current_index = (self.current_index + 1) % len(self.playlist)
            self.start_playgsf()

    def close(self):
        self.stop_current_playback()
        try:
            self.destroy()
        except Exception:
            pass


class TwoSFPlayerWindow(tk.Toplevel):
    def __init__(self, parent, audio_file):
        super().__init__(parent)
        self.parent = parent
        self.audio_file = audio_file
        self.proc = None
        self.is_paused = False
        self.playlist = []
        self.current_index = 0
        self.current_position_seconds = 0.0
        self.total_duration_seconds = 180.0
        self.is_dragging_volume = False
        self.is_scrubbing = False
        self.volume_update_id = None
        self.track_repeat_count = 0

        is_emu = is_emulated_file(self.audio_file)
        self.loop_chk_var = tk.BooleanVar(value=is_emu)
        self.loop_count_var = tk.IntVar(value=1 if is_emu else 1)
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
        self.protocol("WM_DELETE_WINDOW", self.close)

        if self.playlist:
            self.play_current_track()
            self.start_timer_thread()

    def format_time(self, seconds):
        if seconds is None or seconds < 0:
            return "00:00:00"
        total_seconds = int(seconds)
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        secs = total_seconds % 60
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    def load_playlist_files(self):
        folder = os.path.dirname(self.audio_file)
        self.playlist = []
        try:
            for f in sorted(os.listdir(folder)):
                if f.lower().endswith((".mini2sf", ".2sf")):
                    self.playlist.append(os.path.join(folder, f))
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
        ToolTip(browse_btn, "Find and play a local file", delay=500)

        browse_lbl = ttk.Label(browse_frame, text="Browse", font=("Arial", 8))
        browse_lbl.pack(side="top", anchor="w")

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
            seek_start_func=self.on_seek_start,
            seek_release_func=self.on_seek_release
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
        self.volume_slider.bind("<ButtonPress-1>", self.on_volume_press)
        self.volume_slider.bind("<ButtonRelease-1>", self.on_volume_release)

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

    def on_seek_start(self):
        self.is_scrubbing = True

    def on_seek_release(self):
        self.is_scrubbing = False

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
        parent = self.parent
        initial_dir = getattr(parent, "_last_loaded_music_dir", None) or parent.default_load_music_dir.get()
        file_path = filedialog.askopenfilename(
            parent=self,
            title="Select Music File",
            initialdir=initial_dir,
            filetypes=[
                ("Audio & Emulated Files", "*.ay *.gbs *.gym *.hes *.kss *.nsf *.nsfe *.sap *.spc *.vgm *.vgz *.usf *.miniusf *.gsf *.minigsf *.sid *.adx *.mp3 *.ogg *.flac *.wav *.m4a *.aac *.opus *.wma *.ape *.wv *.mod *.s3m *.xm *.it *.mka *.tta *.ac3 *.dts *.aiff *.au *.ra *.mid *.MID *.xma *.XMA *.2sf *.mini2sf"),
                ("All Files", "*.*")
            ]
        )
        if file_path:
            folder = os.path.dirname(file_path)
            parent._last_loaded_music_dir = folder
            self.close()
            parent.play_audio_file(file_path)

    def on_loop_settings_changed(self):
        self.start_2sfplay(preserve_time=True)

    def on_volume_press(self, event):
        self.is_dragging_volume = True

    def on_volume_release(self, event):
        self.is_dragging_volume = False
        set_player_volume(self.volume_slider.get())

    def on_volume_slide(self, val):
        set_player_volume(float(val))

    def queue_2sf_volume_update(self):
        if self.volume_update_id is None:
            self.volume_update_id = self.after(75, self.apply_2sf_volume)

    def apply_live_volume(self):
        self.queue_2sf_volume_update()

    def apply_2sf_volume(self):
        self.volume_update_id = None
        if not self.proc or self.proc.poll() is not None:
            return

        pactl_bin = shutil.which("pactl")
        if not pactl_bin:
            return

        try:
            result = subprocess.run(
                [pactl_bin, "--format=json", "list", "sink-inputs"],
                check=True,
                capture_output=True,
                text=True,
                timeout=2
            )
            sink_inputs = json.loads(result.stdout)
            process_id = str(self.proc.pid)
            volume = f"{self.volume_var.get()}%"
            for sink_input in sink_inputs:
                properties = sink_input.get("properties", {})
                if properties.get("application.process.id") == process_id:
                    subprocess.run(
                        [pactl_bin, "set-sink-input-volume", str(sink_input["index"]), volume],
                        check=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=2
                    )
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
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
        vol_gain = max(0, min(200, self.volume_var.get())) / 100.0
        env["SDL_AUDIO_VOLUME"] = str(vol_gain)

        cmd_args = [twosf_bin]
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
            self.queue_2sf_volume_update()
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
                    m = re.search(r'Remaining:\s*(\d+)s\s*/\s*(\d+)s', line)
                    if m and self.winfo_exists():
                        rem = int(m.group(1))
                        tot = int(m.group(2))
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


class ExtractedFilesBrowserWindow(tk.Toplevel):
    def __init__(self, parent, folder_path):
        super().__init__(parent)
        self.parent = parent
        self.folder_path = folder_path
        self.title(f"Select Music File - {os.path.basename(folder_path)}")
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
            ".usf", ".miniusf", ".gsf", ".minigsf", ".sid", ".adx", ".mp3", ".ogg", ".flac", ".wav", ".m4a", ".aac", ".opus", ".wma",
            ".ape", ".wv", ".mod", ".s3m", ".xm", ".it", ".mka", ".tta", ".ac3", ".dts", ".aiff", ".au", ".ra", ".mid", ".MID", ".xma", ".XMA", ".2sf", ".mini2sf"
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
        ttk.Button(btn_frame, text="Browse Extracted Files", command=self.browse_extracted_folder).pack(side="left", expand=True, padx=5)
        ttk.Button(btn_frame, text="Close", command=self.destroy).pack(side="right", padx=5)

    def play_selected(self):
        selection = self.listbox.curselection()
        if not selection:
            return
        idx = selection[0]
        _, audio_file = self.files_list[idx]
        self.parent.play_audio_file(audio_file)

    def browse_extracted_folder(self):
        try:
            if os.path.exists(self.folder_path):
                subprocess.Popen(["xdg-open", self.folder_path])
        except Exception as e:
            messagebox.showerror("Error", f"Could not open folder:\n\n{e}")


class VGMStreamPlayerWindow(tk.Toplevel):
    def __init__(self, parent, audio_file):
        super().__init__(parent)
        self.parent = parent
        self.audio_file = audio_file
        self.vgmstream_proc = None
        self.is_paused = False
        self.current_position_seconds = 0.0
        self.total_duration_seconds = 180.0
        self.m3u_path = None
        self.volume_update_id = None
        saved_volume = get_saved_player_volume()
        self.volume_var = tk.IntVar(value=saved_volume)
        self.volume_label_var = tk.StringVar(value=f"Volume: {saved_volume}%")
        register_volume_player(self)

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
        self.seek_pct_var = tk.StringVar(value="0%")

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
            self.m3u_path = None

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
        self.geometry("480x540")
        self.minsize(440, 460)
        self.transient(self.parent)

        padding = ttk.Frame(self, padding=12)
        padding.pack(fill="both", expand=True)

        browse_frame = ttk.Frame(padding)
        browse_frame.pack(side="top", anchor="w", pady=(0, 6))

        browse_btn = tk.Button(browse_frame, text="📁", font=("Arial", 12), width=3, command=self.on_browse_clicked)
        browse_btn.pack(side="top", anchor="w")
        ToolTip(browse_btn, "Find and play a local file", delay=500)

        browse_lbl = ttk.Label(browse_frame, text="Browse", font=("Arial", 8))
        browse_lbl.pack(side="top", anchor="w")

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
            seek_start_func=lambda: None,
            seek_release_func=lambda: None
        )
        self.progress_bar_widget.frame.pack(fill="x", expand=True)

        vol_frame = ttk.Frame(padding)
        vol_frame.pack(fill="x", pady=(0, 8))
        ttk.Label(vol_frame, textvariable=self.volume_label_var, font=("Arial", 9)).pack(side="left", padx=5)
        self.volume_slider = ttk.Scale(vol_frame, from_=0, to=150, orient="horizontal", command=self.on_volume_slide)
        self.volume_slider.set(self.volume_var.get())
        self.volume_slider.pack(side="right", fill="x", expand=True, padx=5)
        self.volume_slider.bind("<ButtonPress-1>", self.on_volume_press)
        self.volume_slider.bind("<ButtonRelease-1>", self.on_volume_release)

        vol_frame = ttk.Frame(padding)
        vol_frame.pack(fill="x", pady=(0, 8))
        ttk.Label(vol_frame, textvariable=self.volume_label_var, font=("Arial", 9)).pack(side="left", padx=5)
        self.volume_slider = ttk.Scale(vol_frame, from_=0, to=150, orient="horizontal", command=self.on_volume_slide)
        self.volume_slider.set(self.volume_var.get())
        self.volume_slider.pack(side="right", fill="x", expand=True, padx=5)
        self.volume_slider.bind("<ButtonPress-1>", self.on_volume_press)
        self.volume_slider.bind("<ButtonRelease-1>", self.on_volume_release)

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

    def on_browse_clicked(self):
        parent = self.parent
        initial_dir = getattr(parent, "_last_loaded_music_dir", None) or parent.default_load_music_dir.get()
        file_path = filedialog.askopenfilename(
            parent=self,
            title="Select Music File",
            initialdir=initial_dir,
            filetypes=[
                ("Audio & Emulated Files", "*.ay *.gbs *.gym *.hes *.kss *.nsf *.nsfe *.sap *.spc *.vgm *.vgz *.usf *.miniusf *.gsf *.minigsf *.sid *.adx *.mp3 *.ogg *.flac *.wav *.m4a *.aac *.opus *.wma *.ape *.wv *.mod *.s3m *.xm *.it *.mka *.tta *.ac3 *.dts *.aiff *.au *.ra *.mid *.MID *.xma *.XMA *.2sf *.mini2sf"),
                ("All Files", "*.*")
            ]
        )
        if file_path:
            folder = os.path.dirname(file_path)
            parent._last_loaded_music_dir = folder
            self.close()
            parent.play_audio_file(file_path)

    def on_loop_settings_changed(self):
        self.play_current_track(start_time=self.current_position_seconds)

    def on_volume_press(self, event):
        self.is_dragging_volume = True

    def on_volume_release(self, event):
        self.is_dragging_volume = False
        set_player_volume(self.volume_slider.get())

    def on_volume_slide(self, value):
        set_player_volume(float(value))

    def queue_vgmstream_volume_update(self):
        if self.volume_update_id is None:
            self.volume_update_id = self.after(75, self.apply_vgmstream_volume)

    def apply_live_volume(self):
        self.queue_vgmstream_volume_update()

    def apply_vgmstream_volume(self):
        self.volume_update_id = None
        if not self.vgmstream_proc or self.vgmstream_proc.poll() is not None:
            return
        pactl_bin = shutil.which("pactl")
        if not pactl_bin:
            return
        try:
            result = subprocess.run(
                [pactl_bin, "--format=json", "list", "sink-inputs"],
                check=True,
                capture_output=True,
                text=True,
                timeout=2
            )
            sink_inputs = json.loads(result.stdout)
            process_id = str(self.vgmstream_proc.pid)
            volume = f"{self.volume_var.get()}%"
            for sink_input in sink_inputs:
                properties = sink_input.get("properties", {})
                if properties.get("application.process.id") == process_id:
                    subprocess.run(
                        [pactl_bin, "set-sink-input-volume", str(sink_input["index"]), volume],
                        check=True,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        timeout=2
                    )
        except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError):
            pass

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
            self.queue_vgmstream_volume_update()
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
                        self.total_duration_seconds = float(dur)
                        self.position_var.set(f"Position: {curr} s")
                        self.duration_var.set(f"Duration: {dur} s")
                        if self.total_duration_seconds > 0:
                            pct = int((self.current_position_seconds / self.total_duration_seconds) * 100)
                            self.seek_pct_var.set(f"{pct}%")
                        self.progress_bar_widget.update_progress(self.current_position_seconds, self.total_duration_seconds)
            
        except Exception:
            pass

        return_code = proc.wait()
        if self.winfo_exists() and self.vgmstream_proc == proc and self.status_var.get() == "Playing":
            if return_code != 0:
                self.status_var.set("Playback Error")
            else:
                self.after(0, self.advance_after_end, proc)

    def advance_after_end(self, proc):
        if not self.winfo_exists() or self.vgmstream_proc != proc or self.status_var.get() != "Playing":
            return
        if self.loop_forever_var.get():
            self.play_current_track(start_time=0.0)
            return
        next_index = get_next_track_index(self.current_index, len(self.playlist), False)
        if next_index is None:
            self.vgmstream_proc = None
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
        if self.m3u_path and os.path.exists(self.m3u_path):
            try:
                os.remove(self.m3u_path)
            except Exception:
                pass
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
        self.m3u_path = None
        self.socket_path = os.path.join(tempfile.gettempdir(), f"mpv-python-{os.getpid()}-{threading.get_ident()}.sock")
        self.is_dragging_volume = False
        self.is_scrubbing = False
        self.raw_duration_seconds = 180.0
        self.raw_position_seconds = 0.0

        is_emu = is_emulated_file(self.audio_file)
        self.loop_chk_var = tk.BooleanVar(value=is_emu)
        self.loop_count_var = tk.IntVar(value=1 if is_emu else 1)
        self.loop_forever_var = tk.BooleanVar(value=False)

        self.playlist = []
        self.current_index = 0
        self.current_track_index = None
        self.track_repeat_count = 0
        self.end_of_track_handled = False
        self.load_playlist_files()

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

        ext = os.path.splitext(self.audio_file)[1].lower()
        if ext not in (".miniusf", ".usf", ".mid", ".midi", ".xma", ".XMA", ".adx", ".2sf", ".mini2sf"):
            self.generate_m3u_playlist()

        if self.playlist:
            self.play_current_track()
            self.poll_status()

    def format_time(self, seconds):
        if seconds is None or seconds < 0:
            return "00:00:00"
        total_seconds = int(seconds)
        hours = total_seconds // 3600
        minutes = (total_seconds % 3600) // 60
        secs = total_seconds % 60
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    def load_playlist_files(self):
        folder = os.path.dirname(self.audio_file)
        ext = os.path.splitext(self.audio_file)[1].lower()
        audio_basename = os.path.splitext(os.path.basename(self.audio_file))[0]

        if ext in (".hes", ".nsf", ".nsfe", ".gbs", ".miniusf", ".usf", ".mid", ".midi", ".xma", ".XMA", ".adx", ".2sf", ".mini2sf"):
            self.playlist = []
            if ext in (".miniusf", ".usf", ".mid", ".midi", ".xma", ".XMA", ".adx", ".2sf", ".mini2sf"):
                try:
                    target_exts = (".miniusf", ".usf") if ext in (".miniusf", ".usf") else (
                        (".mid", ".midi", ".MID") if ext in (".mid", ".midi") else (
                            (".xma", ".XMA") if ext in (".xma", ".XMA") else (
                                (".2sf", ".mini2sf") if ext in (".2sf", ".mini2sf") else (".adx", ".ADX")
                            )
                        )
                    )
                    for f in sorted(os.listdir(folder)):
                        if f.lower().endswith(tuple(target_exts)):
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

                if not self.playlist and ext == ".nsf":
                    try:
                        with open(self.audio_file, "rb") as f:
                            header = f.read(0x08)
                            if header.startswith(b"NESM"):
                                total_songs = header[6]
                                if total_songs > 0:
                                    for i in range(total_songs):
                                        self.playlist.append({"file": self.audio_file, "title": f"Track {i + 1}", "track_index": i})
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
                    ".mod", ".s3m", ".xm", ".it", ".mka", ".tta", ".ac3", ".dts", ".aiff", ".au", ".ra", ".miniusf", ".usf", ".mid", ".midi", ".MID", ".xma", ".XMA", ".adx", ".2sf", ".mini2sf"
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
                    if isinstance(p, dict):
                        f.write(f"{os.path.basename(p['file'])}?track_index={p['track_index']}\n")
                    elif isinstance(p, str):
                        f.write(f"{os.path.basename(p)}\n")
            self.m3u_path = m3u_path
        except Exception:
            self.m3u_path = None

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
        ToolTip(browse_btn, "Find and play a local file", delay=500)

        browse_lbl = ttk.Label(browse_frame, text="Browse", font=("Arial", 8))
        browse_lbl.pack(side="top", anchor="w")

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
            get_duration_func=lambda: self.raw_duration_seconds,
            seek_func=self.seek_to_position,
            seek_start_func=self.on_seek_start,
            seek_release_func=self.on_seek_release
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
        
        self.volume_slider.bind("<ButtonPress-1>", self.on_volume_press)
        self.volume_slider.bind("<ButtonRelease-1>", self.on_volume_release)

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

    def on_seek_start(self):
        self.is_scrubbing = True

    def on_seek_release(self):
        self.is_scrubbing = False

    def seek_to_position(self, target_time):
        ext = os.path.splitext(self.audio_file)[1].lower()
        if ext in (".mid", ".midi"):
            self.midi_elapsed_offset = float(target_time)
            self.midi_start_time = time.time()
            self.raw_position_seconds = float(target_time)
            ticks = int((float(target_time) / (self.midi_tempo / 1000000.0)) * self.midi_tpq)
            try:
                if self.mpv and self.mpv.stdin:
                    self.mpv.stdin.write(f"seek {ticks}\n")
                    self.mpv.stdin.flush()
            except Exception:
                pass
            if self.status_var.get() == "Paused":
                self.status_var.set("Playing")
        else:
            self.command("seek", float(target_time), "absolute")
            if self.status_var.get() == "Paused":
                self.command("set", "pause", "no")
                self.status_var.set("Playing")

    def on_browse_clicked(self):
        parent = self.parent
        initial_dir = getattr(parent, "_last_loaded_music_dir", None) or parent.default_load_music_dir.get()
        file_path = filedialog.askopenfilename(
            parent=self,
            title="Select Music File",
            initialdir=initial_dir,
            filetypes=[
                ("Audio & Emulated Files", "*.ay *.gbs *.gym *.hes *.kss *.nsf *.nsfe *.sap *.spc *.vgm *.vgz *.usf *.miniusf *.gsf *.minigsf *.sid *.adx *.mp3 *.ogg *.flac *.wav *.m4a *.aac *.opus *.wma *.ape *.wv *.mod *.s3m *.xm *.it *.mka *.tta *.ac3 *.dts *.aiff *.au *.ra *.mid *.MID *.xma *.XMA *.2sf *.mini2sf"),
                ("All Files", "*.*")
            ]
        )
        if file_path:
            folder = os.path.dirname(file_path)
            parent._last_loaded_music_dir = folder
            self.close()
            parent.play_audio_file(file_path)

    def on_loop_settings_changed(self):
        self.play_current_track()

    def on_volume_press(self, event):
        self.is_dragging_volume = True

    def on_volume_release(self, event):
        self.is_dragging_volume = False
        set_player_volume(self.volume_slider.get())

    def on_volume_slide(self, val):
        set_player_volume(float(val))

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
                if isinstance(self.mpv, subprocess.Popen) and self.mpv.stdin:
                    self.mpv.stdin.write("quit\n")
                    self.mpv.stdin.flush()
                self.mpv.terminate()
            except Exception:
                pass
        try:
            if os.path.exists(self.socket_path):
                os.unlink(self.socket_path)
        except FileNotFoundError:
            pass

        ext = os.path.splitext(self.audio_file)[1].lower()
        if ext in (".mid", ".midi"):
            sf2_path = os.path.join(get_cache_directory(), "FluidR3_GM.sf2")
            dur, tpq, tempo = get_midi_info(self.audio_file)
            self.raw_duration_seconds = dur
            self.midi_tpq = tpq
            self.midi_tempo = tempo
            self.midi_start_time = time.time()
            self.midi_elapsed_offset = 0.0
            
            cmd = [
                "fluidsynth", "-n",
                "-g", "0.8",
                "-z", "256",
                "-c", "64",
                "-r", "44100",
                sf2_path,
                self.audio_file
            ]
            try:
                self.mpv = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)
                try:
                    vol = float(self.volume_var.get())
                    gain_val = max(0.0, min(2.0, vol / 100.0 * 0.8))
                    self.mpv.stdin.write(f"gain {gain_val}\n")
                    self.mpv.stdin.flush()
                except Exception:
                    pass

                class FluidSynthSocketWrapper:
                    def __init__(self, proc, outer):
                        self.proc = proc
                        self.outer = outer
                        self.is_paused = False

                    def sendall(self, data):
                        try:
                            msg = json.loads(data.decode().strip())
                            cmd_list = msg.get("command", [])
                            if cmd_list:
                                action = cmd_list[0]
                                if action == "cycle" and len(cmd_list) > 1 and cmd_list[1] == "pause":
                                    if self.is_paused:
                                        self.proc.stdin.write("player_cont\n")
                                        self.is_paused = False
                                        self.outer.status_var.set("Playing")
                                    else:
                                        self.proc.stdin.write("player_stop\n")
                                        self.is_paused = True
                                        self.outer.status_var.set("Paused")
                                    self.proc.stdin.flush()
                                elif action == "set_property" and len(cmd_list) > 2 and cmd_list[1] == "volume":
                                    vol = float(cmd_list[2])
                                    gain_val = max(0.0, min(2.0, vol / 100.0 * 0.8))
                                    self.proc.stdin.write(f"gain {gain_val}\n")
                                    self.proc.stdin.flush()
                                elif action == "seek" and len(cmd_list) > 1:
                                    target = float(cmd_list[1])
                                    mode = cmd_list[2] if len(cmd_list) > 2 else "absolute"
                                    curr = self.outer.raw_position_seconds
                                    new_pos = (curr + target) if mode == "relative" else target
                                    new_pos = max(0.0, min(new_pos, self.outer.raw_duration_seconds))
                                    self.outer.seek_to_position(new_pos)
                        except Exception:
                            pass
                    def recv(self, size):
                        return b""
                    def close(self):
                        try:
                            self.proc.stdin.write("quit\n")
                            self.proc.stdin.flush()
                        except Exception:
                            pass

                self.sock = FluidSynthSocketWrapper(self.mpv, self)
            except Exception as e:
                messagebox.showerror("FluidSynth Error", f"Could not launch fluidsynth:\n\n{e}")
            return

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
            f_path = item["file"]
            t_idx = item["track_index"]
            display_title = item["title"]
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

            self.current_track_index = None

        self.audio_file = target_file
        display_title = os.path.basename(self.audio_file)

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

    def advance_after_end(self):
        if self.status_var.get() != "Playing":
            return

        ext = os.path.splitext(self.audio_file)[1].lower()
        if ext in (".mid", ".midi"):
            repeat_limit = max(1, int(self.loop_count_var.get()))
            if self.loop_forever_var.get() or (self.loop_chk_var.get() and self.track_repeat_count < repeat_limit - 1):
                if not self.loop_forever_var.get():
                    self.track_repeat_count += 1
                self.midi_elapsed_offset = 0.0
                self.midi_start_time = time.time()
                self.raw_position_seconds = 0.0
                try:
                    self.mpv.stdin.write("seek 0\n")
                    self.mpv.stdin.flush()
                except Exception:
                    pass
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
        if self.mpv:
            try:
                if isinstance(self.mpv, subprocess.Popen) and self.mpv.stdin:
                    self.mpv.stdin.write("quit\n")
                    self.mpv.stdin.flush()
                self.mpv.terminate()
            except Exception:
                pass
            self.mpv = None
        self.status_var.set("Stopped")

    def poll_status(self):
        if not self.winfo_exists():
            return
        
        ext = os.path.splitext(self.audio_file)[1].lower()
        if ext in (".mid", ".midi"):
            if hasattr(self, "midi_start_time") and self.status_var.get() == "Playing" and not getattr(self, "is_scrubbing", False):
                elapsed = self.midi_elapsed_offset + (time.time() - self.midi_start_time)
                self.raw_position_seconds = elapsed
                
                if self.raw_duration_seconds > 0 and elapsed >= self.raw_duration_seconds:
                    repeat_limit = max(1, int(self.loop_count_var.get()))
                    if self.loop_forever_var.get() or (self.loop_chk_var.get() and self.track_repeat_count < repeat_limit - 1):
                        if not self.loop_forever_var.get():
                            self.track_repeat_count += 1
                        self.midi_start_time = time.time()
                        self.midi_elapsed_offset = 0.0
                        self.raw_position_seconds = 0.0
                        try:
                            self.mpv.stdin.write("seek 0\n")
                            self.mpv.stdin.flush()
                        except Exception:
                            self.play_current_track()
                    else:
                        if not self.end_of_track_handled:
                            self.end_of_track_handled = True
                            self.after(0, self.advance_after_end)
                        return

                self.position_var.set(f"Position: {self.format_time(self.raw_position_seconds)}")
                if self.raw_duration_seconds > 0:
                    pct = int((self.raw_position_seconds / self.raw_duration_seconds) * 100)
                    self.seek_pct_var.set(f"{pct}%")
                self.progress_bar_widget.update_progress(self.raw_position_seconds, self.raw_duration_seconds)
            if self.winfo_exists():
                self.after(500, self.poll_status)
            return

        if not self.sock:
            if self.winfo_exists():
                self.after(500, self.poll_status)
            return
        
        self.request_property("time-pos")
        self.request_property("duration")
        self.request_property("pause")
        self.request_property("eof-reached")
        
        if not self.is_dragging_volume:
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
                        ext = os.path.splitext(self.audio_file)[1].lower()
                        if ext not in (".nsf", ".nsfe", ".gbs", ".hes", ".kss"):
                            self.raw_duration_seconds = float(val)
                    elif rid == "pause" and val is not None and self.status_var.get() != "Stopped":
                        self.status_var.set("Paused" if val else "Playing")
                    elif rid == "eof-reached" and val is True and self.status_var.get() == "Playing":
                        if self.loop_forever_var.get():
                            continue
                        if not self.end_of_track_handled:
                            self.end_of_track_handled = True
                            self.after(0, self.advance_after_end)
                    elif rid == "volume" and val is not None and not self.is_dragging_volume:
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
        self.stop()
        if self.m3u_path and os.path.exists(self.m3u_path):
            try:
                os.remove(self.m3u_path)
            except Exception:
                pass
        if self.mpv:
            try:
                if isinstance(self.mpv, subprocess.Popen) and self.mpv.stdin:
                    self.mpv.stdin.write("quit\n")
                    self.mpv.stdin.flush()
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
