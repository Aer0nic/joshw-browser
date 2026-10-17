import array
import json
import os
import queue
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
import sounddevice as sd
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


def set_player_volume(value, source_player=None):
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
                if player != source_player and hasattr(player, "volume_slider"):
                    player.volume_slider.set(volume)
                player.apply_live_volume()
            except (AttributeError, tk.TclError):
                _active_volume_players.discard(player)
    finally:
        _syncing_player_volume = False


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


def prepare_nsf_m3u(nsf_file_path):
    nsf_file_path = os.path.abspath(nsf_file_path)
    folder = os.path.dirname(nsf_file_path)
    base_name = os.path.splitext(os.path.basename(nsf_file_path))[0]
    m3u_path = os.path.join(folder, f"{base_name}.m3u")

    candidate_bins = [
        os.path.expanduser("~/.config/joshw/nsf_tracks"),
        os.path.join(get_cache_directory(), "nsf_tracks"),
        shutil.which("nsf_tracks")
    ]
    nsf_tracks_bin = None
    for b in candidate_bins:
        if b and os.path.exists(b):
            nsf_tracks_bin = b
            break

    track_count = 0
    track_titles = {}

    if nsf_tracks_bin:
        try:
            res = subprocess.run(
                [nsf_tracks_bin, nsf_file_path],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                timeout=5
            )
            output = res.stdout or ""
            for line in output.splitlines():
                line_s = line.strip()
                if not line_s:
                    continue
                m_count = re.search(r'(\d+)\s+tracks?', line_s, re.IGNORECASE)
                if m_count and track_count == 0:
                    track_count = int(m_count.group(1))
                    continue
                m_track = re.match(r'^(?:Track\s+)?(\d+)[:\.]\s*(.*)$', line_s, re.IGNORECASE)
                if m_track:
                    t_num = int(m_track.group(1))
                    t_title = m_track.group(2).strip()
                    track_titles[t_num] = t_title
        except Exception as e:
            print(f"Error running nsf_tracks: {e}")

    if track_count <= 0 and track_titles:
        track_count = max(track_titles.keys())

    if track_count <= 0:
        try:
            with open(nsf_file_path, "rb") as f:
                header = f.read(0x10)
                if header.startswith(b"NESM"):
                    track_count = header[6]
        except Exception:
            pass

    if track_count <= 0:
        track_count = 1

    tracks = []
    nsf_filename = os.path.basename(nsf_file_path)
    for i in range(1, track_count + 1):
        raw_title = track_titles.get(i, "")
        if raw_title and raw_title.lower() != "(untitled)":
            title = f"Track {i}: {raw_title}"
        else:
            title = f"Track {i}"
            
        zero_based_index = i - 1
        tracks.append({
            "file": nsf_file_path,
            "title": title,
            "track_index": zero_based_index
        })

    try:
        with open(m3u_path, "w", encoding="utf-8") as f:
            f.write("#EXTM3U\n")
            for t in tracks:
                f.write(f"#EXTINF:-1,{t['title']}\n")
                f.write(f"{nsf_filename}#{t['track_index']}\n")
    except Exception as e:
        print(f"Error writing .m3u file {m3u_path}: {e}")

    return m3u_path, tracks


def parse_m3u_playlist(m3u_path, fallback_file_path=None):
    folder = os.path.dirname(m3u_path)
    playlist = []
    if not os.path.exists(m3u_path):
        return playlist
    try:
        with open(m3u_path, "r", encoding="utf-8", errors="replace") as f:
            lines = [line.strip() for line in f if line.strip()]

        current_extinf_title = None
        for line in lines:
            if line.startswith("#EXTINF:"):
                parts = line.split(",", 1)
                if len(parts) > 1:
                    current_extinf_title = parts[1].strip()
                continue
            if line.startswith("#"):
                continue

            match_hash = re.search(r'#(\d+)', line)
            match_colon = re.search(r'::NSF,(\d+)(?:,([^,]+))?', line, re.IGNORECASE)

            if match_hash:
                raw_track_num = int(match_hash.group(1))
                track_num = max(0, raw_track_num - 1) if raw_track_num > 0 else 0
                file_part = line.split("#")[0].strip()
                target_file = os.path.join(folder, file_part) if file_part else fallback_file_path
                title = current_extinf_title or f"Track {track_num + 1}"
                playlist.append({"file": target_file or fallback_file_path, "title": title, "track_index": track_num})
                current_extinf_title = None
            elif match_colon:
                track_num = int(match_colon.group(1))
                sub_title = match_colon.group(2) if match_colon.group(2) else None
                title = current_extinf_title or (f"Track {track_num}: {sub_title}" if sub_title else f"Track {track_num}")
                target_file = os.path.join(folder, line.split("::")[0].strip()) if not line.startswith("::") else fallback_file_path
                playlist.append({"file": target_file or fallback_file_path, "title": title, "track_index": track_num})
                current_extinf_title = None
            else:
                target_file = os.path.join(folder, line) if not os.path.isabs(line) else line
                title = current_extinf_title or os.path.basename(line)
                playlist.append({"file": target_file, "title": title, "track_index": None})
                current_extinf_title = None
    except Exception as e:
        print(f"Error parsing .m3u {m3u_path}: {e}")
    return playlist


