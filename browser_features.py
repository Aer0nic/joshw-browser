"""
browser_features.py - browser-side features, mixed into JoshWBrowser.

  * History tab (recently / most played; double-click plays again)
  * game info: Year / Developer / Publisher columns parsed from joshw names,
    plus a side panel with box art
  * right-click menu on the Browse / Favorites / Shuffle lists
  * smart shuffle (skips recently played games, favours favourites and new)
  * faster "Search all indexes" (background load, multi-word, capped results)
  * "new since last index" highlighting and a "New only" filter
  * Now Playing window, tray icon
  * remembers where streamed tracks came from (for History)
"""
import heapq
import os
import random
import re
import threading
import time
import tkinter as tk
import urllib.parse
from pathlib import Path
from tkinter import messagebox

import ttkbootstrap as tb

import library
from config import resource_path

MAX_RESULTS = 3000
GAME_COLUMNS = ("year", "developer", "publisher")
AUDIO_EXTS = None  # filled lazily from player_launcher


def raw_entry_name(entry):
    """Full joshw name of an entry, e.g. "Game (2002-08-20)(Dev)(Pub)[GBA].7z".
    Index entries carry it as raw_name (name is the cleaned display name)."""
    raw = entry.get("raw_name")
    if not raw:
        path = urllib.parse.urlparse(entry.get("url", "")).path.rstrip("/")
        raw = urllib.parse.unquote(path.rsplit("/", 1)[-1]) if path else entry.get("name", "")
    return raw.rstrip("/")


def game_base_name(entry):
    """raw name without the archive extension, lower-case (matches the first
    part of history track keys)."""
    return re.sub(r"\.(7z|zip|rar)$", "", raw_entry_name(entry), flags=re.I).lower()


def _url_domain(url):
    host = (urllib.parse.urlparse(url).hostname or "").lower().split(".")
    return host[1] if len(host) > 1 and host[0] == "www" else (host[0] if host else "")


def _audio_exts():
    global AUDIO_EXTS
    if AUDIO_EXTS is None:
        from player_launcher import GME_EXTS, MPV_EXTS, NEOBAE_EXTS, VGMSTREAM_EXTS
        AUDIO_EXTS = set(GME_EXTS) | set(MPV_EXTS) | set(NEOBAE_EXTS) | set(VGMSTREAM_EXTS) | {
            ".sid", ".usf", ".miniusf", ".gsf", ".minigsf", ".2sf", ".mini2sf", ".psf", ".minipsf",
            ".psf2", ".minipsf2", ".vgz"}
    return AUDIO_EXTS


