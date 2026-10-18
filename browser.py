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
from players import VGMStreamPlayerWindow, MPVPlayerWindow, SIDPlayerWindow, GSFPlayerWindow, TwoSFPlayerWindow, PSFPlayerWindow, ExtractedFilesBrowserWindow, get_vgmstream_path, get_lazyusf_path, get_2sfplay_path
from downloaders import DownloadProgressWindow, IndexAllWindow, IndexPremadeWindow, install_dependencies_prompt


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


class JoshWBrowser(tk.Tk):
    def check_dependency_status(self, dep_name):
        cache_dir = get_cache_directory()
        if dep_name == "lazyusf":
            return (
                shutil.which("lazyusf") is not None
                or os.path.exists(os.path.join(cache_dir, "lazyusf"))
            )
        elif dep_name == "vgmstream":
            return (
                shutil.which("vgmstream123") is not None
                or os.path.exists(os.path.join(cache_dir, "vgmstream123"))
            )
        elif dep_name == "sidplayfp":
            return shutil.which("sidplayfp") is not None
        elif dep_name == "playgsf":
            return (
                shutil.which("playgsf") is not None
                or os.path.exists(os.path.join(cache_dir, "playgsf"))
            )
        elif dep_name == "2sfplay":
            return (
                shutil.which("2sfPlay") is not None
                or os.path.exists(os.path.join(cache_dir, "2sfPlay"))
            )
        elif dep_name == "xterm":
            return shutil.which("xterm") is not None
        elif dep_name == "xdotool":
            return shutil.which("xdotool") is not None
        elif dep_name == "nsf_tracks":
            return (
                shutil.which("nsf_tracks") is not None
                or os.path.exists(os.path.join(cache_dir, "nsf_tracks"))
            )
        elif dep_name == "fluidsynth":
            return (
                shutil.which("fluidsynth") is not None
                and os.path.exists(os.path.join(cache_dir, "FluidR3_GM.sf2"))
            )
        elif dep_name in ("aosdk", "psfplay"):
            try:
                from psf_ctypes import find_aosdk_lib
                find_aosdk_lib()
                return True
            except Exception:
                return False
        return False

    def update_dependencies_submenu(self):
        self.deps_menu.delete(0, tk.END)
        deps = [
            ("lazyusf", "lazyusf"),
            ("vgmstream", "vgmstream123"),
            ("sidplayfp", "sidplayfp"),
            ("playgsf", "playgsf"),
            ("2sfplay", "2sfPlay"),
            ("psfplay", "AOSDK (PSF/PSF2)"),
            ("xterm", "xterm"),
            ("xdotool", "xdotool"),
            ("nsf_tracks", "nsf_tracks"),
            ("fluidsynth", "FluidSynth & Soundfont")
        ]
        for key, label in deps:
            installed = self.check_dependency_status(key)
            display_text = f"✓ {label}" if installed else f"  {label}"
            self.deps_menu.add_command(
                label=display_text, 
                state="disabled",
                foreground="green" if installed else "black"
            )

    def download_premade_indexes_prompt(self):
        cache_dir = get_cache_directory()
        existing_caches = [f for f in os.listdir(cache_dir) if f.endswith(".json") and f != "settings.json" and f != "search_history.json"]
        
        if existing_caches:
            ans = messagebox.askokcancel("Are you sure?", "Are you sure? These are likely to be outdated!")
            if not ans:
                return
            for f in existing_caches:
                try:
                    os.remove(os.path.join(cache_dir, f))
                except Exception:
                    pass

        IndexPremadeWindow(self, show_steps=self.shift_pressed)

    def __init__(self):
        super().__init__()
        self.title("JoshW Archive Browser")
        self.geometry("1280x760")
        self.minsize(850, 560)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.shift_pressed = False
        self.bind("<KeyPress-Shift_L>", lambda e: setattr(self, "shift_pressed", True))
        self.bind("<KeyPress-Shift_R>", lambda e: setattr(self, "shift_pressed", True))
        self.bind("<KeyRelease-Shift_L>", lambda e: setattr(self, "shift_pressed", False))
        self.bind("<KeyRelease-Shift_R>", lambda e: setattr(self, "shift_pressed", False))

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
        self._last_loaded_music_dir = saved_load_dir

        self.path_7z = tk.StringVar(value="/bin/7z")
        self.path_mpv = tk.StringVar(value="/bin/mpv")

        self.selection_checkboxes_enabled = tk.BooleanVar(value=False)
        self.keep_download_window_open = tk.BooleanVar(value=True)

        self.search_text = tk.StringVar()
        self.search_all_indexes_var = tk.BooleanVar(value=False)
        self.all_index_entries = []
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
        
        load_music_btn = ttk.Button(archive_frame, text="Load Music...", command=lambda: self.load_local_music(is_default=True))
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
        self.options_dropdown.add_command(label="Download Premade Indexes (may be outdated!)", command=self.download_premade_indexes_prompt)
        
        self.install_deps_item_index = self.options_dropdown.add_command(
            label="Install Dependencies", 
            command=lambda: install_dependencies_prompt(self)
        )

        self.deps_menu = tk.Menu(self.options_dropdown, tearoff=0)
        self.options_dropdown.add_cascade(label="Dependencies", menu=self.deps_menu)
        self.update_dependencies_submenu()

        options_menu_btn["menu"] = self.options_dropdown

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

        info_search_frame = ttk.Frame(self, padding=(8, 0, 8, 2))
        info_search_frame.pack(fill="x", padx=2)

        ttk.Label(info_search_frame, text="Double click or hit Enter on a file to stream or download it", font=("Arial", 9, "italic")).pack(side="left")

        clear_search_btn = ttk.Button(info_search_frame, text="Clear", command=self.clear_search_history)
        clear_search_btn.pack(side="right", padx=2)
        ToolTip(clear_search_btn, "Click to clear search history")

        self.search_combo = ttk.Combobox(info_search_frame, textvariable=self.search_text, width=18, values=self.load_search_history())
        self.search_combo.pack(side="right", padx=4)
        self.search_combo.bind("<KeyRelease>", lambda event: self.apply_filter())
        self.search_combo.bind("<<ComboboxSelected>>", lambda event: self.on_search_selected())
        self.search_combo.bind("<Return>", lambda event: self.on_search_submitted())
        ttk.Checkbutton(
            info_search_frame,
            text="Search all indexes",
            variable=self.search_all_indexes_var,
            command=self.on_search_scope_changed
        ).pack(side="right", padx=4)

        ttk.Label(info_search_frame, text="Search:").pack(side="right", padx=(4, 2))

        tree_frame = ttk.Frame(self, padding=(8, 0, 8, 8))
        tree_frame.pack(fill="both", expand=True)

        self.tree = ttk.Treeview(tree_frame, columns=("select", "type", "name", "date", "size", "domain", "url"), show="headings", selectmode="extended")
        self.tree.heading("select", text="[ ]")
        self.tree.heading("type", text="Type", command=lambda: self.sort_by_column("type"))
        self.tree.heading("name", text="Name ▲", command=lambda: self.sort_by_column("name"))
        self.tree.heading("date", text="Date", command=lambda: self.sort_by_column("date"))
        self.tree.heading("size", text="Size", command=lambda: self.sort_by_column("size"))
        self.tree.heading("domain", text="Domain", command=lambda: self.sort_by_column("domain"))
        self.tree.heading("url", text="URL", command=lambda: self.sort_by_column("url"))

        self.tree.column("select", width=50, anchor="center", stretch=False)
        self.tree.column("type", width=90, anchor="center", stretch=False)
        self.tree.column("name", width=280)
        self.tree.column("date", width=110, anchor="center", stretch=False)
        self.tree.column("size", width=100, anchor="e", stretch=False)
        self.tree.column("domain", width=90, anchor="center", stretch=False)
        self.tree.column("url", width=520)
        self.tree["displaycolumns"] = ("select", "type", "name", "date", "size", "url")

        scrollbar = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.tree.bind("<Double-1>", self.double_click_entry)
        self.tree.bind("<Return>", self.double_click_entry)
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

    def load_local_music(self, is_default=False):
        initial_dir = self.default_load_music_dir.get() if is_default else self._last_loaded_music_dir
        file_path = filedialog.askopenfilename(
            title="Select Music File",
            initialdir=initial_dir,
            filetypes=[
                ("All Files", "*.*")
            ]
        )
        if file_path:
            folder = os.path.dirname(file_path)
            self._last_loaded_music_dir = folder
            self.play_audio_file(file_path)

    def set_default_load_music_dir(self):
        folder = filedialog.askdirectory(title="Choose Default Load Music Folder", initialdir=self.default_load_music_dir.get())
        if folder:
            self.default_load_music_dir.set(folder)
            self._last_loaded_music_dir = folder
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
            "It is pretty simple to use, first, i highly recommend that you click the Options button and choose the Download Premade Indexes option. Once a domain is indexed, it will load instantly rather than having to get reloaded from the server every time.\n\n"
            "After that finishes, you can use the dropdown and choose a domain to load. click Open to show the directory listing of that domain, and once you find a file you want, you can double click or press Enter to try and stream the files or download them directly. You may also shift click to choose multiple files to download, or use the \"Download Every File Here\" button to grab everything.\n\n"
            "You may set a default download location and enable it by clicking the little box to the left of the textbox. Otherwise when downloading it will ask you where you want the files.\n\n"
            "If you know the name of the file you want, log into the appropriate domain and go to the search bar. Start typing and it will filter it down instantly! If you hit enter, it will save the search in the search history, which can be seen with the dropdown arrow on the search bar. Alternatively, you can click the option next to the search bar named search all indexes to then have your search term go thru every domain at once! This will be laggy however.\n\n"
            "Dependencies Note:\n"
            "If you ever need to check or install dependencies, open the options menu and either install or view the installed dependencies.\n\n"
            "Currently, the only fully supported streamable domains are the following:\n\n"
            "PC (.7z, Various Rips)\n"
            "FM Towns (.7z, Various Rips)\n"
            "NES (.nsf, .nsfe)\n"
            "SNES (.spc)\n"
            "c64 (.sid)\n"
            "GameCube (.ast, .dsp, .brstm)\n"
            "Nintendo Wii (.brstm, .rstm, .bfsar)\n"
            "Nintendo WiiU (.bfstm, .bfwav, .fsar)\n"
            "SEGA CD / Mega Drive (.vgm, .vgz, .gym)\n"
            "Saturn (.ssf)\n"
            "Dreamcast (.dsf)\n"
            "PC Engine (.hes)\n"
            "Neo Geo (.kss)\n"
            "Playstation (.psf, .minipsf)\n"
            "Playstation 2 (.psf2, .minipsf2)\n"
            "Xbox (.xma, .xmd)\n"
            "Xbox 360 (.xma, .xmd)\n"
            "3DO (.vag, .adpcm)\n"
            "Game Boy (.gbs)\n"
            "Game Boy Advance (.gsf, .minigsf)\n"
            "Nintendo DS (.2sf, .mini2sf)\n"
            "Playstation Portable (.vag, .at3)\n"
            "Playstation Vita (.vag, .at3)\n\n"
            "Partially working domains are:\n\n"
            "N64 (.usf, .usfmini, .usflib)\n\n"
            "I hope to add more in the future with more updates\n\n"
            "All streamed songs are saved in a temporary folder within ~/.config/joshw/temp_extracted and will be deleted once the program is closed.\n\n"
            "I hope you really enjoy this program!"
        )

        text_widget.insert("1.0", help_text)
        text_widget.configure(state="disabled")

    def update_sort_headers(self):
        base_titles = {"type": "Type", "name": "Name", "date": "Date", "size": "Size", "domain": "Domain", "url": "URL"}
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
            elif col == "domain":
                return (1, entry.get("domain", "").lower())
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

    def play_audio_file(self, audio_file, playlist=None, start_index=None):
        ext = os.path.splitext(audio_file)[1].lower()

        # Force vgmstream123 for these specific formats
        vgmstream_formats = {".ast", ".dsp", ".brstm", ".rstm", ".bfsar", ".bfwav", ".adp", ".str", ".thp", ".was", ".h4m", ".vag", ".wem"}
        if ext in vgmstream_formats:
            vgm_bin = get_vgmstream_path()
            if not vgm_bin or (not shutil.which(vgm_bin) and not os.path.exists(vgm_bin)):
                messagebox.showerror("vgmstream123 Not Found", "vgmstream123 not found!")
                return
            VGMStreamPlayerWindow(self, audio_file, playlist=playlist, start_index=start_index)
            return

        supported_extensions = {
            ".ay", ".gbs", ".gym", ".hes", ".kss", ".nsf", ".nsfe", ".sap", ".spc", ".vgm", ".vgz",
            ".mp3", ".ogg", ".flac", ".wav", ".m4a", ".aac", ".opus", ".wma", ".ape", ".wv",
            ".mod", ".s3m", ".xm", ".it", ".mka", ".tta", ".ac3", ".dts", ".aiff", ".aifc", ".au", ".ra", ".xma", ".XMA", ".xmd",
            ".dsp", ".ast", ".brstm", ".rstm", ".brwav", ".rwav", ".brsar", ".rsar", ".bfstm", ".fstm", ".bfwav", ".fwav", ".bfsar", ".fsar",
            ".vag", ".adpcm", ".at3", ".adp", ".str", ".thp", ".was", ".h4m", ".wem"
        }
        if ext == ".sid":
            SIDPlayerWindow(self, audio_file, playlist=playlist, start_index=start_index)
            return
        elif ext in (".gsf", ".minigsf"):
            if not self.check_dependency_status("playgsf"):
                messagebox.showerror("playgsf Not Found", "playgsf not found! Please install dependencies.")
                return
            GSFPlayerWindow(self, audio_file, playlist=playlist, start_index=start_index)
            return
        elif ext in (".2sf", ".mini2sf"):
            if not self.check_dependency_status("2sfplay"):
                messagebox.showerror("2sfPlay Not Found", "2sfPlay not found! Please install dependencies.")
                return
            TwoSFPlayerWindow(self, audio_file, playlist=playlist, start_index=start_index)
            return
        elif ext in (".psf", ".minipsf", ".psf2", ".minipsf2"):
            if not self.check_dependency_status("psfplay"):
                messagebox.showerror(
                    "AOSDK Library Not Found",
                    "libaosdk.so not found! Please run ./build_aosdk.sh to build the PSF/PSF2 playback library."
                )
                return
            PSFPlayerWindow(self, audio_file, playlist=playlist, start_index=start_index)
            return
        elif ext in (".mid", ".midi"):
            if not self.check_dependency_status("fluidsynth"):
                messagebox.showerror("FluidSynth Required", "Midi playback requires FluidSynth and soundfont. Please install dependencies.")
                return
            MPVPlayerWindow(self, audio_file, playlist=playlist, start_index=start_index)
            return
        elif ext in supported_extensions or ext in (".hes", ".nsf", ".nsfe", ".xma", ".XMA", ".miniusf", ".usf"):
            mpv_path = self.path_mpv.get().strip() or "/bin/mpv"
            if not (shutil.which(mpv_path) or os.path.exists(mpv_path)):
                messagebox.showerror("mpv Not Found", "mpv not found!")
                return
            MPVPlayerWindow(self, audio_file, playlist=playlist, start_index=start_index)
        else:
            vgm_bin = get_vgmstream_path()
            if not vgm_bin or (not shutil.which(vgm_bin) and not os.path.exists(vgm_bin)):
                messagebox.showerror("vgmstream123 Not Found", "vgmstream123 not found!")
                return
            VGMStreamPlayerWindow(self, audio_file, playlist=playlist, start_index=start_index)

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
            self.search_text.set("")
            self.apply_filter()
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

    def load_all_index_entries(self):
        bundled_root = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
        index_directories = (
            os.path.join(bundled_root, "indexes"),
            get_cache_directory(),
        )
        index_files = {}
        for directory in index_directories:
            try:
                for filename in os.listdir(directory):
                    if filename.startswith("domain_cache_") and filename.endswith(".json"):
                        index_files[filename] = os.path.join(directory, filename)
            except OSError:
                continue

        entries_by_url = {}
        for index_path in index_files.values():
            try:
                with open(index_path, "r", encoding="utf-8") as index_file:
                    index_data = json.load(index_file)
                entries = index_data.get("entries", []) if isinstance(index_data, dict) else index_data
                if not isinstance(entries, list):
                    continue
                for entry in entries:
                    if not isinstance(entry, dict) or not entry.get("url"):
                        continue
                    indexed_entry = dict(entry)
                    hostname = urllib.parse.urlparse(indexed_entry["url"]).hostname or ""
                    host_parts = hostname.lower().split(".")
                    domain = host_parts[1] if len(host_parts) > 1 and host_parts[0] == "www" else host_parts[0]
                    indexed_entry["domain"] = domain
                    entries_by_url[indexed_entry["url"]] = indexed_entry
            except (OSError, json.JSONDecodeError, TypeError, ValueError):
                continue
        return list(entries_by_url.values())

    def on_search_scope_changed(self):
        if self.search_all_indexes_var.get():
            self.all_index_entries = self.load_all_index_entries()
        else:
            self.all_index_entries = []
        self.apply_filter()

    def apply_filter(self):
        search = self.search_text.get().lower().strip()
        self.tree.delete(*self.tree.get_children())
        if self.search_all_indexes_var.get():
            self.tree["displaycolumns"] = ("select", "type", "name", "date", "size", "domain", "url")
            source = self.all_index_entries
            filtered = [
                entry for entry in source
                if search
                and not entry.get("directory")
                and (search in entry["name"].lower() or search in entry["url"].lower() or search in entry.get("domain", "").lower())
            ]
        else:
            self.tree["displaycolumns"] = ("select", "type", "name", "date", "size", "url")
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
                entry.get("domain", ""),
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

            stream_btn = ttk.Button(btn_frame, text="Stream", width=12, command=lambda: [dialog.destroy(), self.download_and_open_temp()])
            stream_btn.pack(side="left", padx=8)
            download_btn = ttk.Button(btn_frame, text="Download", width=12, command=lambda: [dialog.destroy(), self.download_selected()])
            download_btn.pack(side="left", padx=8)

            stream_btn.focus_set()

            def focus_stream(e):
                stream_btn.focus_set()
                return "break"

            def focus_download(e):
                download_btn.focus_set()
                return "break"

            dialog.bind("<Left>", focus_stream)
            dialog.bind("<Right>", focus_download)
            stream_btn.bind("<Left>", focus_stream)
            stream_btn.bind("<Right>", focus_download)
            download_btn.bind("<Left>", focus_stream)
            download_btn.bind("<Right>", focus_download)

            def confirm_action(e):
                if dialog.focus_get() == stream_btn:
                    dialog.destroy()
                    self.download_and_open_temp()
                elif dialog.focus_get() == download_btn:
                    dialog.destroy()
                    self.download_selected()
                return "break"

            dialog.bind("<Return>", confirm_action)

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
        ext = os.path.splitext(filename)[1].lower()
        compressed_exts = {".7z", ".zip", ".tar", ".gz", ".bz2"}

        if ext not in compressed_exts:
            output_path = os.path.join(temp_dir, filename)
            if os.path.exists(output_path):
                if progress_win.winfo_exists():
                    self.messages.put(("close_progress", progress_win))
                self.messages.put(("play_audio", output_path))
                return

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
                self.messages.put(("close_progress", progress_win))
            self.messages.put(("play_audio", output_path))
            return

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
            self.messages.put(("start_extracting", progress_win))

        os.makedirs(extract_dir, exist_ok=True)
        ext = os.path.splitext(output_path)[1].lower()
        extracted = False

        if ext == ".7z":
            try:
                def extract_progress_cb(current, total):
                    if progress_win.winfo_exists():
                        self.messages.put(("update_extraction_progress", progress_win, filename, current, total))
                
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
        urls = []
        for item_id in target_ids:
            try:
                entry = self.visible_entries[int(item_id)]
                if not entry["directory"]:
                    urls.append(entry["url"])
            except Exception:
                pass
        return urls

    def download_selected(self):
        urls = self.get_selected_files()
        if not urls:
            selection = self.tree.selection()
            if selection:
                for item_id in selection:
                    try:
                        entry = self.visible_entries[int(item_id)]
                        if not entry["directory"]:
                            urls.append(entry["url"])
                    except Exception:
                        pass
        if not urls:
            messagebox.showinfo("No files selected", "Please select files to download.")
            return

        dest_dir = self.destination.get().strip() if self.use_default_dest.get() else ""
        if not dest_dir or not os.path.isdir(dest_dir):
            dest_dir = filedialog.askdirectory(title="Choose destination folder", initialdir=self.destination.get())
            if not dest_dir:
                return
            self.destination.set(dest_dir)
            self.save_destination_history(dest_dir)

        self.stop_event.clear()
        progress_win = DownloadProgressWindow(self, title="Downloading Selected Files...")
        threading.Thread(
            target=self.download_files_worker,
            args=(urls, dest_dir, progress_win),
            daemon=True
        ).start()

    def download_current_directory(self):
        urls = [e["url"] for e in self.current_entries if not e["directory"] and not e.get("is_parent_nav")]
        if not urls:
            messagebox.showinfo("No files", "No files found in the current directory.")
            return

        dest_dir = self.destination.get().strip() if self.use_default_dest.get() else ""
        if not dest_dir or not os.path.isdir(dest_dir):
            dest_dir = filedialog.askdirectory(title="Choose destination folder", initialdir=self.destination.get())
            if not dest_dir:
                return
            self.destination.set(dest_dir)
            self.save_destination_history(dest_dir)

        self.stop_event.clear()
        progress_win = DownloadProgressWindow(self, title="Downloading Directory Files...")
        threading.Thread(
            target=self.download_files_worker,
            args=(urls, dest_dir, progress_win),
            daemon=True
        ).start()

    def download_files_worker(self, urls, dest_dir, progress_win):
        total_files = len(urls)
        for idx, url in enumerate(urls, start=1):
            if self.stop_event.is_set() or (progress_win.winfo_exists() and progress_win.is_cancelled):
                break
            parsed = urllib.parse.urlparse(url)
            filename = safe_filename(urllib.parse.unquote(posixpath.basename(parsed.path))) or "file"
            output_path = unique_path(os.path.join(dest_dir, filename))

            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            try:
                with urllib.request.urlopen(request, timeout=60) as response:
                    total_bytes = int(response.headers.get("Content-Length", 0))
                    downloaded_bytes, start_time = 0, time.time()
                    with open(output_path, "wb") as output:
                        while True:
                            if self.stop_event.is_set() or (progress_win.winfo_exists() and progress_win.is_cancelled):
                                break
                            chunk = response.read(64 * 1024)
                            if not chunk:
                                break
                            output.write(chunk)
                            downloaded_bytes += len(chunk)
                            elapsed = time.time() - start_time
                            speed = (downloaded_bytes / elapsed) if elapsed > 0 else 0
                            eta = ((total_bytes - downloaded_bytes) / speed) if speed > 0 and total_bytes > 0 else 0
                            if progress_win.winfo_exists():
                                self.messages.put(("update_progress", progress_win, f"[{idx}/{total_files}] {filename}", downloaded_bytes, total_bytes, idx, total_files, speed, eta))
            except Exception as error:
                self.messages.put(("error", f"Failed to download {filename}:\n\n{error}"))

        if progress_win.winfo_exists():
            self.messages.put(("close_progress", progress_win))
        self.messages.put(("status", f"Finished downloading {total_files} files."))

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
                elif msg[0] == "play_audio":
                    self.play_audio_file(msg[1])
                elif msg[0] == "scraper_finished":
                    self.status_text.set(f"Indexed {msg[1]} successfully.")
                elif msg[0] == "status":
                    self.status_text.set(msg[1])
                elif msg[0] == "update_progress":
                    win, fname, d_bytes, t_bytes, c_idx, t_idx, speed, eta = msg[1:]
                    if win.winfo_exists():
                        win.update_progress(fname, d_bytes, t_bytes, c_idx, t_idx, speed, eta)
                elif msg[0] == "start_extracting":
                    win = msg[1]
                    if win.winfo_exists():
                        win.start_extraction_mode()
                elif msg[0] == "update_extraction_progress":
                    win, fname, current, total = msg[1:]
                    if win.winfo_exists():
                        win.update_extraction_progress(fname, current, total)
                elif msg[0] == "close_progress":
                    win = msg[1]
                    if win.winfo_exists():
                        win.destroy()
                elif msg[0] == "error":
                    messagebox.showerror("Error", msg[1])
        except queue.Empty:
            pass
        self.after(100, self.process_messages)