class SIDPlayerWindow(tk.Toplevel):
    def __init__(self, parent, audio_file, playlist=None, start_index=None):
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
        self.is_scrubbing = False
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

        browse_btn = tk.Button(browse_frame, text="刀", font=("Arial", 12), width=3, command=self.on_browse_clicked)
        browse_btn.pack(side="top", anchor="w")

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
            seek_start_func=lambda: setattr(self, "is_scrubbing", True),
            seek_release_func=lambda: setattr(self, "is_scrubbing", False)
        )
        self.progress_bar_widget.frame.pack(fill="x", expand=True)

        vol_frame = ttk.Frame(padding)
        vol_frame.pack(fill="x", pady=(0, 8))
        ttk.Label(vol_frame, textvariable=self.volume_label_var, font=("Arial", 9)).pack(side="left", padx=5)
        self.volume_slider = ttk.Scale(vol_frame, from_=0, to=150, orient="horizontal", command=self.on_volume_slide)
        self.volume_slider.set(self.volume_var.get())
        self.volume_slider.pack(side="right", fill="x", expand=True, padx=5)

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

    def on_volume_slide(self, val):
        set_player_volume(float(val), source_player=self)

    def apply_live_volume(self):
        pass

    def start_timer_thread(self):
        def timer_loop():
            while self.winfo_exists():
                if self.status_var.get() == "Playing" and not self.is_scrubbing:
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
        ExtractedFilesBrowserWindow(self, os.path.dirname(self.audio_file))

    def populate_subtrack_listbox(self):
        if not self.winfo_exists():
            return
        self.subtrack_listbox.delete(0, tk.END)
        for idx in range(1, len(self.subtracks) + 1):
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
        
        title, author, released, song_length, playtime = "UNKNOWN", "UNKNOWN", "UNKNOWN", "UNKNOWN", "00:00"
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

                        formatted_text = (
                            f"Title: {title}\nAuthor: {author}\nReleased: {released}\n\n"
                            f"Song Length: {song_length}\nPlaytime: {playtime}"
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
        self.is_scrubbing = False
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
        self.geometry("560x630")
        self.minsize(520, 560)
        self.transient(self.parent)

        padding = ttk.Frame(self, padding=12)
        padding.pack(fill="both", expand=True)

        browse_frame = ttk.Frame(padding)
        browse_frame.pack(side="top", anchor="w", pady=(0, 6))

        browse_btn = tk.Button(browse_frame, text="刀", font=("Arial", 12), width=3, command=self.on_browse_clicked)
        browse_btn.pack(side="top", anchor="w")

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
            seek_start_func=lambda: setattr(self, "is_scrubbing", True),
            seek_release_func=lambda: setattr(self, "is_scrubbing", False)
        )
        self.progress_bar_widget.frame.pack(fill="x", expand=True)

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

    def on_volume_slide(self, val):
        set_player_volume(float(val), source_player=self)

    def apply_live_volume(self):
        pass

    def start_timer_thread(self):
        def timer_loop():
            while self.winfo_exists():
                if self.status_var.get() == "Playing" and not self.is_scrubbing:
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
                next_index = get_next_track_index(self.current_index, len(self.playlist), self.loop_forever_var.get())
                if next_index is None:
                    self.gsf_proc = None
                    self.status_var.set("Stopped")
                else:
                    self.current_index = next_index
                    self.after(0, self.start_playgsf)

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
        self.is_scrubbing = False
        self.track_repeat_count = 0

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
        self.protocol("WM_DELETE_WINDOW", self.close)

        if self.playlist:
            self.play_current_track()
            self.start_timer_thread()

    def format_time(self, seconds):
        if seconds is None or seconds < 0:
            return "00:00:00"
        total_seconds = int(seconds)
        return f"{total_seconds // 3600:02d}:{(total_seconds % 3600) // 60:02d}:{total_seconds % 60:02d}"

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

        browse_btn = tk.Button(browse_frame, text="刀", font=("Arial", 12), width=3, command=self.on_browse_clicked)
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


