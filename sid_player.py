import os
import re
import shutil
import signal
import subprocess
import threading
import time
import tkinter as tk
from tkinter import ttk, messagebox
from config import resource_path, get_binary_path
from players import (
    get_saved_player_volume,
    register_volume_player,
    set_player_volume,
    InteractiveProgressBar,
    ExtractedFilesBrowserWindow,
    get_next_track_index
)

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

        try:
            icon_file = resource_path("assets/icons/player_icon.png")
            if icon_file.exists():
                self.iconphoto(False, tk.PhotoImage(file=str(icon_file)))
        except Exception:
            pass

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
        idx = self.subtrack_listbox.nearest(event.y)
        hover_color = self.get_dynamic_hover_color()
        for i in range(self.subtrack_listbox.size()):
            if i == idx:
                self.subtrack_listbox.itemconfig(i, background=hover_color)
            else:
                self.subtrack_listbox.itemconfig(i, background="")

    def on_listbox_leave(self, event):
        for i in range(self.subtrack_listbox.size()):
            self.subtrack_listbox.itemconfig(i, background="")

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
        self.volume_slider = ttk.Scale(vol_frame, from_=0, to=100, orient="horizontal", command=self.on_volume_slide)
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
        self.subtrack_listbox.bind("<Motion>", self.on_listbox_motion)
        self.subtrack_listbox.bind("<Leave>", self.on_listbox_leave)
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
        """Adjust stream/process volume dynamically via PulseAudio/PipeWire system tools."""
        if not self.winfo_exists() or not self.sid_proc or self.sid_proc.poll() is not None:
            return

        vol_percent = int(self.volume_var.get())
        pid = self.sid_proc.pid

        # Attempt volume adjustment using standard Linux process volume tools (pactl / pamixer / wpctl)
        try:
            if shutil.which("pactl"):
                # Find the sink-input index associated with this process PID
                out = subprocess.check_output(["pactl", "list", "sink-inputs"], text=True, stderr=subprocess.DEVNULL)
                current_input = None
                for line in out.splitlines():
                    if line.strip().startswith("Sink Input #"):
                        current_input = line.split("#")[1].strip()
                    elif f"application.process.id = \"{pid}\"" in line and current_input:
                        subprocess.run(["pactl", "set-sink-input-volume", current_input, f"{vol_percent}%"], check=False)
                        break
            elif shutil.which("pamixer"):
                # Fallback to pamixer if pactl isn't available
                subprocess.run(["pamixer", "--pid", str(pid), "--set-volume", str(vol_percent)], check=False)
        except Exception:
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

        sidplay_bin = str(get_binary_path("sidplayfp"))
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
            
            filename = os.path.basename(self.audio_file)
            curr_sub = self.subtracks[self.current_subtrack_idx]
            
            self.title(f"sidplayfp Controller - {filename} (Track {curr_sub})")

            # Apply target process volume immediately after spawn
            self.after(100, self.apply_live_volume)

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