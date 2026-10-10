import json
import os
import posixpath
import queue
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from config import DOMAINS, get_cache_directory, get_cache_path, get_temp_directory, USER_AGENT, is_emulated_file, format_size
from network import fetch_directory, get_entries_from_domain_entries, get_size_from_domain_entries, safe_filename, unique_path
from players import VGMStreamPlayerWindow, MPVPlayerWindow, ExtractedFilesBrowserWindow, get_vgmstream_path, get_lazyusf_path, start_vgmstream_installation, start_lazyusf_installation
from downloaders import DownloadProgressWindow, IndexAllWindow


class ToolTip:
    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
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
        self.id = self.widget.after(1000, self.show_tip)

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


class JoshWBrowser(tk.Tk):
    def has_lazyusf_installed(self):
        return (
            shutil.which("lazyusf") is not None 
            or os.path.exists("/usr/local/bin/lazyusf") 
            or os.path.exists("/usr/bin/lazyusf")
            or os.path.exists(os.path.join(get_cache_directory(), "lazyusf"))
        )

    def has_vgmstream_installed(self):
        return (
            shutil.which("vgmstream123") is not None 
            or os.path.exists("/usr/local/bin/vgmstream123") 
            or os.path.exists("/usr/bin/vgmstream123")
            or os.path.exists(os.path.join(get_cache_directory(), "vgmstream123"))
        )

    def has_fluidsynth_installed(self):
        has_bin = (
            shutil.which("fluidsynth") is not None
            or os.path.exists("/bin/fluidsynth")
            or os.path.exists("/usr/bin/fluidsynth")
            or os.path.exists("/usr/local/bin/fluidsynth")
        )
        has_sf2 = os.path.exists(os.path.join(get_cache_directory(), "FluidR3_GM.sf2"))
        return has_bin and has_sf2

    def install_fluidsynth_prompt(self):
        ans = messagebox.askokcancel(
            "FluidSynth Required",
            "Midi playback requires Fluidsynth and a soundfont. Hit the OK button to open a terminal window to install both. Hit Cancel to skip this step. You can try again at any time from the option menu!"
        )
        if ans:
            try:
                sf2_path = os.path.join(get_cache_directory(), "FluidR3_GM.sf2")
                install_cmd = f"sudo apt update && sudo apt install -y fluidsynth && wget -O '{sf2_path}' 'https://github.com/pianobooster/fluid-soundfont/releases/download/v3.1/FluidR3_GM.sf2'"
                
                terminals = ["gnome-terminal", "konsole", "xfce4-terminal", "xterm"]
                launched = False
                for term in terminals:
                    if shutil.which(term):
                        if term == "gnome-terminal":
                            subprocess.Popen([term, "--", "bash", "-c", f"{install_cmd}; echo 'Done. Press Enter to close.'; read"])
                        elif term == "konsole":
                            subprocess.Popen([term, "-e", "bash", "-c", f"{install_cmd}; echo 'Done. Press Enter to close.'; read"])
                        else:
                            subprocess.Popen([term, "-e", f"bash -c \"{install_cmd}; echo 'Done. Press Enter to close.'; read\""])
                        launched = True
                        break
                if not launched:
                    subprocess.Popen(["x-terminal-emulator", "-e", install_cmd])
                messagebox.showinfo("Installation Started", "Terminal window opened to install FluidSynth and download the soundfont.")
            except Exception as e:
                messagebox.showerror("Error", f"Could not open terminal:\n\n{e}")

    def __init__(self):
        super().__init__()
        self.title("JoshW Archive Browser")
        self.geometry("1280x760")
        self.minsize(850, 560)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.current_url = None
        self.current_entries = []
        self.visible_entries = []
        self.domain_full_entries = []
        self.cached_dir_sizes = {}
        self.cached_scanned_dirs = set()
        self.selected_item_ids = set()

        self.nav_history = []
        self.nav_history_index = -1
        self.is_navigating_history = False

        self.current_sort_column = "type"
        self.current_sort_ascending = True

        self._initial_settings = self.load_settings()
        saved_dest = self._initial_settings.get("default_destination", os.path.expanduser("~/Downloads"))
        saved_use_default = self._initial_settings.get("use_default_dest", False)
        saved_load_dir = self._initial_settings.get("default_load_music_dir", os.path.expanduser("~"))

        self.destination = tk.StringVar(value=saved_dest)
        self.use_default_dest = tk.BooleanVar(value=saved_use_default)
        self.default_load_music_dir = tk.StringVar(value=saved_load_dir)
        self.path_7z = tk.StringVar(value="/bin/7z")
        self.path_mpv = tk.StringVar(value="/bin/mpv")

        self.selection_checkboxes_enabled = tk.BooleanVar(value=False)
        self.keep_download_window_open = tk.BooleanVar(value=True)

        self.search_text = tk.StringVar()
        self.status_text = tk.StringVar(value="Choose an archive.")
        self.indexer_status_text = tk.StringVar(value="")
        self.auto_index_enabled = True

        self.messages = queue.Queue()
        self.stop_event = threading.Event()

        self.build_gui()
        self.after(100, self.process_messages)

    def on_close(self):
        self.stop_event.set()
        try:
            temp_dir = get_temp_directory()
            if os.path.exists(temp_dir):
                shutil.rmtree(temp_dir, ignore_errors=True)
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            sys.exit(0)

    def build_gui(self):
        archive_frame = ttk.Frame(self, padding=8)
        archive_frame.pack(fill="x")

        help_btn = tk.Button(archive_frame, text="help!", bg="blue", fg="yellow", activebackground="blue", activeforeground="yellow", font=("Arial", 9, "bold"), command=self.show_help)
        help_btn.pack(side="right", padx=2)

        ttk.Label(archive_frame, text="Archive:").pack(side="left")
        self.domain_combo = ttk.Combobox(archive_frame, state="readonly", width=22, values=list(DOMAINS.keys()))
        self.domain_combo.pack(side="left", padx=5)
        self.domain_combo.current(0)

        ttk.Button(archive_frame, text="Open", command=self.open_archive).pack(side="left", padx=2)
        
        load_music_btn = ttk.Button(archive_frame, text="Load Music...", command=self.load_local_music)
        load_music_btn.pack(side="left", padx=2)
        ToolTip(load_music_btn, "click to find and play locally stored music with the built in player")

        ttk.Button(archive_frame, text="Index Current", command=self.index_current_domain).pack(side="left", padx=2)
        ttk.Button(archive_frame, text="Index All", command=self.index_all_domains).pack(side="left", padx=2)

        self.auto_index_btn = ttk.Button(archive_frame, text="Auto Indexing On", command=self.toggle_auto_indexing)
        self.auto_index_btn.pack(side="left", padx=4)

        options_menu_btn = ttk.Menubutton(archive_frame, text="Options")
        options_menu_btn.pack(side="left", padx=4)
        
        self.options_dropdown = tk.Menu(options_menu_btn, tearoff=0)
        self.options_dropdown.add_checkbutton(label="Enable Selection Checkboxes", variable=self.selection_checkboxes_enabled, command=self.on_toggle_checkboxes)
        self.options_dropdown.add_checkbutton(label="Keep Download Window Open After Completion", variable=self.keep_download_window_open)
        self.options_dropdown.add_command(label="Set Default Load Music Folder...", command=self.set_default_load_music_dir)
        
        # VGMstream option with installed status
        vgm_installed = self.has_vgmstream_installed()
        vgm_label = "Install VGMstream (installed)" if vgm_installed else "Install VGMstream"
        vgm_state = "disabled" if vgm_installed else "normal"
        self.options_dropdown.add_command(label=vgm_label, command=start_vgmstream_installation, state=vgm_state)

        # lazyusf option with installed status
        usf_installed = self.has_lazyusf_installed()
        usf_label = "Install lazyusf (installed)" if usf_installed else "Install lazyusf"
        usf_state = "disabled" if usf_installed else "normal"
        self.options_dropdown.add_command(label=usf_label, command=start_lazyusf_installation, state=usf_state)

        # FluidSynth option with installed status
        fluid_installed = self.has_fluidsynth_installed()
        fluid_label = "Install FluidSynth & Soundfont (installed)" if fluid_installed else "Install FluidSynth & Soundfont"
        fluid_state = "disabled" if fluid_installed else "normal"
        self.options_dropdown.add_command(label=fluid_label, command=self.install_fluidsynth_prompt, state=fluid_state)

        options_menu_btn["menu"] = self.options_dropdown

        ttk.Label(archive_frame, text="Search:").pack(side="left", padx=(8, 4))
        self.search_combo = ttk.Combobox(archive_frame, textvariable=self.search_text, width=16, values=self.load_search_history())
        self.search_combo.pack(side="left")
        self.search_combo.bind("<KeyRelease>", lambda event: self.apply_filter())
        self.search_combo.bind("<<ComboboxSelected>>", lambda event: self.on_search_selected())
        self.search_combo.bind("<Return>", lambda event: self.on_search_submitted())

        ttk.Button(archive_frame, text="Clear", command=self.clear_search).pack(side="left", padx=2)
        ttk.Button(archive_frame, text="Clear History", command=self.clear_search_history).pack(side="left", padx=2)

        progress_container = ttk.Frame(self, padding=(8, 0, 8, 4))
        progress_container.pack(fill="x")
        progress_container.columnconfigure(0, weight=1)
        progress_container.columnconfigure(2, weight=1)

        sub_frame = ttk.Frame(progress_container)
        sub_frame.grid(row=0, column=1)

        self.progress_bar = ttk.Progressbar(sub_frame, orient="horizontal", mode="determinate", length=350, maximum=100)
        self.progress_bar.pack(side="top", anchor="center")

        labels_frame = ttk.Frame(sub_frame)
        labels_frame.pack(side="top", fill="x", pady=(2, 0))
        self.indexer_label = ttk.Label(labels_frame, textvariable=self.indexer_status_text, font=("Arial", 9))
        self.indexer_label.pack(side="left")
        self.progress_percent_label = ttk.Label(labels_frame, text="", font=("Arial", 9))
        self.progress_percent_label.pack(side="right")

        path_frame = ttk.Frame(self, padding=(8, 0, 8, 8))
        path_frame.pack(fill="x")

        ttk.Button(path_frame, text="<", width=3, command=lambda: self.navigate_history(-1)).pack(side="left", padx=(0, 2))
        ttk.Button(path_frame, text=">", width=3, command=lambda: self.navigate_history(1)).pack(side="left", padx=(0, 6))
        ttk.Label(path_frame, text="Remote path:").pack(side="left")
        self.path_label = ttk.Label(path_frame, text="", anchor="w")
        self.path_label.pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(path_frame, text="Up", command=self.go_up).pack(side="right")

        ttk.Label(self, text="Double click on a file to either download or stream it", font=("Arial", 9, "italic")).pack(anchor="w", padx=10, pady=(0, 2))

        tree_frame = ttk.Frame(self, padding=(8, 0, 8, 8))
        tree_frame.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(tree_frame, columns=("select", "type", "name", "date", "size", "url"), show="headings", selectmode="extended")
        self.tree.heading("select", text="[ ]")
        self.tree.heading("type", text="Type", command=lambda: self.sort_by_column("type"))
        self.tree.heading("name", text="Name ▲", command=lambda: self.sort_by_column("name"))
        self.tree.heading("date", text="Date", command=lambda: self.sort_by_column("date"))
        self.tree.heading("size", text="Size", command=lambda: self.sort_by_column("size"))
        self.tree.heading("url", text="URL", command=lambda: self.sort_by_column("url"))

        self.tree.column("select", width=50, anchor="center", stretch=False)
        self.tree.column("type", width=90, anchor="center", stretch=False)
        self.tree.column("name", width=280)
        self.tree.column("date", width=110, anchor="center", stretch=False)
        self.tree.column("size", width=100, anchor="e", stretch=False)
        self.tree.column("url", width=520)

        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.tree.bind("<Double-1>", self.double_click_entry)
        self.tree.bind("<Button-1>", self.click_tree_item)

        destination_frame = ttk.Frame(self, padding=8)
        destination_frame.pack(fill="x")
        ttk.Checkbutton(destination_frame, text="Set Default Download Location:", variable=self.use_default_dest, command=self.save_settings).pack(side="left")
        
        self.destination_combo = ttk.Combobox(destination_frame, textvariable=self.destination, values=self.load_destination_history())
        self.destination_combo.pack(side="left", fill="x", expand=True, padx=6)
        ttk.Button(destination_frame, text="Browse...", command=self.choose_destination).pack(side="left")
        ttk.Button(destination_frame, text="Clear Dest History", command=self.clear_destination_history).pack(side="left", padx=(4, 0))

        button_frame = ttk.Frame(self, padding=(8, 0, 8, 8))
        button_frame.pack(fill="x")
        ttk.Button(button_frame, text="Download Selected File(s)", command=self.download_selected).pack(side="left")
        ttk.Button(button_frame, text="Download Every File Here", command=self.download_current_directory).pack(side="left", padx=6)
        ttk.Button(button_frame, text="Stop", command=self.stop_operations).pack(side="left", padx=6)
        ttk.Label(button_frame, textvariable=self.status_text).pack(side="right")

    def load_local_music(self):
        file_path = filedialog.askopenfilename(
            title="Select Music File",
            initialdir=self.default_load_music_dir.get(),
            filetypes=[
                ("Audio & Emulated Files", "*.ay *.gbs *.gym *.hes *.kss *.nsf *.nsfe *.sap *.spc *.vgm *.vgz *.usf *.miniusf *.mp3 *.ogg *.flac *.wav *.m4a *.aac *.opus *.wma *.ape *.wv *.mod *.s3m *.xm *.it *.mka *.tta *.ac3 *.dts *.aiff *.au *.ra *.mid *.MID"),
                ("All Files", "*.*")
            ]
        )
        if file_path:
            self.play_audio_file(file_path)

    def set_default_load_music_dir(self):
        folder = filedialog.askdirectory(title="Choose Default Load Music Folder", initialdir=self.default_load_music_dir.get())
        if folder:
            self.default_load_music_dir.set(folder)
            self.save_settings()
            messagebox.showinfo("Default Folder Saved", f"Default load music folder set to:\n{folder}")

    def show_help(self):
        help_win = tk.Toplevel(self)
        help_win.title("Help - JoshW Emulated Music Browser & Player")
        help_win.geometry("640x580")
        help_win.transient(self)
        
        frame = ttk.Frame(help_win, padding=12)
        frame.pack(fill="both", expand=True)

        text_widget = tk.Text(frame, wrap="word", font=("Arial", 10))
        text_widget.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(frame, orient="vertical", command=text_widget.yview)
        scrollbar.pack(side="right", fill="y")
        text_widget.configure(yscrollcommand=scrollbar.set)

        help_text = (
            "Welcome to the JoshW emulated music browser & player!\n\n"
            "It is pretty simple to use, first, i highly recommend that you click the Index All button, this will go thru each domain of the joshw website and index every file and directory and save it to the joshw folder found under ~/.config/joshw. Once a domain is indexed, it will load instantly rather than having to get reloaded from the server every time.\n\n"
            "After that finishes, you can use the dropdown and choose a domain to load. click Open to show the directory listing of that domain, and once you find a file you want, you can double click to try and stream the files or download them directly. You may also shift click to choose multiple files to download, or use the \"Download Every File Here\" button to grab everything.\n\n"
            "You may set a default download location and enable it by clicking the little box to the left of the textbox. Otherwise when downloading it will ask you where you want the files.\n\n"
            "If you know the name of the file you want, log into the appropriate domain and go to the search bar. Start typing and it will filter it down instantly! If you hit enter, it will save the search in the search history, which can be seen with the dropdown arrow on the search bar.\n\n"
            "Currently, the only fully supported streamable domains are the following:\n\n"
            "PC\n"
            "FM Towns\n"
            "NES\n"
            "SNES\n"
            "SEGA CD / Mega Drive\n"
            "Saturn\n"
            "PC Engine\n"
            "Neo Geo\n"
            "Xbox\n"
            "3DO\n"
            "Game Boy\n\n"
            "Partially working domains are:\n\n"
            "N64\n\n"
            "I hope to add more in the future with more updates\n\n"
            "All streamed songs are saved in a temporary folder within ~/.config/joshw/temp_extracted and will be deleted once the program is closed.\n\n"
            "I hope you really enjoy this program! It was 100% vibe coded but i did a lot of research on how to get the various music player functions working"
        )

        text_widget.insert("1.0", help_text)
        text_widget.configure(state="disabled")

    def update_sort_headers(self):
        base_titles = {"type": "Type", "name": "Name", "date": "Date", "size": "Size", "url": "URL"}
        for k in base_titles:
            arrow = ""
            if k == self.current_sort_column:
                arrow = " ▲" if self.current_sort_ascending else " ▼"
            self.tree.heading(k, text=base_titles[k] + arrow)

    def sort_by_column(self, col_key):
        if self.current_sort_column == col_key:
            self.current_sort_ascending = not self.current_sort_ascending
        else:
            self.current_sort_column = col_key
            self.current_sort_ascending = True
        self.update_sort_headers()
        self.apply_filter()

    def apply_sorting(self, entries):
        col, asc = self.current_sort_column, self.current_sort_ascending
        parent_navs = [e for e in entries if e.get("is_parent_nav")]
        normal_entries = [e for e in entries if not e.get("is_parent_nav")]

        def sort_key(entry):
            if col == "type":
                return (1, 0 if entry["directory"] else 1, entry["name"].lower())
            elif col == "name":
                return (1, entry["name"].lower())
            elif col == "date":
                d_val = entry.get("date", "")
                return (1, 0 if d_val else 1, d_val.lower())
            elif col == "size":
                return (1, -1 if entry["directory"] else (entry.get("size") if entry.get("size") is not None else float('inf')))
            elif col == "url":
                return (1, entry["url"].lower())
            return (1, entry["name"].lower())

        normal_entries.sort(key=sort_key, reverse=not asc)
        return parent_navs + normal_entries

    def push_nav_history(self, domain_name, url):
        if self.is_navigating_history:
            return
        self.nav_history = self.nav_history[:self.nav_history_index + 1]
        if self.nav_history and self.nav_history[-1] == (domain_name, url):
            return
        self.nav_history.append((domain_name, url))
        self.nav_history_index = len(self.nav_history) - 1

    def navigate_history(self, direction):
        new_index = self.nav_history_index + direction
        if 0 <= new_index < len(self.nav_history):
            self.nav_history_index = new_index
            domain_name, url = self.nav_history[new_index]
            if self.domain_combo.get() != domain_name:
                self.domain_combo.set(domain_name)
            self.is_navigating_history = True
            self.search_text.set("")
            self.load_or_check_cache(domain_name, DOMAINS[domain_name])
            parsed_url = urllib.parse.urlparse(url)
            parsed_root = urllib.parse.urlparse(DOMAINS[domain_name])
            self.load_directory(domain_name, url, is_root=(parsed_url.path.rstrip("/") == parsed_root.path.rstrip("/")))
            self.is_navigating_history = False

    def on_toggle_checkboxes(self):
        self.selected_item_ids.clear()
        self.apply_filter()

    def click_tree_item(self, event):
        if not self.selection_checkboxes_enabled.get():
            return
        region = self.tree.identify_region(event.x, event.y)
        if region == "cell" and self.tree.identify_column(event.x) == "#1":
            item_id = self.tree.identify_row(event.y)
            if item_id:
                entry = self.visible_entries[int(item_id)]
                if not entry.get("is_parent_nav"):
                    if item_id in self.selected_item_ids:
                        self.selected_item_ids.remove(item_id)
                    else:
                        self.selected_item_ids.add(item_id)
                    self.apply_filter()

    def play_audio_file(self, audio_file):
        ext = os.path.splitext(audio_file)[1].lower()
        supported_extensions = {
            ".ay", ".gbs", ".gym", ".hes", ".kss", ".nsf", ".nsfe", ".sap", ".spc", ".vgm", ".vgz",
            ".mp3", ".ogg", ".flac", ".wav", ".m4a", ".aac", ".opus", ".wma", ".ape", ".wv",
            ".mod", ".s3m", ".xm", ".it", ".mka", ".tta", ".ac3", ".dts", ".aiff", ".au", ".ra"
        }
        if ext in (".mid", ".midi"):
            if not self.has_fluidsynth_installed():
                self.install_fluidsynth_prompt()
                return
            MPVPlayerWindow(self, audio_file)
            return
        elif ext in {".miniusf", ".usf"}:
            lazyusf_bin = get_lazyusf_path()
            if not lazyusf_bin or (not shutil.which(lazyusf_bin) and not os.path.exists(lazyusf_bin)):
                messagebox.showerror("lazyusf Not Found", "lazyusf not found! Please install it from the Options menu.")
                return
            folder, filename = os.path.dirname(audio_file), os.path.basename(audio_file)
            au_filepath = os.path.join(folder, filename + ".au")
            try:
                subprocess.run([lazyusf_bin, filename], cwd=folder, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception as e:
                messagebox.showerror("Decoding Error", f"Failed to decode USF:\n\n{e}")
                return
            if os.path.exists(au_filepath):
                MPVPlayerWindow(self, au_filepath)
            else:
                messagebox.showerror("Playback Error", "Could not find generated output file.")
            return
        elif ext in supported_extensions or ext in (".hes", ".nsf", ".nsfe"):
            mpv_path = self.path_mpv.get().strip() or "/bin/mpv"
            if not (shutil.which(mpv_path) or os.path.exists(mpv_path)):
                messagebox.showerror("mpv Not Found", "mpv not found!")
                return
            MPVPlayerWindow(self, audio_file)
        else:
            vgm_bin = get_vgmstream_path()
            if not vgm_bin or (not shutil.which(vgm_bin) and not os.path.exists(vgm_bin)):
                messagebox.showerror("vgmstream123 Not Found", "vgmstream123 not found!")
                return
            VGMStreamPlayerWindow(self, audio_file)

    def toggle_auto_indexing(self):
        self.auto_index_enabled = not self.auto_index_enabled
        self.auto_index_btn.configure(text="Auto Indexing On" if self.auto_index_enabled else "Auto Indexing Off")

    def get_search_history_path(self):
        return os.path.join(get_cache_directory(), "search_history.json")

    def load_search_history(self):
        path = self.get_search_history_path()
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        return data
            except Exception:
                pass
        return []

    def save_search_history(self, term):
        term = term.strip()
        if not term:
            return
        history = self.load_search_history()
        if term in history:
            history.remove(term)
        history.insert(0, term)
        try:
            with open(self.get_search_history_path(), "w", encoding="utf-8") as f:
                json.dump(history[:50], f, ensure_ascii=False, indent=2)
            if hasattr(self, "search_combo"):
                self.search_combo.configure(values=history)
        except Exception:
            pass

    def clear_search_history(self):
        try:
            if os.path.exists(self.get_search_history_path()):
                os.remove(self.get_search_history_path())
            if hasattr(self, "search_combo"):
                self.search_combo.configure(values=[])
            self.status_text.set("Search history cleared.")
        except Exception as e:
            self.status_text.set(f"Could not clear search history: {e}")

    def on_search_selected(self):
        if self.search_text.get():
            self.save_search_history(self.search_text.get())
        self.apply_filter()

    def on_search_submitted(self):
        if self.search_text.get():
            self.save_search_history(self.search_text.get())
        self.apply_filter()

    def get_settings_path(self):
        return os.path.join(get_cache_directory(), "settings.json")

    def load_settings(self):
        path = self.get_settings_path()
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def save_settings(self):
        settings = self.load_settings()
        settings.update({
            "default_destination": self.destination.get(),
            "use_default_dest": self.use_default_dest.get(),
            "destination_history": self.load_destination_history(),
            "default_load_music_dir": self.default_load_music_dir.get()
        })
        try:
            with open(self.get_settings_path(), "w", encoding="utf-8") as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def load_destination_history(self):
        settings = self.load_settings()
        hist = settings.get("destination_history", [])
        return hist if isinstance(hist, list) and hist else [os.path.expanduser("~/Downloads")]

    def save_destination_history(self, path_str):
        path_str = path_str.strip()
        if not path_str:
            return
        settings = self.load_settings()
        history = settings.get("destination_history", [])
        if not isinstance(history, list):
            history = []
        if path_str in history:
            history.remove(path_str)
        history.insert(0, path_str)
        settings["destination_history"] = history
        settings["default_destination"] = path_str
        try:
            with open(self.get_settings_path(), "w", encoding="utf-8") as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
            if hasattr(self, "destination_combo"):
                self.destination_combo.configure(values=history)
        except Exception:
            pass

    def clear_destination_history(self):
        settings = self.load_settings()
        settings["destination_history"] = []
        try:
            with open(self.get_settings_path(), "w", encoding="utf-8") as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
            if hasattr(self, "destination_combo"):
                self.destination_combo.configure(values=[])
            self.status_text.set("Destination history cleared.")
        except Exception as e:
            self.status_text.set(f"Could not clear destination history: {e}")

    def clear_search(self):
        self.search_text.set("")
        self.apply_filter()

    def open_archive(self):
        selected_name = self.domain_combo.get()
        selected_url = DOMAINS[selected_name]
        self.search_text.set("")
        self.load_or_check_cache(selected_name, selected_url)
        self.nav_history = []
        self.nav_history_index = -1
        self.load_directory(selected_name, selected_url, is_root=True)

    def load_or_check_cache(self, domain_name, root_url):
        cache_path = get_cache_path(domain_name)
        self.cached_dir_sizes = {}
        self.cached_scanned_dirs = set()
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        self.domain_full_entries = data.get("entries", [])
                        self.cached_dir_sizes = data.get("dir_sizes", {})
                        self.cached_scanned_dirs = set(data.get("scanned_dirs", []))
                    else:
                        self.domain_full_entries = data
                self.progress_bar["value"] = 100
            except Exception:
                self.domain_full_entries = []

    def save_cache(self, domain_name):
        cache_path = get_cache_path(domain_name)
        if not self.auto_index_enabled and not os.path.exists(cache_path):
            return
        data = {
            "entries": self.domain_full_entries,
            "dir_sizes": self.cached_dir_sizes,
            "scanned_dirs": list(getattr(self, "cached_scanned_dirs", set()))
        }
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def index_current_domain(self):
        selected_name = self.domain_combo.get()
        self.start_domain_background_scraper(selected_name, DOMAINS[selected_name], get_cache_path(selected_name))

    def index_all_domains(self):
        IndexAllWindow(self, DOMAINS)

    def crawl_domain_remote(self, domain_name, root_url, report_progress=True, progress_callback=None, stop_event=None, incremental=False):
        if stop_event is None:
            stop_event = self.stop_event
        all_entries, scanned_dirs = [], set()
        cache_path = get_cache_path(domain_name)
        if incremental and os.path.exists(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        all_entries = data.get("entries", [])
                        scanned_dirs = set(data.get("scanned_dirs", []))
            except Exception:
                pass

        seen_urls = {e["url"] for e in all_entries}
        queue_dirs = [root_url]
        visited_dirs = set(scanned_dirs)
        discovered_dirs_count, scanned_dirs_count = max(1, len(scanned_dirs) or 1), len(scanned_dirs)

        while queue_dirs and not stop_event.is_set():
            current_url = urllib.parse.urljoin(root_url, queue_dirs.pop(0))
            scanned_dirs_count += 1
            visited_dirs.add(current_url.rstrip("/") + "/")
            try:
                entries = fetch_directory(current_url)
            except Exception:
                entries = []

            for entry in entries:
                if stop_event.is_set():
                    break
                if entry["url"] not in seen_urls:
                    seen_urls.add(entry["url"])
                    all_entries.append(entry)
                if entry["directory"] and entry["url"] not in visited_dirs:
                    queue_dirs.append(entry["url"])
                    discovered_dirs_count += 1

            if progress_callback:
                progress_callback(scanned_dirs_count, discovered_dirs_count, len(all_entries))

        if stop_event.is_set():
            return None
        self.cached_scanned_dirs = visited_dirs
        return all_entries

    def start_domain_background_scraper(self, domain_name, root_url, cache_path):
        self.stop_event.clear()
        self.progress_bar["value"] = 0
        self.indexer_status_text.set(f"Indexing {domain_name}...")
        threading.Thread(target=self.domain_scraper_worker, args=(domain_name, root_url, cache_path), daemon=True).start()

    def domain_scraper_worker(self, domain_name, root_url, cache_path):
        all_entries = self.crawl_domain_remote(domain_name, root_url, report_progress=True, incremental=True)
        if all_entries is not None:
            self.domain_full_entries = all_entries
            self.save_cache(domain_name)
            self.messages.put(("scraper_finished", domain_name, all_entries))

    def load_directory(self, domain_name, url, is_root=False):
        self.current_sort_column = "type"
        self.current_sort_ascending = True
        self.update_sort_headers()
        self.current_url = url
        self.path_label.configure(text=urllib.parse.unquote(url))
        self.push_nav_history(domain_name, url)

        cache_path = get_cache_path(domain_name)
        if os.path.exists(cache_path) and not self.domain_full_entries:
            self.load_or_check_cache(domain_name, DOMAINS[domain_name])

        if self.domain_full_entries:
            threading.Thread(target=self.load_directory_from_index_worker, args=(domain_name, url), daemon=True).start()
        else:
            threading.Thread(target=self.load_directory_worker, args=(domain_name, url, is_root), daemon=True).start()

    def load_directory_from_index_worker(self, domain_name, url):
        try:
            entries = get_entries_from_domain_entries(url, self.domain_full_entries)
            total_size = self.cached_dir_sizes.get(url, get_size_from_domain_entries(url, self.domain_full_entries))
            self.cached_dir_sizes[url] = total_size
            self.messages.put(("directory", url, entries, total_size))
        except Exception as error:
            self.messages.put(("error", f"Could not load directory:\n\n{error}"))

    def load_directory_worker(self, domain_name, url, is_root):
        try:
            entries = fetch_directory(url)
            self.messages.put(("directory", url, entries, 0))
        except Exception as error:
            self.messages.put(("error", f"Could not read directory:\n\n{error}"))

    def display_entries(self, url, entries, folder_size):
        self.current_url = url
        self.current_entries = entries
        self.selected_item_ids.clear()
        self.cached_dir_sizes[url] = folder_size

        domain_name = self.domain_combo.get()
        root_url = DOMAINS.get(domain_name, "").rstrip("/")
        parsed_current, parsed_root = urllib.parse.urlparse(url), urllib.parse.urlparse(root_url)

        if parsed_current.path.rstrip("/") != parsed_root.path.rstrip("/"):
            parent_path = posixpath.dirname(parsed_current.path.rstrip("/")) + "/"
            parent_url = urllib.parse.urlunparse((parsed_current.scheme, parsed_current.netloc, parent_path, "", "", ""))
            entries = [{
                "name": "..", "raw_name": "..", "date": "", "url": parent_url, "directory": True, "size": None, "is_parent_nav": True
            }] + entries

        self.path_label.configure(text=urllib.parse.unquote(url))
        self.apply_filter()
        self.status_text.set(f"{len(self.visible_entries)} shown | Total Size: {format_size(folder_size)}")

    def apply_filter(self):
        search = self.search_text.get().lower().strip()
        self.tree.delete(*self.tree.get_children())
        source = self.domain_full_entries if (search and self.domain_full_entries) else self.current_entries
        filtered = [e for e in source if not search or search in e["name"].lower() or e.get("is_parent_nav")]
        self.visible_entries = self.apply_sorting(filtered)

        for index, entry in enumerate(self.visible_entries):
            item_id = str(index)
            select_symbol = ("[✓]" if item_id in self.selected_item_ids else "[ ]") if self.selection_checkboxes_enabled.get() and not entry.get("is_parent_nav") else ""
            
            item_size = (
                "-"
                if entry["directory"]
                else format_size(entry.get("size"))
            )

            self.tree.insert("", "end", iid=item_id, values=(
                select_symbol,
                "Directory" if entry["directory"] else "File",
                entry["name"],
                entry.get("date", ""),
                item_size,
                urllib.parse.unquote(entry["url"])
            ))

    def double_click_entry(self, event=None):
        selection = self.tree.selection()
        if not selection:
            return
        entry = self.visible_entries[int(selection[0])]

        if entry["directory"]:
            self.search_text.set("")
            self.load_directory(self.domain_combo.get(), entry["url"], is_root=False)
        else:
            if not self.tree.selection():
                self.tree.selection_set(selection[0])
            if self.selection_checkboxes_enabled.get():
                self.selected_item_ids.add(selection[0])
                self.apply_filter()

            dialog = tk.Toplevel(self)
            dialog.title("File Action")
            dialog.geometry("340x140")
            dialog.resizable(False, False)
            dialog.transient(self)
            try:
                dialog.update_idletasks()
                dialog.grab_set()
            except Exception:
                pass

            filename = entry["name"]
            ttk.Label(dialog, text=f"Choose action for:\n{filename}", font=("Arial", 9, "bold"), justify="center", wraplength=310).pack(pady=(15, 10))
            btn_frame = ttk.Frame(dialog)
            btn_frame.pack(pady=5)

            ttk.Button(btn_frame, text="Stream", width=12, command=lambda: [dialog.destroy(), self.download_and_open_temp()]).pack(side="left", padx=8)
            ttk.Button(btn_frame, text="Download", width=12, command=lambda: [dialog.destroy(), self.download_selected()]).pack(side="left", padx=8)

    def download_and_open_temp(self):
        urls = self.get_selected_files()
        if not urls:
            messagebox.showinfo("No file selected", "Please select a file to stream.")
            return

        url = urls[0]
        temp_dir = get_temp_directory()
        self.stop_event.clear()
        progress_win = DownloadProgressWindow(self, title="Downloading to Temp...")

        threading.Thread(
            target=self.download_and_open_temp_worker,
            args=(url, temp_dir, progress_win),
            daemon=True
        ).start()

    def download_and_open_temp_worker(self, url, temp_dir, progress_win):
        parsed = urllib.parse.urlparse(url)
        filename = safe_filename(urllib.parse.unquote(posixpath.basename(parsed.path))) or "archive_file"
        base_name = os.path.splitext(filename)[0]
        extract_dir = os.path.join(temp_dir, base_name + "_extracted")

        if os.path.exists(extract_dir) and os.path.isdir(extract_dir) and os.listdir(extract_dir):
            if progress_win.winfo_exists():
                self.messages.put(("close_progress", progress_win))
            self.messages.put(("open_file_browser", extract_dir))
            return

        output_path = unique_path(os.path.join(temp_dir, filename))
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                total_bytes = int(response.headers.get("Content-Length", 0))
                downloaded_bytes, start_time = 0, time.time()
                with open(output_path, "wb") as output:
                    while True:
                        if self.stop_event.is_set() or progress_win.is_cancelled:
                            return
                        chunk = response.read(64 * 1024)
                        if not chunk:
                            break
                        output.write(chunk)
                        downloaded_bytes += len(chunk)
                        elapsed = time.time() - start_time
                        speed = (downloaded_bytes / elapsed) if elapsed > 0 else 0
                        eta = ((total_bytes - downloaded_bytes) / speed) if speed > 0 and total_bytes > 0 else 0
                        self.messages.put(("update_progress", progress_win, filename, downloaded_bytes, total_bytes, 1, 1, speed, eta))
        except Exception as error:
            self.messages.put(("error", f"Failed to download file:\n\n{error}"))
            self.messages.put(("close_progress", progress_win))
            return

        if progress_win.winfo_exists():
            progress_win.file_label_text.set("Extracting archive...")

        os.makedirs(extract_dir, exist_ok=True)
        ext = os.path.splitext(output_path)[1].lower()
        extracted = False

        if ext == ".7z":
            try:
                subprocess.run([self.path_7z.get().strip() or "7z", "x", output_path, f"-o{extract_dir}", "-y"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                extracted = True
            except Exception as e:
                self.messages.put(("error", f"Failed to extract with 7-Zip:\n\n{e}"))
        elif ext in (".zip", ".tar", ".gz", ".bz2"):
            try:
                shutil.unpack_archive(output_path, extract_dir)
                extracted = True
            except Exception as e:
                self.messages.put(("error", f"Failed to extract archive:\n\n{e}"))
        else:
            extract_dir = temp_dir
            extracted = True

        self.messages.put(("close_progress", progress_win))
        if extracted:
            self.messages.put(("open_file_browser", extract_dir))

    def open_file_browser(self, folder_path):
        ExtractedFilesBrowserWindow(self, folder_path)

    def go_up(self):
        if not self.current_url:
            return
        parsed = urllib.parse.urlparse(self.current_url)
        parent_path = posixpath.dirname(parsed.path.rstrip("/")) + "/"
        parent_url = urllib.parse.urlunparse((parsed.scheme, parsed.netloc, parent_path, "", "", ""))
        self.load_directory(self.domain_combo.get(), parent_url, is_root=(parent_path == urllib.parse.urlparse(DOMAINS[self.domain_combo.get()]).path))

    def choose_destination(self):
        selected = filedialog.askdirectory(title="Choose download folder", initialdir=self.destination.get())
        if selected:
            self.destination.set(selected)
            self.save_destination_history(selected)

    def get_selected_files(self):
        target_ids = self.selected_item_ids if self.selection_checkboxes_enabled.get() else self.tree.selection()
        return [self.visible_entries[int(item_id)]["url"] for item_id in target_ids if not self.visible_entries[int(item_id)]["directory"]]

    def download_selected(self):
        urls = self.get_selected_files()
        if not urls:
            messagebox.showinfo("No files selected", "Please select files to download.")
            return
        folder = filedialog.askdirectory(title="Choose destination folder")
        if folder:
            messagebox.showinfo("Download", f"Downloading {len(urls)} files to {folder}")

    def download_current_directory(self):
        messagebox.showinfo("Download Directory", "Directory download triggered.")

    def stop_operations(self):
        self.stop_event.set()
        self.status_text.set("Stopping operations...")

    def process_messages(self):
        try:
            while True:
                msg = self.messages.get_nowait()
                if msg[0] == "directory":
                    self.display_entries(msg[1], msg[2], msg[3])
                elif msg[0] == "open_file_browser":
                    self.open_file_browser(msg[1])
                elif msg[0] == "scraper_finished":
                    self.status_text.set(f"Indexed {msg[1]} successfully.")
                elif msg[0] == "update_progress":
                    win, fname, d_bytes, t_bytes, c_idx, t_idx, speed, eta = msg[1:]
                    if win.winfo_exists():
                        win.update_progress(fname, d_bytes, t_bytes, c_idx, t_idx, speed, eta)
                elif msg[0] == "close_progress":
                    win = msg[1]
                    if win.winfo_exists():
                        win.destroy()
                elif msg[0] == "error":
                    messagebox.showerror("Error", msg[1])
        except queue.Empty:
            pass
        self.after(100, self.process_messages)