class PSFAudioWorker:
    def __init__(self, audio_file, ui_queue, volume=1.0, loop_forever=False, loop_count=1):
        self.audio_file = audio_file
        self.ui_queue = ui_queue
        self.volume = max(0.0, float(volume))
        self.loop_forever = loop_forever
        self.loop_count = loop_count
        self.repeat_count = 0

        self._stop_event = threading.Event()
        self._pause_event = threading.Event()
        self._pause_event.set()
        self._seek_reset_event = threading.Event()
        self._thread = None
        self._lock = threading.Lock()

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stop_event.set()
        self._pause_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)

    def pause(self):
        self._pause_event.clear()

    def resume(self):
        self._pause_event.set()

    def set_volume(self, vol):
        with self._lock:
            self.volume = max(0.0, float(vol))

    def reset_track(self):
        self._seek_reset_event.set()

    def update_loop_settings(self, loop_forever, loop_count):
        with self._lock:
            self.loop_forever = loop_forever
            self.loop_count = loop_count

    def _run(self):
        try:
            import sounddevice as sd
            from psf_ctypes import AOSDKContext, AOSDKError
        except Exception as e:
            self.ui_queue.put(("error", f"Failed to initialize audio subsystem: {e}"))
            self.ui_queue.put(("status", "Audio Error"))
            return

        try:
            with AOSDKContext() as ctx:
                try:
                    ctx.open(self.audio_file)
                except AOSDKError as e:
                    self.ui_queue.put(("error", str(e)))
                    self.ui_queue.put(("status", "Load Error"))
                    return

                duration = ctx.get_duration()
                fade = ctx.get_fade()
                tags = ctx.get_tags()
                fmt = ctx.format_name

                self.ui_queue.put(("opened", {"duration": duration, "fade": fade, "tags": tags, "format": fmt}))
                self.ui_queue.put(("status", "Playing"))

                chunk_size = 2048
                sample_rate = ctx.sample_rate

                try:
                    stream = sd.RawOutputStream(
                        samplerate=sample_rate, channels=2, dtype="int16", blocksize=chunk_size
                    )
                    stream.start()
                except Exception as e:
                    self.ui_queue.put(("error", f"Could not open audio output device: {e}"))
                    self.ui_queue.put(("status", "Device Error"))
                    return

                try:
                    last_pos_time = 0.0
                    while not self._stop_event.is_set():
                        if not self._pause_event.is_set():
                            time.sleep(0.05)
                            continue

                        if self._seek_reset_event.is_set():
                            self._seek_reset_event.clear()
                            try:
                                ctx.reset()
                                self.ui_queue.put(("position", (0.0, duration, 0)))
                            except Exception:
                                pass

                        count, pcm = ctx.render(chunk_size)
                        if count <= 0 or ctx.is_done():
                            with self._lock:
                                loop_forever, loop_count = self.loop_forever, self.loop_count

                            if loop_forever:
                                ctx.reset()
                                continue
                            elif self.repeat_count < loop_count - 1:
                                self.repeat_count += 1
                                ctx.reset()
                                continue
                            else:
                                self.ui_queue.put(("ended", None))
                                break

                        with self._lock:
                            vol = self.volume

                        if vol != 1.0 and len(pcm) > 0:
                            arr = array.array("h")
                            arr.frombytes(pcm)
                            for i in range(len(arr)):
                                arr[i] = max(-32768, min(32767, int(arr[i] * vol)))
                            pcm = arr.tobytes()

                        try:
                            stream.write(pcm)
                        except Exception:
                            if self._stop_event.is_set():
                                break
                            raise

                        now = time.time()
                        if now - last_pos_time >= 0.2:
                            last_pos_time = now
                            curr_pos = ctx.get_position()
                            pct = int((curr_pos / duration) * 100) if duration and duration > 0 else 0
                            self.ui_queue.put(("position", (curr_pos, duration, pct)))
                finally:
                    try:
                        stream.stop()
                        stream.close()
                    except Exception:
                        pass
                    ctx.stop()
        except Exception as e:
            if not self._stop_event.is_set():
                self.ui_queue.put(("status", "Playback Error"))
        finally:
            if not self._stop_event.is_set():
                self.ui_queue.put(("status", "Stopped"))


