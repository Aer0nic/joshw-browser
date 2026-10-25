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
import ttkbootstrap as tb
import weakref
import sounddevice as sd
from config import get_cache_directory, is_emulated_file
from tkinter import ttk, messagebox, filedialog

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


class ExtractedFilesBrowserWindow(tb.Toplevel):
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

    def get_dynamic_hover_color(self):
        try:
            # Check parent window theme if available
            if hasattr(self.parent, "style") and self.parent.style:
                theme_name = self.parent.style.theme_use().lower()
                if "dark" in theme_name:
                    return "#2a3b4c"
        except Exception:
            pass
        return "#e8f0fe"

    def on_listbox_motion(self, event):
        idx = self.listbox.nearest(event.y)
        hover_color = self.get_dynamic_hover_color()
        
        for i in range(self.listbox.size()):
            if i == idx:
                self.listbox.itemconfig(i, background=hover_color)
            else:
                self.listbox.itemconfig(i, background="")

    def on_listbox_leave(self, event):
        for i in range(self.listbox.size()):
            self.listbox.itemconfig(i, background="")
    
    def build_ui(self):
        padding = tb.Frame(self, padding=10)
        padding.pack(fill="both", expand=True)

        top_frame = tb.Frame(padding)
        top_frame.pack(fill="x", pady=(0, 6))

        self.up_btn = tb.Button(top_frame, text="Up", width=5, command=self.go_up)
        self.up_btn.pack(side="left", padx=(0, 6))

        self.dir_label = tb.Label(top_frame, text=f"Directory: {self.folder_path}", font=("Arial", 9, "bold"), wraplength=440)
        self.dir_label.pack(side="left", fill="x", expand=True)

        list_frame = tb.Frame(padding)
        list_frame.pack(fill="both", expand=True, pady=(0, 10))

        self.listbox = tb.Listbox(list_frame, font=("Arial", 9), selectmode="single", exportselection=False)
        self.listbox.pack(side="left", fill="both", expand=True)

        self.listbox.bind("<Motion>", self.on_listbox_motion)
        self.listbox.bind("<Leave>", self.on_listbox_leave)

        scrollbar = tb.Scrollbar(list_frame, orient="vertical", command=self.listbox.yview)
        scrollbar.pack(side="right", fill="y")
        self.listbox.configure(yscrollcommand=scrollbar.set)

        self.listbox.bind("<Double-1>", lambda e: self.on_item_activated())

        btn_frame = tb.Frame(padding)
        btn_frame.pack(fill="x")

        tb.Button(btn_frame, text="Play / Open", command=self.on_item_activated).pack(side="left", padx=4)
        tb.Button(btn_frame, text="Open File Picker...", command=self.open_file_picker).pack(side="left", padx=4)
        tb.Button(btn_frame, text="Open in System Browser", command=self.browse_extracted_folder).pack(side="left", expand=True, padx=4)
        tb.Button(btn_frame, text="Close", command=self.destroy).pack(side="right", padx=4)

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
            return

        ext = os.path.splitext(full_path)[1].lower()
        playbae_exts = {".mid", ".midi", ".kar", ".rmi", ".rmf", ".zmf", ".xmf", ".mxmf", ".mthc"}

        if ext in playbae_exts:
            playbae_files = sorted([
                p for _, p, is_d in self.entries_list 
                if not is_d and os.path.splitext(p)[1].lower() in playbae_exts
            ])
            if not playbae_files:
                playbae_files = [full_path]
            try:
                file_idx = playbae_files.index(full_path)
            except ValueError:
                file_idx = 0

            handler = self.parent
            while handler and not hasattr(handler, "play_audio_file"):
                handler = getattr(handler, "parent", None)

            if handler and hasattr(handler, "play_audio_file"):
                handler.play_audio_file(full_path, playlist=playbae_files, start_index=file_idx)
            else:
                from neobae_player import PlayBAEPlayerWindow
                PlayBAEPlayerWindow(self.parent, full_path, playlist=playbae_files, start_index=file_idx)
            return

        if ext == ".xmi":
            folder = os.path.dirname(full_path)
            sh_path = os.path.join(get_cache_directory(), "xmi2mid.sh")
            
            if not os.path.exists(sh_path):
                try:
                    os.makedirs(get_cache_directory(), exist_ok=True)
                    script_content = """#!/usr/bin/env bash
folder="${1:-}"
if [[ -z "$folder" || ! -d "$folder" ]]; then
    echo "Usage: $0 DIRECTORY"
    exit 1
fi
mapfile -d '' files < <(find "$folder" -type f -iname '*.xmi' -print0)
if ((${#files[@]} == 0)); then
    echo "No .xmi files found in: $folder"
    exit 1
fi
for file in "${files[@]}"; do
    output="${file%.*}.mid"
    if [[ -e "$output" ]]; then
        echo "Output already exists; nothing converted or deleted: $output"
        exit 1
    fi
done
for file in "${files[@]}"; do
    output="${file%.*}.mid"
    echo "Converting: $file -> $output"
    if ! wildmidi -x "$output" "$file"; then
        echo "Conversion failed; XMI originals have been kept."
        exit 1
    fi
done
for file in "${files[@]}"; do
    rm -- "$file"
done
echo "Conversion succeeded; XMI originals deleted."
"""
                    with open(sh_path, "w", encoding="utf-8") as f:
                        f.write(script_content)
                    os.chmod(sh_path, 0o755)
                except Exception:
                    pass

            try:
                subprocess.run([sh_path, folder + "/"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
            except Exception as e:
                messagebox.showerror("XMI Conversion Error", f"Failed to convert XMI files:\n\n{e}")
                return

            mid_files = sorted([
                os.path.join(folder, f) for f in os.listdir(folder)
                if os.path.isfile(os.path.join(folder, f)) and f.lower().endswith((".mid", ".midi"))
            ])
            if mid_files:
                target_mid = f"{os.path.splitext(full_path)[0]}.mid"
                start_idx = mid_files.index(target_mid) if target_mid in mid_files else 0
                
                from neobae_player import PlayBAEPlayerWindow
                PlayBAEPlayerWindow(self.parent, mid_files[start_idx], playlist=mid_files, start_index=start_idx)
                self.destroy()
                return

        if ext == ".miniusf":
            all_files = sorted([
                p for _, p, is_d in self.entries_list 
                if not is_d and p.lower().endswith(".miniusf")
            ])
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
            messagebox.showerror("Error", f"No handler available to play: {os.path.basename(full_path)}")

    def open_file_picker(self):
        initial_dir = self.folder_path if os.path.isdir(self.folder_path) else os.path.expanduser("~")
        file_path = filedialog.askopenfilename(
            title="Select Music File",
            initialdir=initial_dir,
            filetypes=[("All Files", "*.*")]
        )
        if file_path:
            handler = self.parent
            while handler and not hasattr(handler, "play_audio_file"):
                handler = getattr(handler, "parent", None)
            if handler and hasattr(handler, "play_audio_file"):
                handler.play_audio_file(file_path)

    def browse_extracted_folder(self):
        try:
            if os.path.exists(self.folder_path):
                subprocess.Popen(["xdg-open", self.folder_path])
        except Exception as e:
            messagebox.showerror("Error", f"Could not open folder:\n\n{e}")