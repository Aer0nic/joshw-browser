import os
import sys
import time
import subprocess
import threading
import json
import re
import signal
import random
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from config import get_cache_directory

def get_shared_settings_path():
    cache_dir = Path(get_cache_directory())
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / "settings.json"

def load_saved_volume():
    try:
        settings_path = get_shared_settings_path()
        if settings_path.exists():
            data = json.loads(settings_path.read_text(encoding="utf-8"))
            for key in ("player_volume", "volume"):
                if key in data:
                    return float(data[key])
    except Exception:
        pass
    return 100.0

def save_shared_volume(vol):
    try:
        settings_path = get_shared_settings_path()
        data = {}
        if settings_path.exists():
            try:
                data = json.loads(settings_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        val_float = float(vol)
        data["volume"] = val_float
        data["player_volume"] = val_float
        settings_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


class InteractiveProgressBar:
    """Purely visual progress bar element (non-draggable)."""
    def __init__(self, parent, get_duration_func=None, length=380, **kwargs):
        self.parent = parent
        self.get_duration = get_duration_func
        
        self.frame = ttk.Frame(parent)
        self.canvas = tk.Canvas(self.frame, width=length, height=18, bg="#e0e0e0", highlightthickness=1, relief="sunken")
        self.canvas.pack(fill="x", expand=True)
        
        self.progress_rect = self.canvas.create_rectangle(0, 0, 0, 18, fill="#3a7ebf", outline="")
        self.thumb = self.canvas.create_rectangle(0, 0, 6, 18, fill="#204a87", outline="")
        self.current_pct = 0.0

    def update_progress(self, current_seconds, duration_seconds):
        if not self.canvas.winfo_exists():
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


class USFPlayerWindow(tk.Toplevel):
    def __init__(self, parent=None, playlist=None, initial_index=0, audio_file=None, **kwargs):
        super().__init__(parent)
        self.parent = parent
        
        if playlist:
            self.playlist = list(playlist)
            self.current_index = initial_index if 0 <= initial_index < len(self.playlist) else 0
        elif audio_file:
            self.playlist = [audio_file]
            self.current_index = 0
        else:
            self.playlist = []
            self.current_index = 0

        self.audio_file = self.playlist[self.current_index] if self.playlist else ""

        self.process = None
        self.is_playing = False
        self.is_paused = False
        self.playback_thread = None
        self.playback_session_id = 0

        self.raw_duration_seconds = 180.0
        self.current_position_seconds = 0.0
        self.lazyusf_wpctl_id = None

        initial_vol = load_saved_volume()
        self.volume_var = tk.DoubleVar(value=initial_vol)
        self.volume_label_var = tk.StringVar(value=f"Volume: {int(initial_vol)}%")
        
        self.status_var = tk.StringVar(value="Stopped")
        self.seek_pct_var = tk.StringVar(value="0%")
        self.position_var = tk.StringVar(value="00:00 / 03:00")
        self.track_var = tk.StringVar(value="")
        self.duration_var = tk.StringVar(value="03:00")
        
        self.loop_chk_var = tk.BooleanVar(value=False)
        self.loop_count_var = tk.IntVar(value=1)
        self.loop_forever_var = tk.BooleanVar(value=False)

        self.protocol("WM_DELETE_WINDOW", self.close)

        self.build_ui()

        # Load custom taskbar/window icon using native tk.PhotoImage (.png) with persistent reference
        try:
            icon_path = Path(get_cache_directory()) / "usficon.png"
            if icon_path.exists():
                self.icon_photo = tk.PhotoImage(file=str(icon_path))
                self.iconphoto(False, self.icon_photo)
        except Exception as e:
            print(f"[USFPlayer] Could not load custom icon: {e}")

        if self.playlist:
            self.load_and_play_current()

    def build_ui(self):
        filename = os.path.basename(self.audio_file) if self.audio_file else "No file"
        self.title(f"USF Player - {filename}")
        self.geometry("480x620")
        self.minsize(440, 540)
        if self.parent:
            try:
                self.transient(self.parent)
            except Exception:
                pass

        padding = ttk.Frame(self, padding=12)
        padding.pack(fill="both", expand=True)

        # Header frame holding the browse button and banner side-by-side
        header_frame = ttk.Frame(padding)
        header_frame.pack(side="top", fill="x", pady=(0, 6))

        # Browse button sitting next to the banner
        browse_btn = tk.Button(header_frame, text="📁", font=("Arial", 12), width=3, command=self.on_browse_clicked)
        browse_btn.pack(side="left", anchor="center", padx=(0, 8))

        # Canvas banner header taking up the remaining width
        self.banner_canvas = tk.Canvas(header_frame, height=85, highlightthickness=0)
        self.banner_canvas.pack(side="left", fill="x", expand=True)

        # Load, stretch, and render banner image across the canvas dynamically while keeping transparency
        try:
            banner_path = Path(get_cache_directory()) / "usfbanner.png"
            if not banner_path.exists():
                banner_path = Path(os.path.expanduser("~/.config/joshw/usfbanner.png"))
            if banner_path.exists():
                from PIL import Image, ImageTk
                self.raw_banner_img = Image.open(str(banner_path)).convert("RGBA")
                self.banner_image_item = self.banner_canvas.create_image(0, 0, anchor="nw")

                def resize_banner(event):
                    if event.width > 1 and event.height > 1:
                        resized_pil = self.raw_banner_img.resize((event.width, event.height), Image.Resampling.LANCZOS)
                        self.banner_photo = ImageTk.PhotoImage(resized_pil)
                        self.banner_canvas.itemconfig(self.banner_image_item, image=self.banner_photo)

                self.banner_canvas.bind("<Configure>", resize_banner)
        except Exception as e:
            print(f"[USFPlayer] Could not load banner image: {e}")

        self.title_label = ttk.Label(padding, text=f"Now Playing:\n{filename}", font=("Arial", 9, "bold"), wraplength=350, justify="center")
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
            get_duration_func=lambda: self.raw_duration_seconds
        )
        self.progress_bar_widget.frame.pack(fill="x", expand=True)

        meta_frame = ttk.Frame(padding)
        meta_frame.pack(fill="x", pady=(0, 8))
        ttk.Label(meta_frame, textvariable=self.track_var, font=("Arial", 9, "bold")).pack(side="left", padx=5)
        ttk.Label(meta_frame, textvariable=self.duration_var, font=("Arial", 9)).pack(side="right", padx=5)

        vol_frame = ttk.Frame(padding)
        vol_frame.pack(fill="x", pady=(0, 8))
        ttk.Label(vol_frame, textvariable=self.volume_label_var, font=("Arial", 9)).pack(side="left", padx=5)
        self.volume_slider = ttk.Scale(vol_frame, from_=0, to=100, orient="horizontal", command=self.on_volume_slide)
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
        ttk.Button(controls, text="Stop", width=8, command=self.stop).grid(row=0, column=1, padx=2)
        ttk.Button(controls, text="Play/Pause", width=10, command=self.toggle_play_pause).grid(row=0, column=2, padx=2)
        ttk.Button(controls, text="Next >>|", width=8, command=self.next_track).grid(row=0, column=3, padx=2)
        ttk.Button(controls, text="Close", width=8, command=self.close).grid(row=0, column=4, padx=2)
                
        btn_bottom = ttk.Frame(padding)
        btn_bottom.pack(pady=(6, 4), fill="x")

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
          
        
    def populate_playlist_listbox(self):
        self.playlist_listbox.delete(0, "end")
        for p in self.playlist:
            self.playlist_listbox.insert("end", os.path.basename(p))
        if self.playlist and 0 <= self.current_index < len(self.playlist):
            self.playlist_listbox.selection_set(self.current_index)
            self.playlist_listbox.see(self.current_index)
            try:
                top_idx = max(0, self.current_index - 5)
                self.playlist_listbox.yview(top_idx)
            except Exception:
                pass

    def on_browse_clicked(self):
        """Opens file dialog and populates playlist with strictly .miniusf files only."""
        initial_dir = os.path.expanduser("~")
        try:
            from config import load_settings
            settings = load_settings()
            if settings and "default_load_music_dir" in settings:
                cfg_dir = settings["default_load_music_dir"]
                if cfg_dir and os.path.exists(cfg_dir):
                    initial_dir = cfg_dir
        except Exception:
            if self.audio_file:
                initial_dir = os.path.dirname(self.audio_file)

        chosen_file = filedialog.askopenfilename(
            title="Open MiniUSF File",
            initialdir=initial_dir,
            filetypes=[
                ("MiniUSF Files", "*.miniusf"),
                ("All Files", "*.*")
            ]
        )

        if not chosen_file:
            return

        chosen_path = Path(chosen_file)
        target_dir = chosen_path.parent

        # Strictly filter for .miniusf files only, ignoring everything else
        new_playlist = sorted([
            str(p) for p in target_dir.glob("*") 
            if p.is_file() and p.suffix.lower() == ".miniusf"
        ])

        if not new_playlist:
            new_playlist = [str(chosen_path)]

        new_index = 0
        if str(chosen_path) in new_playlist:
            new_index = new_playlist.index(str(chosen_path))

        self.stop_current_process()
        self.destroy()

        try:
            parent_app = self.master
            new_player = USFPlayerWindow(parent_app, new_playlist, new_index)
            new_player.focus()
        except Exception as e:
            print(f"Error spawning new USF player window: {e}")

    def on_playlist_select(self, event):
        selection = self.playlist_listbox.curselection()
        if selection:
            idx = selection[0]
            if idx != self.current_index:
                self.current_index = idx
                self.load_and_play_current()

    def on_loop_checkbox_changed(self):
        if self.loop_chk_var.get():
            self.loop_forever_var.set(False)
        if self.playlist and 0 <= self.current_index < len(self.playlist):
            self.load_and_play_current()

    def on_loop_forever_changed(self):
        if self.loop_forever_var.get():
            self.loop_chk_var.set(False)
        if self.playlist and 0 <= self.current_index < len(self.playlist):
            self.load_and_play_current()

    def on_loop_spinbox_changed(self):
        # Dynamically recalculate visual duration on spinbox change without restarting the process
        if not self.loop_forever_var.get() and self.audio_file:
            parsed_length, parsed_fade = self.parse_length_and_fade_from_strings(self.audio_file)
            multiplier = 1 + self.loop_count_var.get() if self.loop_chk_var.get() else 1
            effective_length = parsed_length * multiplier
            self.raw_duration_seconds = effective_length + parsed_fade
            formatted_dur = self.format_time(self.raw_duration_seconds)
            self.duration_var.set(formatted_dur)

    def on_volume_slide(self, val):
        vol = float(val)
        self.volume_var.set(vol)
        self.volume_label_var.set(f"Volume: {int(vol)}%")
        save_shared_volume(vol)

        if self.lazyusf_wpctl_id:
            try:
                subprocess.run(
                    ["wpctl", "set-volume", str(self.lazyusf_wpctl_id), f"{vol / 100.0:.2f}"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL
                )
            except Exception:
                pass

    def format_time(self, seconds):
        if seconds < 0:
            seconds = 0
        total_secs = int(round(seconds))
        h = total_secs // 3600
        m = (total_secs % 3600) // 60
        s = total_secs % 60
        if h > 0:
            return f"{h:02d}:{m:02d}:{s:02d}"
        return f"{m:02d}:{s:02d}"

    def parse_length_and_fade_from_strings(self, file_path):
        """Extracts length and fade metadata from headers or companion files synchronously."""
        length_seconds = 180.0
        fade_seconds = 0.0
        try:
            output = ""
            file_p = Path(file_path)
            parent_dir = file_p.parent

            cmd = f'strings -a "{file_path}" | grep -Ei "length|fade"'
            res = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=1.0)
            if res.stdout:
                output = res.stdout

            if ("length=" not in output.lower() or "fade=" not in output.lower()) and parent_dir.exists():
                for usf_file in parent_dir.glob("*.usf"):
                    sub_res = subprocess.run(f'strings -a "{usf_file}" | grep -Ei "length|fade"', shell=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=1.0)
                    if sub_res.stdout:
                        output += "\n" + sub_res.stdout

            if output:
                for line in output.splitlines():
                    line_lower = line.lower()
                    if "length=" in line_lower:
                        parts = line.split("=", 1)
                        if len(parts) > 1:
                            val_str = parts[1].strip()
                            if ":" in val_str:
                                time_parts = val_str.split(":")
                                if len(time_parts) == 2:
                                    length_seconds = float(time_parts[0]) * 60 + float(time_parts[1])
                                elif len(time_parts) == 3:
                                    length_seconds = float(time_parts[0]) * 3600 + float(time_parts[1]) * 60 + float(time_parts[2])
                            else:
                                match = re.search(r'([\d\.]+)', val_str)
                                if match:
                                    length_seconds = float(match.group(1))
                    elif "fade=" in line_lower:
                        parts = line.split("=", 1)
                        if len(parts) > 1:
                            val_str = parts[1].strip()
                            if ":" in val_str:
                                time_parts = val_str.split(":")
                                if len(time_parts) == 2:
                                    fade_seconds = float(time_parts[0]) * 60 + float(time_parts[1])
                                elif len(time_parts) == 3:
                                    fade_seconds = float(time_parts[0]) * 3600 + float(time_parts[1]) * 60 + float(time_parts[2])
                            else:
                                match = re.search(r'([\d\.]+)', val_str)
                                if match:
                                    fade_seconds = float(match.group(1))
        except Exception as e:
            print(f"[USFPlayer] Error parsing length and fade: {e}")
        
        return length_seconds, fade_seconds

    def has_length_tag_in_file(self, file_path):
        try:
            cmd = f'strings -a "{file_path}" | grep -Ei length'
            res = subprocess.run(cmd, shell=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=1.0)
            if res.stdout and "length=" in res.stdout.lower():
                return True
            parent_dir = Path(file_path).parent
            if parent_dir.exists():
                for usf_file in parent_dir.glob("*.usf"):
                    sub_res = subprocess.run(f'strings -a "{usf_file}" | grep -Ei length', shell=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, timeout=1.0)
                    if sub_res.stdout and "length=" in sub_res.stdout.lower():
                        return True
        except Exception:
            pass
        return False

    def cleanup_au_file(self, file_path):
        if not file_path:
            return
        try:
            au_path = Path(str(file_path) + ".au")
            if au_path.exists():
                au_path.unlink()
        except Exception as e:
            print(f"[USFPlayer] Error cleaning up .au file: {e}")

    def load_and_play_current(self):
        self.stop_current_process()

        if not self.playlist or not (0 <= self.current_index < len(self.playlist)):
            return

        self.audio_file = self.playlist[self.current_index]
        filename = os.path.basename(self.audio_file)
        self.title(f"USF Player - {filename}")
        self.title_label.configure(text=f"Now Playing:\n{filename}")

        self.populate_playlist_listbox()

        self.current_position_seconds = 0.0
        self.seek_pct_var.set("0%")
        self.progress_bar_widget.update_progress(0.0, self.raw_duration_seconds)

        parsed_length, parsed_fade = self.parse_length_and_fade_from_strings(self.audio_file)
        has_explicit_length = parsed_length != 180.0 or self.has_length_tag_in_file(self.audio_file)

        is_forever = self.loop_forever_var.get() or not has_explicit_length

        if not is_forever:
            multiplier = 1 + self.loop_count_var.get() if self.loop_chk_var.get() else 1
            effective_length = parsed_length * multiplier
            self.raw_duration_seconds = effective_length + parsed_fade
            formatted_dur = self.format_time(self.raw_duration_seconds)
            self.duration_var.set(formatted_dur)
            self.position_var.set(f"00:00 / {formatted_dur}")
        else:
            self.raw_duration_seconds = 0.0
            self.duration_var.set("forever")
            self.position_var.set("00:00 / forever")

        self.track_var.set(f"Track {self.current_index + 1} of {len(self.playlist)}")

        self.is_playing = True
        self.is_paused = False
        self.status_var.set("Playing")
        self.lazyusf_wpctl_id = None

        self.playback_thread = threading.Thread(
            target=self._playback_worker, 
            args=(self.audio_file, is_forever, self.playback_session_id), 
            daemon=True
        )
        self.playback_thread.start()

    def _resolve_wpctl_stream_id(self):
        awk_cmd = "wpctl status | awk '/Streams:/ {in_streams=1; next} in_streams && /libao\\[lazyusf\\]/ {sub(/\\.$/, \"\", $1); print $1; exit} in_streams && /^[^[:space:]]/ {exit}'"
        
        for _ in range(30):
            if not self.is_playing or not self.process or self.process.poll() is not None:
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
                        self.lazyusf_wpctl_id = stream_id
                        current_vol = self.volume_var.get()
                        subprocess.run(
                            ["wpctl", "set-volume", str(stream_id), f"{current_vol / 100.0:.2f}"],
                            stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL
                        )
                        return
            except Exception:
                pass
            time.sleep(0.1)

    def _playback_worker(self, file_path, is_forever, session_id):
        cache_dir = Path(get_cache_directory())
        lazyusf_bin = cache_dir / "lazyusf"
        if not lazyusf_bin.exists():
            lazyusf_bin = Path(os.path.expanduser("~/.config/joshw/lazyusf"))
        if not lazyusf_bin.exists():
            return

        cmd = [str(lazyusf_bin), "--playback"]
        if is_forever:
            cmd.append("--forever")
        else:
            cmd.extend(["-f", "2"])
        cmd.append(str(file_path))

        try:
            proc = subprocess.Popen(
                cmd, 
                stdout=subprocess.PIPE, 
                stderr=subprocess.DEVNULL, 
                text=True, 
                bufsize=1
            )
            
            # Guard against race conditions if session changed before Popen completed
            if session_id != self.playback_session_id:
                try:
                    proc.terminate()
                    proc.wait(timeout=0.2)
                except Exception:
                    pass
                return

            self.process = proc
            threading.Thread(target=self._resolve_wpctl_stream_id, daemon=True).start()

            while self.process and self.process.poll() is None:
                line = self.process.stdout.readline()
                if not line:
                    break
                
                cleaned = line.strip()
                if cleaned:
                    try:
                        minutes = float(cleaned)
                        current_secs = minutes * 60.0
                        self.current_position_seconds = current_secs

                        if not is_forever and self.raw_duration_seconds > 0:
                            dur = self.raw_duration_seconds
                            if current_secs >= dur:
                                current_secs = dur
                                pct = 100.0
                            else:
                                pct = (current_secs / dur) * 100.0
                        else:
                            dur = 0.0
                            pct = 0.0

                        pos_text = f"{self.format_time(current_secs)} / {'forever' if is_forever else self.format_time(dur)}"

                        def update_ui(p=pct, pt=pos_text, cur=current_secs, d=dur if not is_forever else 180.0):
                            if self.winfo_exists() and self.is_playing:
                                self.seek_pct_var.set(f"{int(p)}%")
                                self.position_var.set(pt)
                                self.progress_bar_widget.update_progress(cur, d if not is_forever else 0.0)

                        self.after(0, update_ui)

                    except ValueError:
                        pass

            if self.process is not None:
                try:
                    self.process.wait()
                except Exception:
                    pass

            self.cleanup_au_file(file_path)

            if self.is_playing and not self.is_paused and session_id == self.playback_session_id:
                self.after(0, self.next_track)

        except Exception as e:
            print(f"Playback error: {e}")

    def toggle_play_pause(self):
        if not self.process or self.process.poll() is not None:
            if self.playlist and 0 <= self.current_index < len(self.playlist):
                self.load_and_play_current()
            return

        if not self.is_paused:
            try:
                if sys.platform == "win32":
                    subprocess.run(["powershell", "-Command", f"Suspend-Process -Id {self.process.pid}"], capture_output=True)
                else:
                    self.process.send_signal(signal.SIGSTOP)
                self.is_paused = True
                self.status_var.set("Paused")
            except Exception as e:
                print(f"[USFPlayer] Error pausing process: {e}")
        else:
            try:
                if sys.platform == "win32":
                    subprocess.run(["powershell", "-Command", f"Resume-Process -Id {self.process.pid}"], capture_output=True)
                else:
                    self.process.send_signal(signal.SIGCONT)
                self.is_paused = False
                self.status_var.set("Playing")
            except Exception as e:
                print(f"[USFPlayer] Error resuming process: {e}")

    def stop_current_process(self):
        self.playback_session_id += 1
        self.is_playing = False
        target_file = self.audio_file
        self.lazyusf_wpctl_id = None
        
        proc = self.process
        self.process = None
        
        if proc is not None:
            try:
                proc.terminate()
                proc.wait(timeout=0.3)
                if proc.poll() is None:
                    proc.kill()
            except Exception:
                pass
        
        if target_file:
            self.cleanup_au_file(target_file)

    def stop(self):
        self.stop_current_process()
        self.status_var.set("Stopped")
        self.position_var.set(f"00:00 / {self.format_time(self.raw_duration_seconds)}")
        self.seek_pct_var.set("0%")
        self.progress_bar_widget.update_progress(0.0, self.raw_duration_seconds)

    def next_track(self):
        if not self.playlist:
            return
        self.current_index = (self.current_index + 1) % len(self.playlist)
        self.load_and_play_current()

    def prev_track(self):
        if not self.playlist:
            return
        self.current_index = (self.current_index - 1) % len(self.playlist)
        self.load_and_play_current()

    def random_track(self):
        if len(self.playlist) <= 1:
            return
        
        new_index = self.current_index
        while new_index == self.current_index:
            new_index = random.randint(0, len(self.playlist) - 1)
            
        self.current_index = new_index
        self.load_and_play_current()

    def close(self):
        self.stop_current_process()
        try:
            self.destroy()
        except Exception:
            pass

USFPlayer = USFPlayerWindow