class PSFPlayerWindow(tk.Toplevel):
    def __init__(self, parent, audio_file, playlist=None, start_index=0, **kwargs):
        super().__init__(parent)
        self.parent = parent
        self.audio_file = audio_file
        self.playlist = playlist or [{"file": audio_file, "title": os.path.basename(audio_file), "track_index": 0}]
        self.current_index = start_index if 0 <= start_index < len(self.playlist) else 0
        
        self.worker = None
        self.is_paused = False
        self.current_position_seconds = 0.0
        self.total_duration_seconds = 180.0
        self.is_scrubbing = False
        self.ui_queue = queue.Queue()

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
        self.tags_var = tk.StringVar(value="")

        self.load_playlist_files()
        self.build_ui()
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.start_queue_polling()

        if self.playlist:
            self.play_current_track()

    def format_time(self, seconds):
        if seconds is None or seconds < 0:
            return "00:00:00"
        total_seconds = int(seconds)
        return f"{total_seconds // 3600:02d}:{(total_seconds % 3600) // 60:02d}:{total_seconds % 60:02d}"

    def load_playlist_files(self):
        folder = os.path.dirname(self.audio_file)
        abs_target = os.path.normpath(os.path.abspath(self.audio_file))
        self.playlist = []
        try:
            for f in sorted(os.listdir(folder)):
                if f.lower().endswith((".psf", ".minipsf", ".psf2", ".minipsf2")):
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
        self.title(f"PSF Player - {filename}")
        self.geometry("520x620")
        self.minsize(460, 520)
        self.transient(self.parent)

        padding = ttk.Frame(self, padding=12)
        padding.pack(fill="both", expand=True)

        browse_frame = ttk.Frame(padding)
        browse_frame.pack(side="top", anchor="w", pady=(0, 6))

        browse_btn = tk.Button(browse_frame, text="刀", font=("Arial", 12), width=3, command=self.on_browse_clicked)
        browse_btn.pack(side="top", anchor="w")

        self.title_label = ttk.Label(padding, text=f"Now Playing:\n{filename}", font=("Arial", 9, "bold"), wraplength=480, justify="center")
        self.title_label.pack(pady=(0, 4))

        self.meta_label = ttk.Label(padding, textvariable=self.tags_var, font=("Arial", 8), wraplength=480, justify="center", foreground="#555555")
        self.meta_label.pack(pady=(0, 6))

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
        self.populate_playlist_listbox()

        controls = ttk.Frame(padding)
        controls.pack(pady=4)

        ttk.Button(controls, text="|<< Prev", width=8, command=self.prev_track).grid(row=0, column=0, padx=2)
        ttk.Button(controls, text="Restart", width=8, command=self.restart_track).grid(row=0, column=1, padx=2)
        ttk.Button(controls, text="Play/Pause", width=10, command=self.toggle_play_pause).grid(row=0, column=2, padx=2)
        ttk.Button(controls, text="Seek Info", width=8, command=self.show_seek_info).grid(row=0, column=3, padx=2)
        ttk.Button(controls, text="Next >>|", width=8, command=self.next_track).grid(row=0, column=4, padx=2)

        btn_bottom = ttk.Frame(padding)
        btn_bottom.pack(pady=(6, 0), fill="x")

        ttk.Checkbutton(btn_bottom, variable=self.loop_chk_var, command=self.on_loop_settings_changed).pack(side="left", padx=(0, 2))
        ttk.Spinbox(btn_bottom, from_=1, to=99, width=3, textvariable=self.loop_count_var, command=self.on_loop_settings_changed).pack(side="left", padx=(0, 6))
        ttk.Button(btn_bottom, text="Stop", width=8, command=self.stop).pack(side="left", padx=2)
        ttk.Checkbutton(btn_bottom, text="Loop forever", variable=self.loop_forever_var, command=self.on_loop_settings_changed).pack(side="right", padx=(4, 0))
        ttk.Button(btn_bottom, text="Close", width=8, command=self.close).pack(side="right", padx=2)

    def populate_playlist_listbox(self):
        self.playlist_listbox.delete(0, tk.END)
        for f in self.playlist:
            self.playlist_listbox.insert(tk.END, os.path.basename(f))
        self.update_listbox_selection()

    def update_listbox_selection(self):
        if 0 <= self.current_index < len(self.playlist):
            self.playlist_listbox.selection_clear(0, tk.END)
            self.playlist_listbox.selection_set(self.current_index)
            self.playlist_listbox.see(self.current_index)

    def on_playlist_select(self, event):
        sel = self.playlist_listbox.curselection()
        if sel and sel[0] != self.current_index:
            self.current_index = sel[0]
            self.play_current_track()

    def seek_to_position(self, target_time):
        if target_time <= 1.0:
            self.restart_track()
        else:
            messagebox.showinfo("Seeking Unsupported", "PSF/PSF2 emulation does not support arbitrary scrubbing.")
            self.progress_bar_widget.update_progress(self.current_position_seconds, self.total_duration_seconds)

    def show_seek_info(self):
        messagebox.showinfo("PSF Seeking Limitation", "Hardware register states cannot be arbitrarily rewound.")

    def restart_track(self):
        if self.worker:
            self.worker.reset_track()
            self.current_position_seconds = 0.0
            self.position_var.set("Position: 00:00:00")
            self.seek_pct_var.set("0%")
            self.progress_bar_widget.update_progress(0.0, self.total_duration_seconds)
            self.status_var.set("Playing")

    def start_queue_polling(self):
        def poll():
            try:
                while True:
                    msg = self.ui_queue.get_nowait()
                    self.handle_queue_message(msg[0], msg[1])
            except queue.Empty:
                pass
            if self.winfo_exists():
                self.after(40, poll)
        self.after(40, poll)

    def handle_queue_message(self, msg_type, data):
        if not self.winfo_exists():
            return
        if msg_type == "opened":
            dur = data.get("duration")
            tags = data.get("tags", {})
            fmt = data.get("format", "PSF")
            if dur and dur > 0:
                self.total_duration_seconds = float(dur)
                self.duration_var.set(f"Duration: {self.format_time(dur)}")
            else:
                self.total_duration_seconds = 180.0
                self.duration_var.set("Duration: Unknown")
            tag_lines = [f"{k.capitalize()}: {v}" for k, v in tags.items() if k in ("game", "title", "artist", "year")]
            self.tags_var.set(f"[{fmt}] " + " | ".join(tag_lines))
        elif msg_type == "position":
            curr, tot, pct = data
            if not self.is_scrubbing:
                self.current_position_seconds = curr
                self.position_var.set(f"Position: {self.format_time(curr)}")
                self.seek_pct_var.set(f"{pct}%")
                self.progress_bar_widget.update_progress(curr, self.total_duration_seconds)
        elif msg_type == "status":
            self.status_var.set(str(data))
        elif msg_type == "ended":
            self.advance_after_end()

    def play_current_track(self):
        self.stop_current_playback()
        self.current_position_seconds = 0.0
        self.audio_file = self.playlist[self.current_index]
        filename = os.path.basename(self.audio_file)

        self.title(f"PSF Player - {filename}")
        if hasattr(self, "title_label") and self.winfo_exists():
            self.title_label.configure(text=f"Now Playing:\n{filename}")
        self.track_var.set(f"Track: {self.current_index + 1} / {len(self.playlist)}")
        self.status_var.set("Starting...")
        self.position_var.set("Position: 00:00:00")
        self.seek_pct_var.set("0%")
        self.update_listbox_selection()

        vol_gain = max(0, min(150, self.volume_var.get())) / 100.0
        loop_forever = self.loop_forever_var.get()
        loop_count = max(1, int(self.loop_count_var.get())) if self.loop_chk_var.get() else 1

        self.worker = PSFAudioWorker(self.audio_file, self.ui_queue, vol_gain, loop_forever, loop_count)
        self.worker.start()
        self.is_paused = False

    def stop_current_playback(self):
        if self.worker:
            w = self.worker
            self.worker = None
            w.stop()

    def toggle_play_pause(self):
        if not self.worker:
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

    def advance_after_end(self):
        next_index = get_next_track_index(self.current_index, len(self.playlist), False)
        if next_index is None:
            self.stop_current_playback()
            self.status_var.set("Stopped")
            return
        self.current_index = next_index
        self.play_current_track()

    def on_loop_settings_changed(self):
        if self.worker:
            self.worker.update_loop_settings(
                self.loop_forever_var.get(),
                max(1, int(self.loop_count_var.get())) if self.loop_chk_var.get() else 1
            )

    def on_volume_slide(self, val):
        set_player_volume(float(val), source_player=self)

    def apply_live_volume(self):
        vol = max(0, min(150, self.volume_var.get())) / 100.0
        if self.worker:
            self.worker.set_volume(vol)

    def on_browse_clicked(self):
        ExtractedFilesBrowserWindow(self, os.path.dirname(self.audio_file))

    def stop(self):
        self.stop_current_playback()
        self.status_var.set("Stopped")

    def close(self):
        self.stop_current_playback()
        try:
            self.destroy()
        except Exception:
            pass


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
        
        # Read starting volume from the shared settings file used by all players
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

        vol_frame = ttk.Frame(padding)
        vol_frame.pack(fill="x", pady=(0, 8))
        ttk.Label(vol_frame, textvariable=self.volume_label_var, font=("Arial", 9)).pack(side="left", padx=5)
        self.volume_slider = ttk.Scale(vol_frame, from_=0, to=150, orient="horizontal", command=self.on_volume_slide)
        self.volume_slider.set(self.volume_var.get())
        self.volume_slider.pack(side="right", fill="x", expand=True, padx=5)

        meta_frame = ttk.Frame(padding)
        meta_frame.pack(fill="x", pady=(0, 8))
        ttk.Label(meta_frame, textvariable=self.chapter_var, font=("Arial", 9, "bold")).pack(side="left", padx=5)
        ttk.Label(meta_frame, textvariable=self.duration_var, font=("Arial", 9)).pack(side="right", padx=5)

        # Expanded metadata text box (black background, yellow font)
        meta_box_frame = ttk.LabelFrame(padding, text="Stream Info", padding=4)
        meta_box_frame.pack(fill="x", pady=(0, 8))

        self.metadata_text = tk.Text(meta_box_frame, height=11, wrap="none", font=("Ubuntu Mono", 10), bg="#000000", fg="#ffff00", insertbackground="yellow")
        self.metadata_text.pack(side="left", fill="x", expand=True)
        meta_scroll = ttk.Scrollbar(meta_box_frame, orient="vertical", command=self.metadata_text.yview)
        meta_scroll.pack(side="right", fill="y")
        self.metadata_text.configure(yscrollcommand=meta_scroll.set)
        self.metadata_text.config(state="disabled")

        # Shrunk playlist frame (`height=4` equivalent constraint via smaller bounds)
        list_frame = ttk.LabelFrame(padding, text="Playlist Files / Subsongs", padding=6)
        list_frame.pack(fill="both", expand=True, pady=(0, 10))

        self.chapter_listbox = tk.Listbox(list_frame, height=4, font=("Arial", 9), selectmode="single", exportselection=False)
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