class BrowserFeaturesMixin:
    # ------------------------------------------------------------------ setup
    def init_features(self):
        s = self._initial_settings
        self.smart_shuffle_var = tk.BooleanVar(value=s.get("smart_shuffle", True))
        self.show_tray_var = tk.BooleanVar(value=s.get("show_tray_icon", True))
        self.show_info_panel_var = tk.BooleanVar(value=s.get("show_game_info_panel", True))
        self.new_only_var = tk.BooleanVar(value=False)
        self.history_mode = tk.StringVar(value="recent")
        self._game_info_cache = {}
        self._search_job = None
        self._all_index_loading = False
        self._info_token = 0
        self.history_rows = []
        self.tray = None
        self.reload_new_markers()

    def feature_settings(self):
        return {"smart_shuffle": self.smart_shuffle_var.get(),
                "show_tray_icon": self.show_tray_var.get(),
                "show_game_info_panel": self.show_info_panel_var.get()}

    def build_feature_menus(self, menubar):
        view = tk.Menu(menubar, tearoff=0)
        view.add_command(label="Now Playing...", command=self.open_now_playing)
        view.add_separator()
        view.add_checkbutton(label="Game Info Panel", variable=self.show_info_panel_var,
                             command=self.toggle_info_panel)
        view.add_checkbutton(label="Tray Icon", variable=self.show_tray_var, command=self.toggle_tray)
        menubar.add_cascade(label="View", menu=view)
        self.options_dropdown.add_separator()
        self.options_dropdown.add_checkbutton(
            label="Smart Shuffle (skip recently played, favour favourites)",
            variable=self.smart_shuffle_var, command=self.save_settings)

    # ------------------------------------------------------------------ game info
    def entry_game_info(self, entry):
        url = entry.get("url", "")
        cached = self._game_info_cache.get(url)
        if cached is not None:
            return cached
        base = re.sub(r"\.(7z|zip|rar)$", "", raw_entry_name(entry), flags=re.I)
        if "(" not in base and "[" not in base:
            # a loose file: the game is the folder it lives in
            parts = [p for p in urllib.parse.urlparse(url).path.split("/") if p]
            folder = urllib.parse.unquote(parts[-2]) if len(parts) > 1 and not entry.get("directory") else ""
            if "(" in folder or "[" in folder:
                base = folder
        if "(" in base or "[" in base:
            info = library.parse_game_folder(base)
        else:
            info = {"title": base, "date": "", "year": "", "developer": "", "publisher": "", "platform": ""}
        self._game_info_cache[url] = info
        return info

    def game_values(self, entry):
        if entry.get("is_parent_nav"):
            return ("", "", "")
        info = self.entry_game_info(entry)
        return (info["year"], info["developer"], info["publisher"])

    def setup_game_columns(self, tree, sort_cmd):
        for col, title in zip(GAME_COLUMNS, ("Year", "Developer", "Publisher")):
            tree.heading(col, text=title, command=lambda c=col: sort_cmd(c))
            tree.column(col, width=70 if col == "year" else 140, anchor="center" if col == "year" else "w",
                        stretch=False)

    def game_sort_key(self, entry, col):
        info = self.entry_game_info(entry)
        v = info.get(col, "")
        return (1, 0 if v else 1, v.lower())

    # ------------------------------------------------------------------ info panel
    def build_info_panel(self, container):
        self.info_panel = tb.Frame(container, padding=(8, 0, 0, 0), width=240)
        self.info_panel.pack_propagate(False)
        self.info_art = tb.Label(self.info_panel, anchor="center")
        self.info_art.pack(fill="x", pady=(4, 6))
        self.info_title_var = tk.StringVar(value="Select a game")
        self.info_meta_var = tk.StringVar(value="")
        self.info_plays_var = tk.StringVar(value="")
        tb.Label(self.info_panel, textvariable=self.info_title_var, font=("Arial", 10, "bold"),
                 wraplength=225, justify="left").pack(anchor="w")
        tb.Label(self.info_panel, textvariable=self.info_meta_var, wraplength=225, justify="left").pack(
            anchor="w", pady=(4, 0))
        tb.Label(self.info_panel, textvariable=self.info_plays_var, font=("Arial", 9, "italic"),
                 wraplength=225, justify="left").pack(anchor="w", pady=(4, 0))
        self._info_photo = None
        self._set_info_art(None)
        if self.show_info_panel_var.get():
            self.info_panel.pack(side="right", fill="y", before=self.notebook)
        for tree in (self.tree, self.fav_tree, self.shuffle_tree):
            tree.bind("<<TreeviewSelect>>", lambda e, t=tree: self.on_tree_selection(t), add="+")

    def toggle_info_panel(self):
        if self.show_info_panel_var.get():
            self.info_panel.pack(side="right", fill="y", before=self.notebook)
        else:
            self.info_panel.pack_forget()
        self.save_settings()

    def _set_info_art(self, path):
        from PIL import Image, ImageTk
        size = 220
        try:
            if path and os.path.exists(path):
                img = Image.open(path).convert("RGB")
                img.thumbnail((size, size))
            else:
                img = Image.new("RGB", (size, int(size * 0.7)), (70, 70, 70))
        except Exception:
            img = Image.new("RGB", (size, int(size * 0.7)), (70, 70, 70))
        self._info_photo = ImageTk.PhotoImage(img)
        self.info_art.configure(image=self._info_photo)

    def entry_for_tree_item(self, tree, item_id):
        try:
            if tree is self.tree:
                return self.visible_entries[int(item_id)]
            if tree is self.fav_tree:
                return getattr(self, "fav_display_mapping", {}).get(item_id)
            if tree is self.shuffle_tree:
                return self.shuffle_entries[int(item_id)]
        except (IndexError, ValueError):
            return None
        return None

    def on_tree_selection(self, tree):
        if not self.show_info_panel_var.get():
            return
        sel = tree.selection()
        entry = self.entry_for_tree_item(tree, sel[0]) if sel else None
        if entry and not entry.get("is_parent_nav"):
            self.show_game_info(entry)

    def show_game_info(self, entry):
        info = self.entry_game_info(entry)
        self.info_title_var.set(info["title"] or entry.get("name", ""))
        lines = []
        if info["date"]:
            lines.append(f"Released: {info['date']}")
        if info["developer"]:
            lines.append(f"Developer: {info['developer']}")
        if info["publisher"]:
            lines.append(f"Publisher: {info['publisher']}")
        if info["platform"]:
            lines.append(f"Platform: {info['platform']}")
        self.info_meta_var.set("\n".join(lines))
        counts = library.history().play_counts()
        game_key = game_base_name(entry)
        plays = sum(n for k, n in counts.items() if k.split("/", 1)[0] == game_key or k == library.track_key(entry.get("url", "")))
        self.info_plays_var.set(f"Played {plays} time{'s' if plays != 1 else ''}" if plays else "")
        self._set_info_art(None)
        self._info_token += 1
        token = self._info_token
        system = library.system_key_for(entry.get("url"), info["platform"])

        def work():
            art = library.find_box_art(info["title"], system)
            self.messages.put(("call", lambda: token == self._info_token and self._set_info_art(art)))
        if system:
            threading.Thread(target=work, daemon=True).start()

    # ------------------------------------------------------------------ context menu
    def show_tree_context_menu(self, event, tree):
        item_id = tree.identify_row(event.y)
        if not item_id:
            return
        entry = self.entry_for_tree_item(tree, item_id)
        if not entry or entry.get("is_parent_nav"):
            return
        tree.selection_set(item_id)
        menu = tk.Menu(self, tearoff=0)
        url = entry.get("url", "")
        if not entry.get("directory"):
            fav_urls = {f.get("url") for f in self.load_favorites()}
            label = "Remove from Favorites" if url in fav_urls else "Add to Favorites"
            menu.add_command(label=f"★ {label}", command=lambda: self.toggle_favorite_by_entry(entry))
            menu.add_separator()
            menu.add_command(label="Stream / Open", command=lambda: self.stream_specific_url(url))
            menu.add_command(label="Download...", command=lambda: self.download_specific_url(url))
        menu.add_command(label="Game info", command=lambda: self._show_info_from_menu(entry))
        menu.add_command(label="Copy URL", command=lambda: (self.clipboard_clear(),
                                                            self.clipboard_append(urllib.parse.unquote(url))))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def _show_info_from_menu(self, entry):
        if not self.show_info_panel_var.get():
            self.show_info_panel_var.set(True)
            self.toggle_info_panel()
        self.show_game_info(entry)

    # ------------------------------------------------------------------ history tab
    def build_history_tab(self):
        frame = tb.Frame(self.notebook)
        self.history_frame = frame
        self.notebook.add(frame, text="History")
        bar = tb.Frame(frame, padding=(0, 4))
        bar.pack(fill="x")
        tb.Radiobutton(bar, text="Recently played", variable=self.history_mode, value="recent",
                       command=self.refresh_history).pack(side="left", padx=(0, 8))
        tb.Radiobutton(bar, text="Most played", variable=self.history_mode, value="most",
                       command=self.refresh_history).pack(side="left")
        tb.Button(bar, text="Clear History", bootstyle="outline-danger", command=self.clear_history).pack(side="right")
        tb.Label(bar, text="Double click to play again", font=("Arial", 9, "italic")).pack(side="right", padx=10)
        inner = tb.Frame(frame)
        inner.pack(fill="both", expand=True)
        self.history_tree = tb.Treeview(inner, columns=("title", "game", "plays", "last", "player", "source"),
                                        show="headings", selectmode="browse")
        for col, text, width in (("title", "Track", 260), ("game", "Game", 260), ("plays", "Plays", 60),
                                 ("last", "Last played", 140), ("player", "Player", 70), ("source", "Source", 400)):
            self.history_tree.heading(col, text=text)
            self.history_tree.column(col, width=width, anchor="center" if col in ("plays", "player") else "w",
                                     stretch=col == "source")
        self._setup_tree_scrolling_and_sizing(inner, self.history_tree)
        self.history_tree.bind("<Double-1>", self.play_history_row)
        self.history_tree.bind("<Return>", self.play_history_row)
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed, add="+")

    def _on_tab_changed(self, event=None):
        try:
            if self.notebook.select() == str(self.history_frame):
                self.refresh_history()
        except Exception:
            pass

    def refresh_history(self):
        h = library.history()
        self.history_rows = h.recent(500) if self.history_mode.get() == "recent" else h.most_played(500)
        self.history_tree.delete(*self.history_tree.get_children())
        for i, r in enumerate(self.history_rows):
            last = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["last_played"])) if r.get("last_played") else ""
            source = urllib.parse.unquote(r.get("url") or r.get("archive_url") or "") or r.get("path") or ""
            self.history_tree.insert("", "end", iid=str(i), values=(
                r.get("title") or "", r.get("game") or "", r.get("plays") or 0, last,
                (r.get("player") or "").upper(), source))
        autofit_treeview_columns = getattr(self, "_autofit", None)
        if autofit_treeview_columns:
            autofit_treeview_columns(self.history_tree)

    def clear_history(self):
        if messagebox.askyesno("Clear History", "Forget all play history and play counts?", parent=self):
            library.history().clear()
            self.refresh_history()

    def play_history_row(self, event=None):
        sel = self.history_tree.selection()
        if not sel:
            return
        row = self.history_rows[int(sel[0])]
        path = row.get("path") or ""
        plain = path.rsplit("#", 1)[0] if "#" in path and not os.path.exists(path) else path
        if plain and os.path.exists(plain):
            self.play_audio_file(plain)
        elif row.get("url"):
            self.stream_specific_url(row["url"])
        elif row.get("archive_url"):
            # Opens the archive's file list again; pick the track there
            self.stream_specific_url(row["archive_url"])
        else:
            messagebox.showinfo("Not available",
                                "This track's file is gone and its source isn't known\n"
                                "(it was played from a local folder that moved).", parent=self)

    # ------------------------------------------------------------------ sources
    def remember_stream_source(self, url, local_path=None, extract_dir=None):
        """Called from the download worker: where a played track came from."""
        h = library.history()
        domain = _url_domain(url)
        try:
            if local_path:
                h.remember_source(library.track_key(local_path), url=url,
                                  name=os.path.basename(local_path), domain=domain)
            if extract_dir and os.path.isdir(extract_dir):
                exts = _audio_exts()
                for root, _dirs, files in os.walk(extract_dir):
                    for f in files:
                        if os.path.splitext(f)[1].lower() in exts:
                            p = os.path.join(root, f)
                            h.remember_source(library.track_key(p), archive_url=url, name=f, domain=domain)
        except Exception as e:
            print(f"[history] could not record source: {e}")

    # ------------------------------------------------------------------ shuffle
    def pick_shuffle(self, files, count):
        count = min(len(files), count)
        if not self.smart_shuffle_var.get():
            return random.sample(files, count)
        recent = library.history().recently_played_keys(7)
        recent_games = {k.split("/", 1)[0] for k in recent}
        fav_urls = {f.get("url") for f in self.load_favorites()}

        def weight(e):
            url = e.get("url", "")
            base = game_base_name(e)
            if base in recent_games or library.track_key(url) in recent:
                return 0.05  # played in the last week: rarely picked again
            w = 1.0
            if url in fav_urls:
                w *= 3.0
            if url in self._new_urls:
                w *= 2.0
            return w

        # weighted sampling without replacement (Efraimidis-Spirakis)
        keyed = ((random.random() ** (1.0 / weight(e)), i) for i, e in enumerate(files))
        return [files[i] for _, i in heapq.nlargest(count, keyed)]

    # ------------------------------------------------------------------ search
    def schedule_filter(self, event=None):
        if self._search_job:
            try:
                self.after_cancel(self._search_job)
            except Exception:
                pass
        self._search_job = self.after(220, self._run_scheduled_filter)

    def _run_scheduled_filter(self):
        self._search_job = None
        self.apply_filter()

    def load_all_indexes_async(self):
        if self._all_index_loading:
            return
        self._all_index_loading = True
        self.status_text.set("Loading all indexes for search...")

        def work():
            entries = self.load_all_index_entries()
            for e in entries:
                e["_s"] = f"{e.get('name', '')} {urllib.parse.unquote(e.get('url', ''))} {e.get('domain', '')}".lower()
            self.messages.put(("call", lambda: self._all_indexes_loaded(entries)))
        threading.Thread(target=work, daemon=True).start()

    def _all_indexes_loaded(self, entries):
        self._all_index_loading = False
        self.all_index_entries = entries
        self.status_text.set(f"{len(entries):,} indexed entries loaded for search.")
        if self.search_all_indexes_var.get():
            self.apply_filter()

    @staticmethod
    def match_terms(entry, terms, haystack=None):
        hay = haystack if haystack is not None else entry.get("_s")
        if hay is None:
            hay = f"{entry.get('name', '')} {urllib.parse.unquote(entry.get('url', ''))}".lower()
            entry["_s"] = hay
        return all(t in hay for t in terms)

    # ------------------------------------------------------------------ new markers
    def reload_new_markers(self):
        self._new_urls = library.new_entry_urls()
        self._new_folder_urls = library.new_folder_urls(self._new_urls)

    def is_new_entry(self, entry):
        url = entry.get("url", "")
        return url in self._new_urls or (entry.get("directory") and url in self._new_folder_urls)

    def configure_new_tag(self, tree):
        try:
            color = self.style.colors.success
        except Exception:
            color = "#2e9d4f"
        import tkinter.font as tkfont
        base = tkfont.nametofont("TkDefaultFont")
        bold = base.copy()
        bold.configure(weight="bold")
        self._new_font = bold
        tree.tag_configure("new", foreground=color, font=bold)

    # ------------------------------------------------------------------ now playing / tray
    def open_now_playing(self):
        from now_playing import NowPlayingWindow
        NowPlayingWindow.show(self)

    def start_tray(self):
        if self.tray is not None or not self.show_tray_var.get():
            return
        try:
            from now_playing import TrayIcon
            icon = resource_path("assets/icons/browser.png")
            self.tray = TrayIcon(str(icon), schedule=lambda fn: self.messages.put(("call", fn)),
                                 on_show=self._tray_show, on_now_playing=self.open_now_playing,
                                 on_quit=self.on_window_close)
        except Exception as e:
            print(f"[tray] unavailable: {e}")
            self.tray = None

    def stop_tray(self):
        if self.tray is not None:
            self.tray.stop()
            self.tray = None

    def toggle_tray(self):
        if self.show_tray_var.get():
            self.start_tray()
        else:
            self.stop_tray()
        self.save_settings()

    def _tray_show(self):
        self.deiconify()
        self.lift()
        self.focus_force()