class MPVPlayerWindow(tk.Toplevel):
    def __init__(self, parent, audio_file, playlist=None, start_index=None):
        super().__init__(parent)
        self.parent = parent
        self.audio_file = audio_file
        self.mpv = None
        self.lazy_proc = None
        self.usf_thread = None
        self.usf_stop_event = None
        self.sock = None
        self.m3u_path = None
        self.socket_path = os.path.join(tempfile.gettempdir(), f"mpv-python-{os.getpid()}-{threading.get_ident()}.sock")
        self.is_scrubbing = False
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

        if ext in (".miniusf", ".usf"):
            self.playlist = []
            try:
                for f in sorted(os.listdir(folder)):
                    if f.lower().endswith((".miniusf", ".usf")):
                        self.playlist.append(os.path.join(folder, f))
                if not self.playlist:
                    self.playlist = [self.audio_file]
                norm_playlist = [os.path.normpath(os.path.abspath(p)) for p in self.playlist]
                if abs_target in norm_playlist:
                    self.current_index = norm_playlist.index(abs_target)
                else:
                    self.current_index = 0
            except Exception:
                self.playlist = [self.audio_file]
                self.current_index = 0
        else:
            try:
                files = sorted(os.listdir(folder))
                audio_exts = {
                    ".ay", ".gbs", ".gym", ".hes", ".kss", ".nsf", ".nsfe", ".sap", ".spc", ".vgm", ".vgz",
                    ".mp3", ".ogg", ".flac", ".wav", ".m4a", ".aac", ".opus", ".wma", ".ape", ".wv",
                    ".mod", ".s3m", ".xm", ".it", ".mka", ".tta", ".ac3", ".dts", ".aiff", ".au", ".ra", ".mid", ".midi", ".miniusf", ".usf"
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

        browse_btn = tk.Button(browse_frame, text="刀", font=("Arial", 12), width=3, command=self.on_browse_clicked)
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
            filetypes=[
                ("Audio & Emulated Files", "*.ay *.gbs *.gym *.hes *.kss *.nsf *.nsfe *.sap *.spc *.vgm *.vgz *.usf *.miniusf *.gsf *.minigsf *.sid *.mp3 *.ogg *.flac *.wav *.m4a *.aac *.opus *.wma *.ape *.wv *.mod *.s3m *.xm *.it *.mka *.tta *.ac3 *.dts *.aiff *.aifc *.au *.ra *.mid *.MID *.xma *.XMA *.xmd *.XMD *.2sf *.mini2sf *.psf *.minipsf *.psf2 *.minipsf2 *.dsp *.ast *.brstm *.rstm *.brwav *.rwav *.brsar *.rsar *.bfstm *.fstm *.bfwav *.fwav *.bfsar *.fsar *.vag *.adpcm *.at3"),
                ("All Files", "*.*")
            ]
        )
        if file_path:
            self.play_audio_file(file_path)

    def play_audio_file(self, audio_file, playlist=None, start_index=None):
        ext = os.path.splitext(audio_file)[1].lower()
        
        vgmstream_formats = {".ast", ".dsp", ".brstm", ".rstm", ".bfsar", ".bfwav"}
        if ext in vgmstream_formats:
            vgm_bin = get_vgmstream_path()
            if not vgm_bin or (not shutil.which(vgm_bin) and not os.path.exists(vgm_bin)):
                messagebox.showerror("vgmstream123 Not Found", "vgmstream123 not found!")
                return
            VGMStreamPlayerWindow(self, audio_file, playlist=playlist, start_index=start_index)
            return
        
        if ext in (".nsf", ".nsfe"):
            self.stop()
            self.audio_file = audio_file
            self.load_playlist_files()
            self.populate_playlist_listbox()
            self.current_index = 0
            self.play_current_track()
        else:
            handler = self.parent
            while handler and not hasattr(handler, "play_audio_file"):
                handler = getattr(handler, "parent", None)
            if handler and hasattr(handler, "play_audio_file"):
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

        if hasattr(self, "usf_stop_event") and self.usf_stop_event:
            self.usf_stop_event.set()
        if hasattr(self, "usf_thread") and self.usf_thread and self.usf_thread.is_alive():
            self.usf_thread.join(timeout=0.5)
            self.usf_thread = None

        try:
            if os.path.exists(self.socket_path):
                os.unlink(self.socket_path)
        except Exception:
            pass

        ext = os.path.splitext(self.audio_file)[1].lower()
        if ext in (".miniusf", ".usf"):
            lazyusf_bin = get_lazyusf_path()
            if not lazyusf_bin or not os.path.exists(lazyusf_bin):
                lazyusf_bin = os.path.expanduser("~/.config/joshw/lazyusf")

            if os.path.exists(lazyusf_bin):
                active_item = self.playlist[self.current_index] if self.playlist else self.audio_file
                target_path = active_item["file"] if isinstance(active_item, dict) else active_item
                
                folder = os.path.dirname(target_path)
                filename = os.path.basename(target_path)
                try:
                    self.stop()
                    detected_rate = 32000
                    detected_format = "s16be"
                    
                    err_fd, err_path = tempfile.mkstemp()
                    os.close(err_fd)
                    try:
                        probe_proc = subprocess.Popen(
                            ['stdbuf', '-o0', lazyusf_bin, filename],
                            cwd=folder,
                            stdout=subprocess.PIPE,
                            stderr=open(err_path, "w")
                        )
                        raw_sample_data = probe_proc.stdout.read(25600)
                        
                        start_time = time.time()
                        while time.time() - start_time < 0.5:
                            if probe_proc.poll() is not None:
                                break
                            try:
                                with open(err_path, "r", encoding="utf-8", errors="ignore") as ef:
                                    content = ef.read()
                                    if "LAZYUSF_META:" in content:
                                        for line in content.splitlines():
                                            if "LAZYUSF_META:" in line:
                                                parts = line.split(":")
                                                if len(parts) >= 2 and parts[1].isdigit():
                                                    detected_rate = int(parts[1])
                                                    break
                                        break
                            except Exception:
                                pass
                            time.sleep(0.01)
                        try:
                            probe_proc.terminate()
                            probe_proc.wait(timeout=0.2)
                        except Exception:
                            probe_proc.kill()
                    finally:
                        try:
                            os.remove(err_path)
                        except Exception:
                            pass

                    if len(raw_sample_data) > 1000:
                        try:
                            samples = array.array("h")
                            samples.frombytes(raw_sample_data[:len(raw_sample_data) & ~1])
                            if len(samples) > 1:
                                total_diff = sum(abs(samples[i+1] - samples[i]) for i in range(len(samples)-1))
                                if (total_diff / (len(samples) - 1)) > 11000:
                                    detected_format = "s16le"
                        except Exception:
                            pass

                    self.usf_stop_event = threading.Event()
                    self.usf_thread = threading.Thread(
                        target=play_usf_via_sounddevice,
                        args=(lazyusf_bin, filename, folder, detected_rate, detected_format, self.volume_var, self.usf_stop_event, lambda: self.after(0, self.advance_after_end)),
                        daemon=True
                    )
                    self.usf_thread.start()
                    self.status_var.set("Playing")
                    return
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
        if hasattr(self, "usf_stop_event") and self.usf_stop_event:
            self.usf_stop_event.set()
        if hasattr(self, "usf_thread") and self.usf_thread and self.usf_thread.is_alive():
            self.usf_thread.join(timeout=0.5)
            self.usf_thread = None

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
        
        ext = os.path.splitext(self.audio_file)[1].lower()
        if ext in (".miniusf", ".usf"):
            if self.usf_thread and self.usf_thread.is_alive():
                self.raw_position_seconds += 0.5
                self.position_var.set(f"Position: {self.format_time(self.raw_position_seconds)}")
                if self.raw_duration_seconds > 0 and not self.is_scrubbing:
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
        
        if not self.is_scrubbing:
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
                    elif rid == "volume" and val is not None:
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
        try:
            if os.path.exists(self.socket_path):
                os.unlink(self.socket_path)
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass


class ExtractedFilesBrowserWindow(tk.Toplevel):
    def __init__(self, parent, folder_path):
        super().__init__(parent)
        self.parent = parent
        self.folder_path = os.path.abspath(folder_path)
        self.title(f"Select Music File - {os.path.basename(self.folder_path)}")
        self.geometry("520x450")
        self.minsize(400, 300)
        self.transient(parent)

        self.entries_list = []
        self.build_ui()
        self.populate_files(self.folder_path)

    def build_ui(self):
        padding = ttk.Frame(self, padding=10)
        padding.pack(fill="both", expand=True)

        top_frame = ttk.Frame(padding)
        top_frame.pack(fill="x", pady=(0, 6))

        self.up_btn = ttk.Button(top_frame, text="Up", width=5, command=self.go_up)
        self.up_btn.pack(side="left", padx=(0, 6))

        self.dir_label = ttk.Label(top_frame, text=f"Directory: {self.folder_path}", font=("Arial", 9, "bold"), wraplength=440)
        self.dir_label.pack(side="left", fill="x", expand=True)

        list_frame = ttk.Frame(padding)
        list_frame.pack(fill="both", expand=True, pady=(0, 10))

        self.listbox = tk.Listbox(list_frame, font=("Arial", 9), selectmode="single", exportselection=False)
        self.listbox.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(list_frame, orient="vertical", command=self.listbox.yview)
        scrollbar.pack(side="right", fill="y")
        self.listbox.configure(yscrollcommand=scrollbar.set)

        self.listbox.bind("<Double-1>", lambda e: self.on_item_activated())

        btn_frame = ttk.Frame(padding)
        btn_frame.pack(fill="x")

        ttk.Button(btn_frame, text="Play / Open", command=self.on_item_activated).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Open File Picker...", command=self.open_file_picker).pack(side="left", padx=4)
        ttk.Button(btn_frame, text="Open in System Browser", command=self.browse_extracted_folder).pack(side="left", expand=True, padx=4)
        ttk.Button(btn_frame, text="Close", command=self.destroy).pack(side="right", padx=4)

    def populate_files(self, folder_path):
        self.folder_path = os.path.abspath(folder_path)
        self.title(f"Select Music File - {os.path.basename(self.folder_path) or self.folder_path}")
        if hasattr(self, "dir_label"):
            self.dir_label.configure(text=f"Directory: {self.folder_path}")

        self.listbox.delete(0, tk.END)
        self.entries_list = []

        try:
            items = sorted(os.listdir(self.folder_path), key=lambda s: s.lower())
        except Exception:
            items = []

        dirs = []
        files = []
        for item in items:
            full_p = os.path.join(self.folder_path, item)
            if os.path.isdir(full_p):
                dirs.append((f"[DIR] {item}", full_p, True))
            else:
                if item.lower().endswith(".m3u"):
                    continue
                files.append((item, full_p, False))

        self.entries_list = dirs + files
        for display_name, _, _ in self.entries_list:
            self.listbox.insert(tk.END, display_name)

    def go_up(self):
        parent_dir = os.path.dirname(self.folder_path)
        if parent_dir and parent_dir != self.folder_path:
            self.populate_files(parent_dir)

    def on_item_activated(self):
        selection = self.listbox.curselection()
        if not selection:
            return
        idx = selection[0]
        _, full_path, is_dir = self.entries_list[idx]

        if is_dir:
            self.populate_files(full_path)
        else:
            all_files = [p for _, p, is_d in self.entries_list if not is_d]
            try:
                file_idx = all_files.index(full_path)
            except ValueError:
                file_idx = 0

            handler = self.parent
            while handler and not hasattr(handler, "play_audio_file"):
                handler = getattr(handler, "parent", None)

            if handler and hasattr(handler, "play_audio_file"):
                handler.play_audio_file(full_path, playlist=all_files, start_index=file_idx)
            else:
                ext = os.path.splitext(full_path)[1].lower()
                if ext in (".nsf", ".nsfe"):
                    MPVPlayerWindow(self.parent, full_path)

    def open_file_picker(self):
        initial_dir = self.folder_path if os.path.isdir(self.folder_path) else os.path.expanduser("~")
        file_path = filedialog.askopenfilename(
            title="Select Music File",
            initialdir=initial_dir,
            filetypes=[
                ("Audio & Emulated Files", "*.ay *.gbs *.gym *.hes *.kss *.nsf *.nsfe *.sap *.spc *.vgm *.vgz *.usf *.miniusf *.gsf *.minigsf *.sid *.mp3 *.ogg *.flac *.wav *.m4a *.aac *.opus *.wma *.ape *.wv *.mod *.s3m *.xm *.it *.mka *.tta *.ac3 *.dts *.aiff *.aifc *.au *.ra *.mid *.MID *.xma *.XMA *.xmd *.XMD *.2sf *.mini2sf *.psf *.minipsf *.psf2 *.minipsf2 *.dsp *.ast *.brstm *.rstm *.brwav *.rwav *.brsar *.rsar *.bfstm *.fstm *.bfwav *.fwav *.bfsar *.fsar *.vag *.adpcm *.at3"),
                ("All Files", "*.*")
            ]
        )
        if file_path:
            handler = self.parent
            while handler and not hasattr(handler, "play_audio_file"):
                handler = getattr(handler, "parent", None)
            if handler and hasattr(handler, "play_audio_file"):
                handler.play_audio_file(file_path)
            else:
                ext = os.path.splitext(file_path)[1].lower()
                if ext in (".nsf", ".nsfe"):
                    MPVPlayerWindow(self.parent, file_path)

    def browse_extracted_folder(self):
        try:
            if os.path.exists(self.folder_path):
                subprocess.Popen(["xdg-open", self.folder_path])
        except Exception as e:
            messagebox.showerror("Error", f"Could not open folder:\n\n{e}")