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
import tkinter.font as tkfont
import ttkbootstrap as tb
from player_launcher import launch_player, player_script_for, write_theme_preview, clear_theme_preview
from ttkbootstrap.constants import *
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from collections import defaultdict
from config import DOMAINS, get_cache_directory, get_cache_path, get_temp_directory, USER_AGENT, format_size, resource_path, apply_global_font_scale
from network import fetch_directory, get_entries_from_domain_entries, get_size_from_domain_entries, safe_filename, unique_path
from players import ExtractedFilesBrowserWindow, get_vgmstream_path
from index_updater import update_domain_index
from downloaders import EXCLUDED_FROM_INDEX_ALL, DownloadProgressWindow, IndexAllWindow, PremadeIndexProgressWindow, install_dependencies_prompt, check_and_download_sid_firmware


class ColumnAutoSizer:
    """Keeps a Treeview's columns wide enough to show all of their text.

    * Every column's width AND minwidth become the widest text in it (heading
      or any cell) plus spacing, so Tk can never squeeze it and cut text off.
    * Grows live: the tree's insert/set/item methods are wrapped so any new or
      changed cell text widens its column right away (only the new text is
      measured, so large listings stay fast).
    * A full re-measure happens when the tree is cleared and refilled, and on
      font-size changes via refit().
    """

    BASE_FONT_SIZE = 10

    def __init__(self, tree, base_widths=None):
        self.tree = tree
        self.base_widths = dict(base_widths or {})
        self.widths = {}
        self._measure_cache = {}
        self._pending = None
        self._fonts = {}
        self._load_fonts()
        self._wrap_tree_methods()
        self.refit()

    # --- fonts / spacing -------------------------------------------------
    def _style_font(self, style_name, fallback):
        try:
            spec = ttk.Style().lookup(style_name, "font")
            if spec:
                return tkfont.Font(font=spec)
        except Exception:
            pass
        return tkfont.nametofont(fallback)

    def _load_fonts(self):
        self.cell_font = self._style_font("Treeview", "TkDefaultFont")
        self.heading_font = self._style_font("Treeview.Heading", "TkHeadingFont")
        self._fonts = {}
        self._measure_cache = {}

    def _tag_font(self, tags):
        """Font of the first tag that sets one (e.g. bold separator rows)."""
        for tag in tags or ():
            if tag in self._fonts:
                if self._fonts[tag] is not None:
                    return self._fonts[tag]
                continue
            font = None
            try:
                spec = self.tree.tag_configure(tag, "font")
                if spec:
                    font = tkfont.Font(font=spec)
            except Exception:
                pass
            self._fonts[tag] = font
            if font is not None:
                return font
        return self.cell_font

    def spacing(self):
        # Gap on each side of the text, scaled with the font
        return max(12, self.cell_font.measure("0") * 2)

    def _measure(self, text, font):
        key = (text, str(font))
        w = self._measure_cache.get(key)
        if w is None:
            w = max((font.measure(line) for line in text.splitlines()), default=0)
            self._measure_cache[key] = w
        return w

    # --- tracking --------------------------------------------------------
    def _note(self, col, text, font=None):
        if text in ("", None):
            return
        w = self._measure(str(text), font or self.cell_font)
        if w > self.widths.get(col, 0):
            self.widths[col] = w
            self._schedule_apply()

    def _note_values(self, values, tags=()):
        if not values:
            return
        if isinstance(values, str):
            values = self.tree.tk.splitlist(values)
        font = self._tag_font(tags)
        for col, val in zip(self.tree["columns"], values):
            self._note(col, val, font)

    def _reset_to_headings(self):
        self.widths = {}
        for col in self.tree["columns"]:
            text = str(self.tree.heading(col, "text") or "")
            # Room for a sort arrow so the header doesn't jump when sorted
            self.widths[col] = self._measure(text + " ▲", self.heading_font)

    def _wrap_tree_methods(self):
        tree = self.tree
        orig_insert, orig_set, orig_item, orig_delete = tree.insert, tree.set, tree.item, tree.delete

        def insert(parent, index, iid=None, **kw):
            result = orig_insert(parent, index, iid=iid, **kw)
            self._note_values(kw.get("values"), kw.get("tags") or ())
            return result

        def set_(item, column=None, value=None):
            result = orig_set(item, column, value)
            if column is not None and value is not None:
                self._note(column, value, self._tag_font(orig_item(item, "tags")))
            return result

        def item(item_id, option=None, **kw):
            result = orig_item(item_id, option, **kw)
            if "values" in kw or "tags" in kw:
                self._note_values(orig_item(item_id, "values"), orig_item(item_id, "tags"))
            return result

        def delete(*items):
            result = orig_delete(*items)
            if not tree.get_children():
                # Emptied: let columns shrink back when the new content arrives
                self._reset_to_headings()
                self._schedule_apply()
            return result

        tree.insert, tree.set, tree.item, tree.delete = insert, set_, item, delete

    # --- applying --------------------------------------------------------
    def _schedule_apply(self):
        if self._pending is None:
            try:
                self._pending = self.tree.after_idle(self._apply)
            except Exception:
                self._pending = None

    def _apply(self):
        self._pending = None
        if not self.tree.winfo_exists():
            return
        pad = self.spacing() * 2
        scale = self.cell_font.actual("size") / self.BASE_FONT_SIZE if self.cell_font.actual("size") > 0 else 1
        for col in self.tree["columns"]:
            floor = int(self.base_widths.get(col, 30) * max(1.0, scale))
            target = max(self.widths.get(col, 0) + pad, floor)
            if int(self.tree.column(col, "minwidth")) != target or int(self.tree.column(col, "width")) < target:
                self.tree.column(col, width=target, minwidth=target)

    def refit_if_font_changed(self):
        old = (self.cell_font.actual(), self.heading_font.actual())
        cell = self._style_font("Treeview", "TkDefaultFont")
        head = self._style_font("Treeview.Heading", "TkHeadingFont")
        if (cell.actual(), head.actual()) != old:
            self.refit()
        else:
            self._schedule_apply()

    def refit(self):
        """Re-measure everything (e.g. after a font-size change)."""
        self._load_fonts()
        self._reset_to_headings()
        for item_id in self.tree.get_children():
            self._note_values(self.tree.item(item_id, "values"), self.tree.item(item_id, "tags"))
        self._schedule_apply()


def autofit_treeview_columns(tree, padding=None, min_widths=None):
    """Re-measure a tree's columns (kept for existing callers)."""
    sizer = getattr(tree, "_column_sizer", None)
    if sizer is None:
        tree._column_sizer = ColumnAutoSizer(tree, min_widths)
    else:
        sizer.refit()


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
        label = tb.Label(
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


class JoshWBrowser(tb.Window):
    
    COLUMN_FLOOR_WIDTHS = {"favorite": 30, "type": 60, "name": 200, "date": 60,
                           "size": 60, "domain": 60, "url": 200}

    def _setup_tree_scrolling_and_sizing(self, frame, tree):
        """Vertical + horizontal scrollbars and live column auto-sizing."""
        y_scroll = tb.Scrollbar(frame, orient="vertical", command=tree.yview)
        x_scroll = tb.Scrollbar(frame, orient="horizontal", command=tree.xview)
        tree.configure(yscrollcommand=y_scroll.set, xscrollcommand=x_scroll.set)
        tree.grid(row=0, column=0, sticky="nsew")
        y_scroll.grid(row=0, column=1, sticky="ns")
        x_scroll.grid(row=1, column=0, sticky="ew")
        frame.grid_rowconfigure(0, weight=1)
        frame.grid_columnconfigure(0, weight=1)
        tree._column_sizer = ColumnAutoSizer(tree, self.COLUMN_FLOOR_WIDTHS)

    def _apply_tree_row_height(self):
        """Row height follows the font so rows never overlap vertically."""
        try:
            spec = self.style.lookup("Treeview", "font") or "TkDefaultFont"
            linespace = tkfont.Font(font=spec).metrics("linespace")
            self.style.configure("Treeview", rowheight=int(linespace * 1.4) + 2)
        except Exception as e:
            print(f"Could not set Treeview row height: {e}")

    def change_font_size(self, new_size):
        self.font_size.set(new_size)
        apply_global_font_scale(new_size)
        self.save_settings()

        self._apply_tree_row_height()
        self._configure_fav_separator_tags()
        # Recalculate Treeview column widths for new font dimensions
        if hasattr(self, "tree") and self.tree.winfo_exists():
            autofit_treeview_columns(self.tree)
        if hasattr(self, "fav_tree") and self.fav_tree.winfo_exists():
            autofit_treeview_columns(self.fav_tree)
        if hasattr(self, "shuffle_tree") and self.shuffle_tree.winfo_exists():
            autofit_treeview_columns(self.shuffle_tree)
    
    def open_choose_random_domains_window(self):
        win = tb.Toplevel(self)
        win.title("Choose Random Stream Domains")
        win.geometry("420x520")
        win.transient(self)
        try:
            win.grab_set()
        except Exception:
            pass

        tb.Label(
            win,
            text="Select domains to include in 'Stream Random (All Indexes)':",
            font=("Arial", 9, "bold"),
            wraplength=380,
            justify="left"
        ).pack(pady=(12, 6), padx=15, anchor="w")

        btn_frame = tb.Frame(win)
        btn_frame.pack(fill="x", padx=15, pady=(0, 8))

        container = tb.Frame(win)
        container.pack(fill="both", expand=True, padx=15, pady=(0, 12))

        canvas = tk.Canvas(container, highlightthickness=0)
        scrollbar = tb.Scrollbar(container, orient="vertical", command=canvas.yview)
        scroll_frame = tb.Frame(canvas)

        scroll_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas_window = canvas.create_window((0, 0), window=scroll_frame, anchor="nw")
        
        canvas.bind("<Configure>", lambda e: canvas.itemconfig(canvas_window, width=e.width))

        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        def _on_mousewheel(event):
            if canvas.winfo_exists():
                canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        
        canvas.bind_all("<MouseWheel>", _on_mousewheel)
        win.bind("<Destroy>", lambda e: canvas.unbind_all("<MouseWheel>"))

        valid_domains = [d for d, url in DOMAINS.items() if url]
        self.domain_vars = {}

        def update_and_save():
            excluded = [d for d, v in self.domain_vars.items() if not v.get()]
            self.excluded_stream_domains = excluded
            self.save_settings()

        def toggle_all(state):
            for d, v in self.domain_vars.items():
                v.set(state)
            update_and_save()

        tb.Button(btn_frame, text="Select All", width=12, command=lambda: toggle_all(True)).pack(side="left", padx=(0, 5))
        tb.Button(btn_frame, text="Deselect All", width=12, command=lambda: toggle_all(False)).pack(side="left")

        excluded_set = set(getattr(self, "excluded_stream_domains", []))

        for domain in valid_domains:
            # If domain is missing from settings.json, domain not in excluded_set evaluates to True (chosen)
            var = tk.BooleanVar(value=(domain not in excluded_set))
            self.domain_vars[domain] = var
            chk = tb.Checkbutton(
                scroll_frame,
                text=domain,
                variable=var,
                command=update_and_save
            )
            chk.pack(anchor="w", pady=3, padx=5)
    
    def on_domain_combobox_select(self, event=None):
        selected = self.domain_combo.get()
        # If the user selected a category header or empty item, revert selection
        if not DOMAINS.get(selected, ""):
            if hasattr(self, "last_valid_domain") and self.last_valid_domain:
                self.domain_combo.set(self.last_valid_domain)
        else:
            self.last_valid_domain = selected
    
    def change_theme(self, theme_name):
        """Apply a theme and persist it to settings.json."""
        self.saved_theme = theme_name
        self._cancel_theme_preview_revert()
        self._apply_theme(theme_name)

        try:
            settings = self.load_settings() or {}
            settings["theme"] = theme_name
            config_path = os.path.join(get_cache_directory(), "settings.json")
            with open(config_path, "w", encoding="utf-8") as f:
                json.dump(settings, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Could not save theme to settings: {e}")
        # Clear only after saving so players go straight to the new saved theme
        clear_theme_preview()

    # --- Theme hover preview -------------------------------------------------
    def _is_theme_menu_event(self, event):
        # Menus in a menubar are Tk clones, so match on the explicit widget name
        return "theme_menu" in str(event.widget)

    def _on_theme_menu_select(self, menu_path):
        """Temporarily apply the theme under the cursor in the Themes menu.

        Receives the raw Tcl path (%W) of the menu clone. Tkinter's event.widget
        maps the clone back to the original menu, which has no active entry.
        """
        if "theme_menu" not in str(menu_path):
            return
        try:
            label = self.tk.call(menu_path, "entrycget", "active", "-label")
        except tk.TclError:
            return  # no active entry
        if label and label in self.style.theme_names():
            self._cancel_theme_preview_revert()
            self._apply_theme(label)
            write_theme_preview(label)  # standalone player processes follow along

    def _on_theme_menu_unmap(self, event):
        """Themes menu closed: revert to the saved theme unless one was chosen."""
        if not self._is_theme_menu_event(event):
            return
        self._cancel_theme_preview_revert()
        # Delay so a click's command (change_theme) runs before we decide to revert
        self._theme_revert_job = self.after(150, self._end_theme_preview)

    def _cancel_theme_preview_revert(self):
        job = getattr(self, "_theme_revert_job", None)
        if job:
            try:
                self.after_cancel(job)
            except Exception:
                pass
        self._theme_revert_job = None

    def _end_theme_preview(self):
        self._theme_revert_job = None
        clear_theme_preview()
        if getattr(self, "_applied_theme", None) != self.saved_theme:
            self._apply_theme(self.saved_theme)

    def _apply_theme(self, theme_name):
        """Apply a theme's visuals without saving it to settings."""
        if getattr(self, "_applied_theme", None) == theme_name:
            return
        self._applied_theme = theme_name
        try:
            self.style.theme_use(theme_name)
        except Exception as e:
            print(f"Could not apply theme: {e}")

        theme_lower = theme_name.lower()
        hover_color = "#2a3b4c" if "dark" in theme_lower else "#e8f0fe"
        border_factor = 1.3 if "dark" in theme_lower else 0.82

        try:
            base_color = None
            if hasattr(self.style, "colors") and hasattr(self.style.colors, "bg"):
                base_color = self.style.colors.bg
            if not base_color:
                base_color = self.style.lookup("TFrame", "background") or "#ffffff"
            
            def darken_color(hex_str, factor=0.9):
                if not hex_str or not isinstance(hex_str, str):
                    return "#ffffff"
                hex_str = hex_str.strip('#')
                if len(hex_str) != 6:
                    return f"#{hex_str}"
                try:
                    r = int(hex_str[0:2], 16)
                    g = int(hex_str[2:4], 16)
                    b = int(hex_str[4:6], 16)
                    r = max(0, min(255, int(r * factor)))
                    g = max(0, min(255, int(g * factor)))
                    b = max(0, min(255, int(b * factor)))
                    return f"#{r:02x}{g:02x}{b:02x}"
                except Exception:
                    return f"#{hex_str}"

            field_bg = darken_color(base_color, 0.9 if "dark" in theme_lower else 0.96)
            self.style.configure("Treeview", fieldbackground=field_bg)
        except Exception as e:
            print(f"Could not configure Treeview fieldbackground: {e}")

        hex_clean = hover_color.strip('#')
        try:
            hr = int(hex_clean[0:2], 16)
            hg = int(hex_clean[2:4], 16)
            hb = int(hex_clean[4:6], 16)
            hr = max(0, min(255, int(hr * border_factor)))
            hg = max(0, min(255, int(hg * border_factor)))
            hb = max(0, min(255, int(hb * border_factor)))
            hover_border_color = f"#{hr:02x}{hg:02x}{hb:02x}"
        except Exception:
            hover_border_color = "#555555" if "dark" in theme_lower else "#cccccc"

        try:
            self.style.map("Treeview",
                bordercolor=[("active", hover_border_color), ("focus", hover_border_color)],
                lightcolor=[("active", hover_border_color), ("focus", hover_border_color)],
                darkcolor=[("active", hover_border_color), ("focus", hover_border_color)]
            )
        except Exception as e:
            print(f"Could not configure Treeview border styles: {e}")

        if hasattr(self, "tree") and hasattr(self, "fav_tree"):
            self.tree.tag_configure("hover", background=hover_color)
            self.fav_tree.tag_configure("hover", background=hover_color)
            if hasattr(self, "shuffle_tree"):
                self.shuffle_tree.tag_configure("hover", background=hover_color)
            self._configure_fav_separator_tags()
            # A theme switch rebuilds styles: restore row height, re-check widths
            self._apply_tree_row_height()
            for t in (self.tree, self.fav_tree, getattr(self, "shuffle_tree", None)):
                sizer = getattr(t, "_column_sizer", None) if t is not None else None
                if sizer:
                    sizer.refit_if_font_changed()

    def _configure_fav_separator_tags(self):
        """Colour the favourites spacer/separator rows for the current theme."""
        if not hasattr(self, "fav_tree"):
            return
        theme_lower = (getattr(self, "_applied_theme", None) or self.saved_theme).lower()
        border_color = "#666666" if "dark" in theme_lower else "#999999"
        self.fav_tree.tag_configure("space_row", background=self.style.lookup("Treeview", "background") or "#ffffff")
        try:
            sep_size = int(self.font_size.get())
        except Exception:
            sep_size = 10
        self.fav_tree.tag_configure("separator_row", foreground=border_color, font=("Arial", sep_size, "bold"))
    
    def on_tree_motion(self, event):
        tree = self.tree
        item_id = tree.identify_row(event.y)
        for item in tree.get_children():
            current_tags = list(tree.item(item, "tags"))
            if "hover" in current_tags:
                current_tags.remove("hover")
                tree.item(item, tags=current_tags)
                
        if item_id:
            current_tags = list(tree.item(item_id, "tags"))
            if "hover" not in current_tags:
                current_tags.append("hover")
                tree.item(item_id, tags=current_tags)

    def on_tree_leave(self, event):
        for item in self.tree.get_children():
            current_tags = list(self.tree.item(item, "tags"))
            if "hover" in current_tags:
                current_tags.remove("hover")
                self.tree.item(item, tags=current_tags)

    def on_fav_motion(self, event):
        tree = self.fav_tree
        item_id = tree.identify_row(event.y)
        for item in tree.get_children():
            current_tags = list(tree.item(item, "tags"))
            if "hover" in current_tags:
                current_tags.remove("hover")
                tree.item(item, tags=current_tags)
                
        if item_id:
            if hasattr(self, "fav_display_mapping") and self.fav_display_mapping.get(item_id) is None:
                return
            current_tags = list(tree.item(item_id, "tags"))
            if "hover" not in current_tags:
                current_tags.append("hover")
                tree.item(item_id, tags=current_tags)

    def on_fav_leave(self, event):
        for item in self.fav_tree.get_children():
            current_tags = list(self.fav_tree.item(item, "tags"))
            if "hover" in current_tags:
                current_tags.remove("hover")
                self.fav_tree.item(item, tags=current_tags)

    def on_shuffle_motion(self, event):
        if not hasattr(self, "shuffle_tree"):
            return
        tree = self.shuffle_tree
        item_id = tree.identify_row(event.y)
        for item in tree.get_children():
            current_tags = list(tree.item(item, "tags"))
            if "hover" in current_tags:
                current_tags.remove("hover")
                tree.item(item, tags=current_tags)

        if item_id:
            current_tags = list(tree.item(item_id, "tags"))
            if "hover" not in current_tags:
                current_tags.append("hover")
                tree.item(item_id, tags=current_tags)

    def on_shuffle_leave(self, event):
        if not hasattr(self, "shuffle_tree"):
            return
        for item in self.shuffle_tree.get_children():
            current_tags = list(self.shuffle_tree.item(item, "tags"))
            if "hover" in current_tags:
                current_tags.remove("hover")
                self.shuffle_tree.item(item, tags=current_tags)
    
    def get_favorites_path(self):
        return os.path.join(get_cache_directory(), "favorites.json")

    def load_favorites(self):
        path = self.get_favorites_path()
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        return data
            except Exception:
                pass
        return []

    def save_favorites(self, favorites):
        path = self.get_favorites_path()
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(favorites, f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def toggle_favorite_by_entry(self, entry):
        if entry.get("directory") or entry.get("is_parent_nav"):
            return
        favorites = self.load_favorites()
        url = entry.get("url")
        existing = next((f for f in favorites if f.get("url") == url), None)
        if existing:
            favorites = [f for f in favorites if f.get("url") != url]
        else:
            # Map raw domain keys/urls to friendly platform display names
            raw_domain = entry.get("domain") or self.domain_combo.get()
            
            domain_map = {
                "gbs": "Game Boy",
                "nsf": "NES",
                "spc": "SNES",
                "usf": "Nintendo 64",
                "gsf": "Game Boy Advance",
                "2sf": "Nintendo DS",
                "3sf": "Nintendo 3DS",
                "psf": "PlayStation",
                "psf2": "PlayStation 2",
                "hes": "PC Engine",
                "wsr": "WonderSwan",
                "kss": "MSX / Neo Geo",
                "Switch": "Nintendo Switch",
                "vita": "PS Vita",
                "wii": "Wii"
            }
            
            clean_domain = domain_map.get(raw_domain.lower(), raw_domain)
            entry["domain"] = clean_domain
            favorites.append(entry)
            
        self.save_favorites(favorites)
        self.apply_filter()
        self.refresh_favorites_tab()
        self.update_shuffle_stars()

    def build_favorite_display_items(self, sorted_favs, col, asc):
        if not sorted_favs:
            return []

        groups = defaultdict(list)

        for entry in sorted_favs:
            if col == "name":
                name = entry.get("name", "").strip()
                first_char = name[0].upper() if name else "#"
                key = first_char if first_char.isalnum() else "#"
            elif col == "domain":
                key = entry.get("domain", "Unknown").strip() or "Unknown"
            elif col == "type":
                key = "Directory" if entry.get("directory") else "File"
            elif col == "date":
                key = entry.get("date", "Unknown").strip() or "Unknown"
            elif col == "size":
                sz = entry.get("size")
                if entry.get("directory"):
                    key = "Directories"
                elif sz is None:
                    key = "Unknown Size"
                elif sz < 1024 * 1024:
                    key = "Under 1 MB"
                elif sz < 10 * 1024 * 1024:
                    key = "1 MB - 10 MB"
                else:
                    key = "Over 10 MB"
            elif col == "url":
                parsed = urllib.parse.urlparse(entry.get("url", ""))
                key = parsed.netloc or "Other"
            else:
                name = entry.get("name", "").strip()
                first_char = name[0].upper() if name else "#"
                key = first_char if first_char.isalnum() else "#"

            groups[key].append(entry)

        sorted_keys = sorted(groups.keys(), reverse=not asc if col in ("name", "domain") else False)

        display_items = []
        for i, key in enumerate(sorted_keys):
            group_entries = groups[key]
            
            display_items.append({"type": "separator", "text": f"┄┄┄┄┄┄┄┄ {key} ┄┄┄┄┄┄┄┄"})
            display_items.append({"type": "space"})
            
            for idx, entry in enumerate(group_entries):
                is_last = (idx == len(group_entries) - 1)
                display_items.append({"type": "entry", "entry": entry, "is_last": is_last})

            if i < len(sorted_keys) - 1:
                display_items.append({"type": "space"})

        return display_items

    def refresh_favorites_tab(self):
        if not hasattr(self, "fav_tree"):
            return
        self.fav_tree.delete(*self.fav_tree.get_children())
        favs = self.load_favorites()

        if not hasattr(self, "fav_sort_column"):
            self.fav_sort_column = "name"
            self.fav_sort_ascending = True

        orig_col = self.current_sort_column
        orig_asc = self.current_sort_ascending
        
        self.current_sort_column = self.fav_sort_column
        self.current_sort_ascending = self.fav_sort_ascending
        
        sorted_favs = self.apply_sorting(favs)
        
        self.current_sort_column = orig_col
        self.current_sort_ascending = orig_asc

        self.save_favorites(sorted_favs)

        display_items = self.build_favorite_display_items(sorted_favs, self.fav_sort_column, self.fav_sort_ascending)
        self.fav_display_mapping = {}

        for index, item_data in enumerate(display_items):
            item_id = str(index)
            if item_data["type"] == "entry":
                entry = item_data["entry"]
                self.fav_display_mapping[item_id] = entry
                item_size = "-" if entry.get("directory") else format_size(entry.get("size"))
                
                tag_name = "group_item_last" if item_data.get("is_last") else "group_item"
                
                self.fav_tree.insert("", "end", iid=item_id, values=(
                    "★",
                    "Directory" if entry.get("directory") else "File",
                    entry["name"],
                    entry.get("date", ""),
                    item_size,
                    entry.get("domain", ""),
                    urllib.parse.unquote(entry["url"])
                ), tags=(tag_name,))
                
            elif item_data["type"] == "space":
                self.fav_display_mapping[item_id] = None
                self.fav_tree.insert("", "end", iid=item_id, values=("", "", "", "", "", "", ""), tags=("space_row",))
            elif item_data["type"] == "separator":
                self.fav_display_mapping[item_id] = None
                sep_text = item_data.get("text", f"┄┄┄┄ Group ┄┄┄┄")
                # Ensure all other columns receive blank strings so they don't hold residual alignment text
                self.fav_tree.insert("", "end", iid=item_id, values=("", "", sep_text, "", "", "", ""), tags=("separator_row",))

        self._configure_fav_separator_tags()
        self.fav_tree.tag_configure("group_item")
        self.fav_tree.tag_configure("group_item_last")  

        autofit_treeview_columns(self.fav_tree)

    def on_browse_right_click(self, event):
        item_id = self.tree.identify_row(event.y)
        if item_id:
            try:
                entry = self.visible_entries[int(item_id)]
                if not entry.get("directory") and not entry.get("is_parent_nav"):
                    self.toggle_favorite_by_entry(entry)
            except Exception:
                pass

    def on_fav_right_click(self, event):
        item_id = self.fav_tree.identify_row(event.y)
        if item_id and hasattr(self, "fav_display_mapping"):
            entry = self.fav_display_mapping.get(item_id)
            if entry:
                self.toggle_favorite_by_entry(entry)

    def double_click_favorite(self, event=None):
        temp_dir = get_temp_directory()
        check_dir = temp_dir if os.path.exists(temp_dir) else os.path.dirname(temp_dir)
        hdd_space = format_size(shutil.disk_usage(check_dir).free)
               
        selection = self.fav_tree.selection()
        if not selection:
            return
        item_id = selection[0]
        if hasattr(self, "fav_display_mapping"):
            entry = self.fav_display_mapping.get(item_id)
            if not entry:
                return
            try:
                dialog = tb.Toplevel(self)
                dialog.title("File Action")
                dialog.geometry("450x140")
                dialog.resizable(False, False)
                dialog.transient(self)
                try:
                    dialog.update_idletasks()
                    dialog.grab_set()
                except Exception:
                    pass

                filename = entry["name"]
                tb.Label(dialog, text=f"Choose action for: {filename}\n\n You have {hdd_space} left in your temp directory.", font=("Arial", 9, "bold"), justify="center", wraplength=310).pack(pady=(15, 10))
                btn_frame = tb.Frame(dialog)
                btn_frame.pack(pady=5)

                url = entry["url"]
                stream_btn = tb.Button(btn_frame, text="Stream", width=12, command=lambda: [dialog.destroy(), self.stream_specific_url(url)])
                stream_btn.pack(side="left", padx=8)
                download_btn = tb.Button(btn_frame, text="Download", width=12, command=lambda: [dialog.destroy(), self.download_specific_url(url)])
                download_btn.pack(side="left", padx=8)
                stream_btn.focus_set()
            except Exception:
                pass

    def stream_specific_url(self, url):
        temp_dir = get_temp_directory()
        self.stop_event.clear()
        progress_win = DownloadProgressWindow(self, title="Downloading to Temp...")
        threading.Thread(
            target=self.download_and_open_temp_worker,
            args=(url, temp_dir, progress_win),
            daemon=True
        ).start()

    def download_specific_url(self, url):
        dest_dir = self.destination.get().strip() if self.use_default_dest.get() else ""
        if not dest_dir or not os.path.isdir(dest_dir):
            dest_dir = filedialog.askdirectory(title="Choose destination folder", initialdir=self.destination.get())
            if not dest_dir:
                return
            self.destination.set(dest_dir)
            self.save_destination_history(dest_dir)

        self.stop_event.clear()
        progress_win = DownloadProgressWindow(self, title="Downloading File...")
        threading.Thread(
            target=self.download_files_worker,
            args=([url], dest_dir, progress_win),
            daemon=True
        ).start()

    def rebuild_stream_random_button(self):
        has_files = any(not e.get("directory") for e in (self.domain_full_entries if self.domain_full_entries else self.current_entries))
        is_enabled = has_files or self.stream_random_all_indexes_var.get()
        state_str = "normal" if is_enabled else "disabled"

        if self.stream_random_anim_id:
            try:
                self.after_cancel(self.stream_random_anim_id)
            except Exception:
                pass
            self.stream_random_anim_id = None

        if hasattr(self, "stream_random_btn") and self.stream_random_btn.winfo_exists():
            self.stream_random_btn.destroy()

        if self.stream_random_animation_enabled.get():
            current_color = self.fade_colors[self.fade_index] if is_enabled else "#f0f0f0"
            self.stream_random_btn = tk.Button(
                self.button_frame,
                text="Stream Random!",
                command=self.stream_random_file,
                state=state_str,
                bg=current_color,
                fg="#000000" if is_enabled else "#555555",
                activebackground=current_color,
                activeforeground="#000000" if is_enabled else "#555555",
                disabledforeground="#555555",
                relief="raised",
                font=("Arial", 9)
            )
        else:
            self.stream_random_btn = tb.Button(
                self.button_frame,
                text="Stream Random!",
                command=self.stream_random_file,
                state=state_str
            )

        if hasattr(self, "stream_random_all_indexes_chk") and self.stream_random_all_indexes_chk.winfo_exists():
            self.stream_random_btn.pack(side="left", padx=2, before=self.stream_random_all_indexes_chk)
        else:
            self.stream_random_btn.pack(side="left", padx=2)

        if self.stream_random_animation_enabled.get() and is_enabled:
            self.animate_stream_random_button()

    def on_toggle_animation_setting(self):
        self.rebuild_stream_random_button()

    def animate_stream_random_button(self):
        if not hasattr(self, "stream_random_btn") or not self.stream_random_btn.winfo_exists():
            return
        
        if (isinstance(self.stream_random_btn, tk.Button) and 
            str(self.stream_random_btn["state"]) in ("normal", "active") and 
            self.stream_random_animation_enabled.get()):
            
            if not hasattr(self, "fade_substep"):
                self.fade_substep = 0

            num_colors = len(self.fade_colors)
            c1 = self.fade_colors[self.fade_index]
            c2 = self.fade_colors[(self.fade_index + 1) % num_colors]
            
            total_steps = 15  # Number of frames between each main color
            t = self.fade_substep / total_steps
            
            try:
                # Interpolate R, G, B components
                r1, g1, b1 = int(c1[1:3], 16), int(c1[3:5], 16), int(c1[5:7], 16)
                r2, g2, b2 = int(c2[1:3], 16), int(c2[3:5], 16), int(c2[5:7], 16)
                
                r = int(r1 + (r2 - r1) * t)
                g = int(g1 + (g2 - g1) * t)
                b = int(b1 + (b2 - b1) * t)
                
                color = f"#{r:02x}{g:02x}{b:02x}"
                
                self.stream_random_btn.configure(
                    bg=color,
                    activebackground=color,
                    fg="#000000",
                    activeforeground="#000000"
                )
            except Exception:
                pass

            self.fade_substep += 1
            if self.fade_substep >= total_steps:
                self.fade_substep = 0
                self.fade_index = (self.fade_index + 1) % num_colors

            # 35ms delay * 15 steps gives a smooth ~500ms transition per color phase
            self.stream_random_anim_id = self.after(35, self.animate_stream_random_button)
        else:
            self.stream_random_anim_id = None

    def update_stream_random_state(self, enable):
        if not hasattr(self, "stream_random_btn") or not self.stream_random_btn.winfo_exists():
            return
        
        is_active = enable or self.stream_random_all_indexes_var.get()
        
        if isinstance(self.stream_random_btn, tk.Button):
            if is_active:
                self.stream_random_btn.configure(
                    state="normal",
                    fg="#000000",
                    activeforeground="#000000"
                )
                if self.stream_random_animation_enabled.get() and not self.stream_random_anim_id:
                    self.animate_stream_random_button()
            else:
                self.stream_random_btn.configure(
                    state="disabled", 
                    bg="#f0f0f0",
                    activebackground="#f0f0f0",
                    fg="#555555",
                    activeforeground="#555555"
                )
                if self.stream_random_anim_id:
                    try:
                        self.after_cancel(self.stream_random_anim_id)
                    except Exception:
                        pass
                    self.stream_random_anim_id = None
        else:
            self.stream_random_btn.configure(state="normal" if is_active else "disabled")
            if self.stream_random_anim_id:
                try:
                    self.after_cancel(self.stream_random_anim_id)
                except Exception:
                    pass
                self.stream_random_anim_id = None   
    
    def stream_random_file(self):
        if self.stream_random_all_indexes_var.get():
            source_pool = self.load_all_index_entries()
            excluded = set(getattr(self, "excluded_stream_domains", []))
            if excluded:
                valid_domains = {d: url for d, url in DOMAINS.items() if url}
                def entry_is_allowed(entry):
                    entry_url = entry.get("url", "")
                    entry_dom = entry.get("domain", "").lower()
                    for ex_domain in excluded:
                        ex_url = valid_domains.get(ex_domain, "")
                        if ex_url and entry_url.startswith(ex_url):
                            return False
                        if entry_dom and entry_dom == ex_domain.lower():
                            return False
                        if ex_url:
                            parsed_ex = urllib.parse.urlparse(ex_url)
                            if parsed_ex.netloc and entry_dom and entry_dom in parsed_ex.netloc.lower():
                                return False
                    return True
                source_pool = [e for e in source_pool if entry_is_allowed(e)]
        else:
            source_pool = self.domain_full_entries if self.domain_full_entries else self.current_entries

        files = [e for e in source_pool if not e.get("directory") and not e.get("is_parent_nav")]
        
        if not files:
            messagebox.showinfo("No Files", "No files available to pick from!")
            return

        import random
        
        rand_win = tk.Toplevel(self)
        rand_win.title("Stream Random!")
        rand_win.geometry("500x220")
        rand_win.resizable(False, False)
        rand_win.transient(self)
        try:
            rand_win.grab_set()
        except Exception:
            pass

        content_frame = tb.Frame(rand_win, padding=15)
        content_frame.pack(fill="both", expand=True)
        content_frame.columnconfigure(0, weight=1)

        text_label = tb.Label(content_frame, text="Rolling the dice...", font=("Arial", 11, "bold"), justify="center", anchor="center")
        text_label.grid(row=0, column=0, pady=(5, 10), sticky="ew")

        img_label = tb.Label(content_frame, anchor="center")
        img_label.grid(row=1, column=0, pady=(5, 5))

        bottom_frame = tb.Frame(rand_win, padding=10)

        from PIL import Image, ImageTk, ImageDraw
        dice_size = 64

        def create_dice_face(pip_coords):
            img = Image.new("RGBA", (dice_size, dice_size), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.rounded_rectangle([4, 4, dice_size-4, dice_size-4], radius=12, fill="white", outline="#333333", width=3)
            pip_r = 5
            for cx, cy in pip_coords:
                draw.ellipse([cx-pip_r, cy-pip_r, cx+pip_r, cy+pip_r], fill="#222222")
            return img

        dice_faces = {
            1: create_dice_face([(32, 32)]),
            2: create_dice_face([(18, 18), (46, 46)]),
            3: create_dice_face([(18, 18), (32, 32), (46, 46)]),
            4: create_dice_face([(18, 18), (46, 18), (18, 46), (46, 46)]),
            5: create_dice_face([(18, 18), (46, 18), (32, 32), (18, 46), (46, 46)]),
            6: create_dice_face([(18, 18), (18, 32), (18, 46), (46, 18), (46, 32), (46, 46)])
        }

        state = {
            "chosen": None,
            "angle": 0,
            "face_num": 1,
            "steps_left": 0,
            "next_face_steps": 0
        }

        def pick_and_spin():
            state["chosen"] = random.choice(files)
            state["angle"] = 0
            state["face_num"] = random.randint(1, 6)
            state["steps_left"] = 70
            state["next_face_steps"] = random.randint(12, 25)
            
            bottom_frame.pack_forget()
            text_label.configure(text="Rolling the dice...", font=("Arial", 11, "bold"), justify="center", anchor="center")
            img_label.grid()
            spin_tick()

        def spin_tick():
            if not rand_win.winfo_exists():
                return
            
            if state["steps_left"] > 0:
                state["steps_left"] -= 1
                state["next_face_steps"] -= 1
                state["angle"] = (state["angle"] + 15) % 360
                
                if state["next_face_steps"] <= 0:
                    state["face_num"] = random.randint(1, 6)
                    state["next_face_steps"] = random.randint(12, 25)
                
                current_face = dice_faces[state["face_num"]]
                rotated = current_face.rotate(state["angle"], resample=Image.BICUBIC, expand=False)
                photo = ImageTk.PhotoImage(rotated)
                img_label.configure(image=photo)
                img_label.image = photo
                
                rand_win.after(30, spin_tick)
            else:
                img_label.grid_remove()
                chosen = state["chosen"]
                filename = chosen["name"]
                size_str = format_size(chosen.get("size", 0) or 0)
                
                # Compute disk space outside the conditional check so it's always defined
                try:
                    temp_dir = get_temp_directory()
                    check_dir = temp_dir if os.path.exists(temp_dir) else os.path.dirname(temp_dir)
                    hdd_space = format_size(shutil.disk_usage(check_dir).free)
                except Exception:
                    hdd_space = "unknown space"
                
                domain_str = ""
                if self.stream_random_all_indexes_var.get():
                    domain = None
                    chosen_url = chosen.get("url", "")
                    if chosen_url:
                        for d_name, d_url in DOMAINS.items():
                            if d_url and chosen_url.startswith(d_url):
                                domain = d_name
                                break
                    if not domain:
                        domain = chosen.get("domain") or self.domain_combo.get() or "Unknown Domain"
                    domain_str = f"{domain} - "

                final_text = (
                    f"You rolled and you got... {domain_str}{filename}! it is {size_str}.\n\n"
                    f"You have {hdd_space} remaining, do you want to stream it? It will "
                    f"download to the temp folder at {os.path.dirname(temp_dir)}/temp_extracted and "
                    f"unzip there, so make sure you have enough space before accepting!"
                )
                text_label.configure(
                    text=final_text,
                    font=("Arial", 10),
                    justify="left",
                    anchor="w",
                    wraplength=460
                )
                bottom_frame.pack(fill="x", side="bottom")

        def on_accept():
            chosen = state["chosen"]
            rand_win.destroy()
            self.download_specific_url_to_temp(chosen["url"])

        def on_cancel():
            rand_win.destroy()

        ok_btn = tb.Button(bottom_frame, text="OK", command=on_accept, width=10)
        ok_btn.pack(side="left")

        cancel_btn = tb.Button(bottom_frame, text="Cancel", command=on_cancel, width=10)
        cancel_btn.pack(side="right")

        roll_again_btn = tb.Button(bottom_frame, text="Roll Again!", command=pick_and_spin, width=12)
        roll_again_btn.pack(side="left", expand=True)

        pick_and_spin()

    def download_specific_url_to_temp(self, url):
        temp_dir = get_temp_directory()
        self.stop_event.clear()
        progress_win = DownloadProgressWindow(self, title="Downloading to Temp...")

        threading.Thread(
            target=self.download_and_open_temp_worker,
            args=(url, temp_dir, progress_win),
            daemon=True
        ).start()    
    
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
        elif dep_name == "vgmplay":
            return (
                shutil.which("vgmplay") is not None
                or os.path.exists(os.path.join(cache_dir, "vgmplay"))
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
        elif dep_name == "neobae_player":
            return (
                shutil.which("neobae_player") is not None
                or os.path.exists(os.path.join(cache_dir, "neobae_player"))
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
            ("Lazyusf", "lazyusf", "lazyusf"),
            ("VGMStream", "vgmstream", "vgmstream123"),
            ("SIDPlayFP", "sidplayfp", "sidplayfp"),
            ("PlayGSF", "playgsf", "playgsf"),
            ("2sfPlay", "2sfplay", "2sfPlay"),
            ("AOSDK (PSF/PSF2)", "psfplay", "libaosdk.so"),
            ("Arcade Player (vgmplay)", "vgmplay", "vgmplay"),
            ("NeoBAE (playbae)", "playbae", "playbae")
        ]
        cache_dir = Path(get_cache_directory())

        for display_name, key, bin_name in deps:
            is_installed = (cache_dir / bin_name).exists() or shutil.which(bin_name)
            if is_installed:
                self.deps_menu.add_command(
                    label=f"✓ {display_name} (Installed)", 
                    state="disabled"
                )

    def download_premade_indexes_prompt(self):
        indexes_dir = os.path.join(get_cache_directory(), "indexes")
        os.makedirs(indexes_dir, exist_ok=True)
        existing_caches = [f for f in os.listdir(indexes_dir) if f.endswith(".json")]
        
        if existing_caches:
            ans = messagebox.askokcancel("Are you sure?", "Are you sure? Existing local indexes will be replaced!")
            if not ans:
                return
            for f in existing_caches:
                try:
                    os.remove(os.path.join(indexes_dir, f))
                except Exception:
                    pass

        PremadeIndexProgressWindow(self)

    def __init__(self, *args, **kwargs):
        settings_path = os.path.join(get_cache_directory(), "settings.json")
        is_first_run = not os.path.isfile(settings_path)

        # Load initial settings into local variables before super().__init__
        initial_settings = self.read_settings_file()
        saved_theme = initial_settings.get("theme", "cosmo")

        # Initialize the parent Tkinter/ttkbootstrap window FIRST
        super().__init__(themename=saved_theme, *args, **kwargs)

        # Now safe to attach attributes to self
        self.is_first_run = is_first_run
        self._initial_settings = initial_settings
        self.saved_theme = saved_theme
        self.excluded_stream_domains = self._initial_settings.get("excluded_stream_domains", [])
        saved_keep_open = self._initial_settings.get("keep_download_window_open", True)
        self.keep_download_window_open = tk.BooleanVar(value=saved_keep_open)

        # Shuffle settings
        saved_shuffle_count = self._initial_settings.get("shuffle_count", 50)
        self.shuffle_count = tk.IntVar(value=saved_shuffle_count)
        self.shuffle_entries = []

        # Load saved font size and apply scale ON STARTUP
        saved_font_size = self._initial_settings.get("font_size", 10)
        self.font_size = tk.IntVar(value=saved_font_size)
        apply_global_font_scale(saved_font_size)

        self.title("JoshW Archive Browser")
        self.geometry("1280x760")
        self.minsize(850, 560)

        if sys.platform != "win32":
            try:
                sh_path = os.path.join(get_cache_directory(), "xmi2mid.sh")
                if not os.path.exists(sh_path):
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
            except Exception as e:
                print(f"Could not create xmi2mid.sh: {e}")

        try:
            icon_file = resource_path("assets/icons/browser.png")
            if icon_file.exists():
                self.iconphoto(False, tk.PhotoImage(file=str(icon_file)))
        except Exception as e:
            print(f"Could not load icon: {e}")

        self.protocol("WM_DELETE_WINDOW", self.on_window_close)

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

        self.nav_history = []
        self.nav_history_index = -1
        self.is_navigating_history = False

        self.current_sort_column = "type"
        self.current_sort_ascending = True

        saved_dest = self._initial_settings.get(
            "default_destination", os.path.expanduser("~/Downloads")
        )
        saved_use_default = self._initial_settings.get("use_default_dest", False)
        saved_load_dir = self._initial_settings.get(
            "default_load_music_dir", os.path.expanduser("~")
        )

        self.destination = tk.StringVar(value=saved_dest)
        self.use_default_dest = tk.BooleanVar(value=saved_use_default)
        self.default_load_music_dir = tk.StringVar(value=saved_load_dir)
        self._last_loaded_music_dir = saved_load_dir

        if sys.platform == "win32":
            self.path_7z = tk.StringVar(value="7z.exe")
            self.path_mpv = tk.StringVar(value="mpv.exe")
        else:
            self.path_7z = tk.StringVar(value="/bin/7z")
            self.path_mpv = tk.StringVar(value="/bin/mpv")

        self.search_text = tk.StringVar()
        self.search_all_indexes_var = tk.BooleanVar(value=False)
        self.stream_random_all_indexes_var = tk.BooleanVar(value=False)
        self.all_index_entries = []
        self.status_text = tk.StringVar(value="Choose an archive.")
        self.indexer_status_text = tk.StringVar(value="")
        self.auto_index_enabled = True

        self.messages = queue.Queue()
        self.stop_event = threading.Event()

        self.build_gui()
        self.refresh_favorites_tab()
        clear_theme_preview()  # drop a stale hover preview from a previous session
        self.change_theme(self.saved_theme)
        self.after(100, self.process_messages)

        sid_fw_dir = os.path.join(get_cache_directory(), "sid")
        if not os.path.exists(sid_fw_dir) or not os.listdir(sid_fw_dir):
            self.after(
                500,
                lambda: threading.Thread(
                    target=check_and_download_sid_firmware, daemon=True
                ).start(),
            )

        self.after(200, self.first_start)

    def read_settings_file(self):
        """Safely read settings without creating a file on disk."""
        path = self.get_settings_path()
        if os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}
    
    def first_start(self):
        """Runs setup prompts sequentially if settings.json was not present on startup."""
        if self.is_first_run:
            def continue_setup():
                PremadeIndexProgressWindow(self)
                self.save_settings()
                self.is_first_run = False

            install_dependencies_prompt(self, is_first_start=True, on_complete=continue_setup)

    def on_window_close(self, callback=None):
        self.stop_event.set()
        if hasattr(self, "stream_random_anim_id") and self.stream_random_anim_id:
            try:
                self.after_cancel(self.stream_random_anim_id)
            except Exception:
                pass
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
        if callback:
            return callback()
    
    def build_gui(self):
        menubar = tk.Menu(self)
        self.config(menu=menubar)

        archive_frame = tb.Frame(self, padding=8)
        archive_frame.pack(fill="x")

        help_btn = tk.Button(archive_frame, text="help!", bg="blue", fg="yellow", activebackground="blue", activeforeground="yellow", font=("Arial", 9, "bold"), command=self.show_help)
        help_btn.pack(side="right", padx=2)

        tb.Label(archive_frame, text="Archive:").pack(side="left")
        self.domain_combo = tb.Combobox(archive_frame, state="readonly", width=22, values=list(DOMAINS.keys()))
        self.domain_combo.pack(side="left", padx=5)
        self.domain_combo.current(0)

        tb.Button(archive_frame, text="Open", command=self.open_archive).pack(side="left", padx=2)
        
        load_music_btn = tb.Button(archive_frame, text="Load Music...", command=lambda: self.load_local_music(is_default=True))
        load_music_btn.pack(side="left", padx=2)
        ToolTip(load_music_btn, "click to find and play locally stored music with the built in player")

        tb.Button(archive_frame, text="Index Current", command=self.index_current_domain).pack(side="right", padx=2)
        tb.Button(archive_frame, text="Index All", command=self.index_all_domains).pack(side="right", padx=2)
        
        self.shuffle_btn = tb.Button(archive_frame, text="Shuffle", command=self.shuffle_current_domain)
        self.shuffle_btn.pack(side="left", padx=4)
        ToolTip(self.shuffle_btn, "Shuffle random files from current domain into the Shuffle tab")

        self.options_dropdown = tk.Menu(menubar, tearoff=0)
        self.options_dropdown.add_checkbutton(
            label="Keep Download Window Open After Completion",
            variable=self.keep_download_window_open,
            command=self.save_settings
        )
        
        self.stream_random_animation_enabled = tk.BooleanVar(value=True)
        self.options_dropdown.add_checkbutton(
            label="Stream Random Button Animation", 
            variable=self.stream_random_animation_enabled, 
            command=self.on_toggle_animation_setting
        )

        self.options_dropdown.add_command(
            label="Choose Random Stream Domains...", 
            command=self.open_choose_random_domains_window
        )

        self.options_dropdown.add_command(label="Set Default Load Music Folder...", command=self.set_default_load_music_dir)
        self.options_dropdown.add_command(label="Download Premade Indexes (may be outdated!)", command=self.download_premade_indexes_prompt)
        
        self.options_dropdown.add_command(
            label="Install Dependencies", 
            command=lambda: install_dependencies_prompt(self)
        )

        # Shuffle Count sub-menu & dialog
        self.shuffle_count_menu = tk.Menu(self.options_dropdown, tearoff=0)
        for count_val in (10, 25, 50, 100, 200, 500):
            self.shuffle_count_menu.add_radiobutton(
                label=f"{count_val} files",
                variable=self.shuffle_count,
                value=count_val,
                command=self.save_settings
            )
        self.shuffle_count_menu.add_separator()
        self.shuffle_count_menu.add_command(
            label="Custom Selection...",
            command=self.open_shuffle_count_dialog
        )
        self.options_dropdown.add_cascade(label="Shuffle Amount", menu=self.shuffle_count_menu)
        
        # Themes sub-menu
        self.theme_var = tk.StringVar(value=self.style.theme_use())
        self.theme_menu = tk.Menu(self.options_dropdown, tearoff=0, name="theme_menu")
        for t_name in sorted(self.style.theme_names()):
            self.theme_menu.add_radiobutton(
                label=t_name,
                variable=self.theme_var,
                value=t_name,
                command=lambda tn=t_name: self.change_theme(tn)
            )
        # Live preview while hovering; revert to the saved theme if nothing is chosen
        # Bind at the Tcl level so the handler gets the raw clone path (%W)
        menu_select_cmd = self.register(self._on_theme_menu_select)
        self.tk.call("bind", "Menu", "<<MenuSelect>>", f"+{menu_select_cmd} %W")
        self.bind_class("Menu", "<Unmap>", self._on_theme_menu_unmap, add="+")
        self.options_dropdown.add_cascade(label="Themes", menu=self.theme_menu)

        # Font Size sub-menu
        self.font_menu = tk.Menu(self.options_dropdown, tearoff=0)
        for size in (8, 9, 10, 11, 12, 14, 16):
            self.font_menu.add_radiobutton(
                label=f"{size} pt",
                variable=self.font_size,
                value=size,
                command=lambda s=size: self.change_font_size(s)
            )
        self.options_dropdown.add_cascade(label="Font Size", menu=self.font_menu)

        # Dependencies sub-menu
        self.deps_menu = tk.Menu(self.options_dropdown, tearoff=0)
        self.options_dropdown.add_cascade(label="Dependencies", menu=self.deps_menu)
        
        self.after(300, self.update_dependencies_submenu)

        menubar.add_cascade(label="Options", menu=self.options_dropdown)

        class DummyProgressBar:
            def __setitem__(self, key, value):
                pass
            def configure(self, **kwargs):
                pass
            def get(self):
                return 0

        self.progress_bar = DummyProgressBar()

        path_frame = tb.Frame(self, padding=(8, 0, 8, 8))
        path_frame.pack(fill="x")

        tb.Button(path_frame, text="<", width=3, command=lambda: self.navigate_history(-1)).pack(side="left", padx=(0, 2))
        tb.Button(path_frame, text=">", width=3, command=lambda: self.navigate_history(1)).pack(side="left", padx=(0, 6))
        tb.Label(path_frame, text="Remote path:").pack(side="left")
        self.path_label = tb.Label(path_frame, text="", anchor="w")
        self.path_label.pack(side="left", fill="x", expand=True, padx=6)
        tb.Button(path_frame, text="Up", command=self.go_up).pack(side="right")

        info_search_frame = tb.Frame(self, padding=(8, 0, 8, 2))
        info_search_frame.pack(fill="x", padx=2)

        tb.Label(info_search_frame, text="Double click or hit Enter on a file to stream or download it. Right click to favorite or unfavorite!", font=("Arial", 9, "italic")).pack(side="left")

        clear_search_btn = tb.Button(info_search_frame, text="Clear", command=self.clear_search_history)
        clear_search_btn.pack(side="right", padx=2)
        ToolTip(clear_search_btn, "Click to clear search history")

        self.search_combo = tb.Combobox(info_search_frame, textvariable=self.search_text, width=18, values=self.load_search_history())
        self.search_combo.pack(side="right", padx=4)
        self.search_combo.bind("<KeyRelease>", lambda event: self.apply_filter())
        self.search_combo.bind("<<ComboboxSelected>>", lambda event: self.on_search_selected())
        self.search_combo.bind("<Return>", lambda event: self.on_search_submitted())
        tb.Checkbutton(
            info_search_frame,
            text="Search all indexes",
            variable=self.search_all_indexes_var,
            command=self.on_search_scope_changed
        ).pack(side="right", padx=4)

        tb.Label(info_search_frame, text="Search:").pack(side="right", padx=(4, 2))

        tree_container = tb.Frame(self, padding=(8, 0, 8, 8))
        tree_container.pack(fill="both", expand=True)

        self.notebook = tb.Notebook(tree_container)
        self.notebook.pack(fill="both", expand=True)

        browse_frame = tb.Frame(self.notebook)
        self.notebook.add(browse_frame, text="Browse")

        self.tree = tb.Treeview(browse_frame, columns=("favorite", "type", "name", "date", "size", "domain", "url"), show="headings", selectmode="extended")
        self.tree.heading("favorite", text="★")
        self.tree.heading("type", text="Type", command=lambda: self.sort_by_column("type"))
        self.tree.heading("name", text="Name ▲", command=lambda: self.sort_by_column("name"))
        self.tree.heading("date", text="Date", command=lambda: self.sort_by_column("date"))
        self.tree.heading("size", text="Size", command=lambda: self.sort_by_column("size"))
        self.tree.heading("domain", text="Domain", command=lambda: self.sort_by_column("domain"))
        self.tree.heading("url", text="URL", command=lambda: self.sort_by_column("url"))

        self.tree.column("favorite", width=40, anchor="center", stretch=False)
        self.tree.column("type", width=90, anchor="center", stretch=False)
        self.tree.column("name", width=280)
        self.tree.column("date", width=110, anchor="center", stretch=False)
        self.tree.column("size", width=100, anchor="center", stretch=False)
        self.tree.column("domain", width=90, anchor="center", stretch=False)
        self.tree.column("url", width=520)
        self.tree["displaycolumns"] = ("favorite", "type", "name", "date", "size", "url")

        self._setup_tree_scrolling_and_sizing(browse_frame, self.tree)

        self.tree.bind("<Double-1>", self.double_click_entry)
        self.tree.bind("<Return>", self.double_click_entry)
        self.tree.bind("<Button-3>", self.on_browse_right_click)

        favorites_frame = tb.Frame(self.notebook)
        self.notebook.add(favorites_frame, text="Favorites")

        self.fav_tree = tb.Treeview(favorites_frame, columns=("favorite", "type", "name", "date", "size", "domain", "url"), show="headings", selectmode="extended")
        self.fav_tree.heading("favorite", text="★")
        self.fav_tree.heading("type", text="Type", command=lambda: self.sort_favorites_by_column("type"))
        self.fav_tree.heading("name", text="Name ▲", command=lambda: self.sort_favorites_by_column("name"))
        self.fav_tree.heading("date", text="Date", command=lambda: self.sort_favorites_by_column("date"))
        self.fav_tree.heading("size", text="Size", command=lambda: self.sort_favorites_by_column("size"))
        self.fav_tree.heading("domain", text="Domain", command=lambda: self.sort_favorites_by_column("domain"))
        self.fav_tree.heading("url", text="URL", command=lambda: self.sort_favorites_by_column("url"))

        self.fav_tree.column("favorite", width=40, anchor="center", stretch=False)
        self.fav_tree.column("type", width=90, anchor="center", stretch=False)
        self.fav_tree.column("name", width=280)
        self.fav_tree.column("date", width=110, anchor="center", stretch=False)
        self.fav_tree.column("size", width=100, anchor="e", stretch=False)
        self.fav_tree.column("domain", width=90, anchor="center", stretch=False)
        self.fav_tree.column("url", width=520)
        self.fav_tree["displaycolumns"] = ("favorite", "type", "name", "date", "size", "domain", "url")

        self._setup_tree_scrolling_and_sizing(favorites_frame, self.fav_tree)

        self.fav_tree.bind("<Double-1>", self.double_click_favorite)
        self.fav_tree.bind("<Return>", self.double_click_favorite)
        self.fav_tree.bind("<Button-3>", self.on_fav_right_click)

        self.shuffle_frame = tb.Frame(self.notebook)
        self.notebook.add(self.shuffle_frame, text="Shuffle")

        self.shuffle_tree = tb.Treeview(self.shuffle_frame, columns=("favorite", "type", "name", "date", "size", "domain", "url"), show="headings", selectmode="extended")
        self.shuffle_tree.heading("favorite", text="★")
        self.shuffle_tree.heading("type", text="Type", command=lambda: self.sort_shuffle_by_column("type"))
        self.shuffle_tree.heading("name", text="Name ▲", command=lambda: self.sort_shuffle_by_column("name"))
        self.shuffle_tree.heading("date", text="Date", command=lambda: self.sort_shuffle_by_column("date"))
        self.shuffle_tree.heading("size", text="Size", command=lambda: self.sort_shuffle_by_column("size"))
        self.shuffle_tree.heading("domain", text="Domain", command=lambda: self.sort_shuffle_by_column("domain"))
        self.shuffle_tree.heading("url", text="URL", command=lambda: self.sort_shuffle_by_column("url"))

        self.shuffle_tree.column("favorite", width=40, anchor="center", stretch=False)
        self.shuffle_tree.column("type", width=90, anchor="center", stretch=False)
        self.shuffle_tree.column("name", width=280)
        self.shuffle_tree.column("date", width=110, anchor="center", stretch=False)
        self.shuffle_tree.column("size", width=100, anchor="e", stretch=False)
        self.shuffle_tree.column("domain", width=90, anchor="center", stretch=False)
        self.shuffle_tree.column("url", width=520)
        self.shuffle_tree["displaycolumns"] = ("favorite", "type", "name", "date", "size", "domain", "url")

        self._setup_tree_scrolling_and_sizing(self.shuffle_frame, self.shuffle_tree)

        self.shuffle_tree.bind("<Double-1>", self.double_click_shuffle)
        self.shuffle_tree.bind("<Return>", self.double_click_shuffle)
        self.shuffle_tree.bind("<Button-3>", self.on_shuffle_right_click)

        destination_frame = tb.Frame(self, padding=8)
        destination_frame.pack(fill="x")
        tb.Checkbutton(destination_frame, text="Set Default Download Location:", variable=self.use_default_dest, command=self.save_settings).pack(side="left")
        
        self.destination_combo = tb.Combobox(destination_frame, textvariable=self.destination, values=self.load_destination_history())
        self.destination_combo.pack(side="left", fill="x", expand=True, padx=6)
        tb.Button(destination_frame, text="Browse...", command=self.choose_destination).pack(side="left")
        tb.Button(destination_frame, text="Clear Dest History", command=self.clear_destination_history).pack(side="left", padx=(4, 0))

        self.button_frame = tb.Frame(self, padding=(8, 0, 8, 8))
        self.button_frame.pack(fill="x")
        tb.Button(self.button_frame, text="Download Selected File(s)", command=self.download_selected).pack(side="left")
        tb.Button(self.button_frame, text="Download Every File Here", command=self.download_current_directory).pack(side="left", padx=6)
        tb.Button(self.button_frame, text="Stop", command=self.stop_operations).pack(side="left", padx=6)

        self.stream_random_all_indexes_chk = tb.Checkbutton(
            self.button_frame,
            text="in all indexes!",
            variable=self.stream_random_all_indexes_var,
            command=lambda: self.update_stream_random_state(any(not e.get("directory") for e in (self.domain_full_entries if self.domain_full_entries else self.current_entries)))
        )
        self.stream_random_all_indexes_chk.pack(side="left", padx=5)

        self.stream_random_anim_id = None
        self.fade_colors = [
            "#38bdf8", "#818cf8", "#c084fc", "#f472b6", 
            "#fb7185", "#34d399", "#fbbf24", "#60a5fa", 
            "#a78bfa", "#4ade80"
        ]
        self.fade_index = 0

        self.rebuild_stream_random_button()
        self.fade_index = 0

        initial_theme_lower = self.saved_theme.lower()
        initial_hover_color = "#2a3b4c" if "dark" in initial_theme_lower else "#e8f0fe"
        
        self.tree.tag_configure("hover", background=initial_hover_color)
        self.fav_tree.tag_configure("hover", background=initial_hover_color)
        self.shuffle_tree.tag_configure("hover", background=initial_hover_color)
        
        self.tree.bind("<Motion>", self.on_tree_motion)
        self.tree.bind("<Leave>", self.on_tree_leave)
        self.fav_tree.bind("<Motion>", self.on_fav_motion)
        self.fav_tree.bind("<Leave>", self.on_fav_leave)
        self.shuffle_tree.bind("<Motion>", self.on_shuffle_motion)
        self.shuffle_tree.bind("<Leave>", self.on_shuffle_leave)

        tb.Label(self.button_frame, textvariable=self.status_text).pack(side="right")

    def load_local_music(self, is_default=False):
        initial_dir = self.default_load_music_dir.get() if is_default else self._last_loaded_music_dir
        file_path = filedialog.askopenfilename(
            title="Select Music File",
            initialdir=initial_dir,
            filetypes=[("All Files", "*.*")]
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
        help_win.geometry("900x580")
        help_win.transient(self)
        
        frame = tb.Frame(help_win, padding=12)
        frame.pack(fill="both", expand=True)

        text_widget = tb.Text(frame, wrap="word", font=("Arial", 15))
        text_widget.pack(side="left", fill="both", expand=True)

        scrollbar = tb.Scrollbar(frame, orient="vertical", command=text_widget.yview)
        scrollbar.pack(side="right", fill="y")
        text_widget.configure(yscrollcommand=scrollbar.set)

        temp_path_display = "Documents\\Joshw\\temp_extracted" if sys.platform == "win32" else "~/.config/joshw/temp_extracted"

        help_text = (
    "Welcome to the JoshW emulated music browser & player!\n\n"
    "========================================================================\n"
    "                         QUICK START & WORKFLOW                         \n"
    "========================================================================\n"
    "1. Download Premade Indexes (Recommended First Step):\n"
    "   Open the Options menu and click 'Download Premade Indexes'. Once a domain is\n"
    "   indexed, it will load instantly from local data rather than fetching from\n"
    "   the server every time.\n\n"
    "2. Browsing & Loading Domains:\n"
    "   Use the domain dropdown menu to choose a platform archive. Select a domain\n"
    "   and click 'Open' to display its directory listing.\n\n"
    "3. Playback & Downloads:\n"
    "   • Stream / Download Single: Double-click or select a track and press Enter.\n"
    "   • Stream Random!: Click this button to pick and stream a random track automatically\n"
    "     from the currently active domain index (or across all indexed domains if\n"
    "     'Search All Indexes' is enabled).\n"
    "   • Multi-Select: Hold Shift while clicking to highlight multiple files to download.\n"
    "   • Folder Download: Click 'Download Every File Here' to grab all files in the active view.\n\n"
    "4. Search System:\n"
    "   • Single Domain: Type in the search bar to filter the current list instantly.\n"
    "   • Search History: Press Enter on a search query to save it to the history dropdown.\n"
    "   • Search All Indexes: Check 'Search All Indexes' next to the search bar to scan\n"
    "     across every indexed domain simultaneously (may cause slight lag on large indexes).\n\n"
    "========================================================================\n"
    "                         OPTIONS MENU OVERVIEW                          \n"
    "========================================================================\n"
    "• Download Premade Indexes:\n"
    "  Fetches and updates local directory indexes for instant loading across domains.\n\n"
    "• Install / View Dependencies:\n"
    "  Opens a menu to check, install, or view required decoders and helper tools\n"
    "  (e.g., VGMstream, lazyusf, FFmpeg/libgme, 7-Zip) needed for playback & extraction.\n\n"
    "• Default Download Location & Checkbox:\n"
    "  Set a default output folder for downloaded files. Toggle the checkbox to the left\n"
    "  of the text field to automatically save downloads there without prompting.\n"
    "  If left unchecked, you will be prompted for a save location on every download.\n\n"
    "• Keep Download Window Open:\n"
    "  Controls whether the download status window stays open after transfers finish.\n\n"
    "• Theme Settings:\n"
    "  Customizes the look and feel of the user interface.\n\n"
    "========================================================================\n"
    "                     SUPPORTED DOMAINS & PLATFORMS                      \n"
    "========================================================================\n"
    "3DO COMPANY:\n"
    "  • 3DO (.vag, .adpcm)\n\n"
    "BANDAI:\n"
    "  • WonderSwan (.wsr)\n\n"
    "COMMODORE:\n"
    "  • c64 music archives (.sid)\n\n"
    "FUJITSU:\n"
    "  • FM Towns music archive (.7z)\n\n"
    "MICROSOFT:\n"
    "  • Xbox (.xma, .xmd)\n"
    "  • Xbox 360 (.xma, .xmd)\n\n"
    "NEC:\n"
    "  • PC Engine (.hes)\n\n"
    "NINTENDO:\n"
    "  • Game Boy (.gbs)\n"
    "  • Game Boy Advance (.gsf, .minigsf)\n"
    "  • GameCube (.ast, .dsp, .brstm)\n"
    "  • NES music archives (.nsf, .nsfe)\n"
    "  • Nintendo 3DS (.3sf)\n"
    "  • Nintendo 64 (.usf, .miniusf)\n"
    "  • Nintendo DS (.2sf, .mini2sf)\n"
    "  • Nintendo Wii (.brstm, .rstm, .bfsar)\n"
    "  • Nintendo Wii U (.bfstm, .bfwav, .fsar)\n"
    "  • SNES music archives (.spc)\n"
    "  • Switch (.bfstm, .bcstm, .bfsar, .bfsars, .bfwav, .bfseq, .bfbnk, .wem, .hca, .acb, .awb, .g1l)\n\n"
    "SEGA:\n"
    "  • Sega CD / Mega Drive (.vgm, .vgz, .gym)\n"
    "  • Saturn (.ssf)\n"
    "  • Dreamcast (.dsf)\n\n"
    "SNK:\n"
    "  • Neo Geo (.ncd / .kss)\n\n"
    "SONY:\n"
    "  • PlayStation (.psf, .minipsf)\n"
    "  • PlayStation 2 (.psf2, .minipsf2)\n"
    "  • PlayStation 3 (.psf3)\n"
    "  • Playstation 4 (.psf4)\n"
    "  • Playstation 5 (.psf5)\n"
    "  • PlayStation Portable (.vag, .at3)\n"
    "  • PlayStation Vita (.vag, .at3)\n\n"
    "OTHER & ASSORTED:\n"
    "  • Arcade (VGMRips)\n"
    "  • Hoot music archives\n"
    "  • MSX music archives (.kss)\n"
    "  • Other Assorted Systems (.cdi)\n"
    "  • PC music archives (.7z, Various Rips)\n"
    "  • S98 music archives (.s98)\n"
    "  • VGMusic Full (.mid)\n\n"
    "========================================================================\n"
    "                             TEMPORARY CACHE                            \n"
    "========================================================================\n"
    f"All streamed songs are saved in a temporary folder within {temp_path_display}\n"
    "and will be deleted once the program is closed.\n\n"
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

    def sort_favorites_by_column(self, col_key):
        if not hasattr(self, "fav_sort_column"):
            self.fav_sort_column = "name"
            self.fav_sort_ascending = True

        if self.fav_sort_column == col_key:
            self.fav_sort_ascending = not self.fav_sort_ascending
        else:
            self.fav_sort_column = col_key
            self.fav_sort_ascending = True
            
        self.update_favorites_sort_headers()
        self.refresh_favorites_tab()

    def update_favorites_sort_headers(self):
        base_titles = {"type": "Type", "name": "Name", "date": "Date", "size": "Size", "domain": "Domain", "url": "URL"}
        current_col = getattr(self, "fav_sort_column", "name")
        current_asc = getattr(self, "fav_sort_ascending", True)
        
        for k in base_titles:
            arrow = ""
            if k == current_col:
                arrow = " ▲" if current_asc else " ▼"
            self.fav_tree.heading(k, text=base_titles[k] + arrow)

    def sort_shuffle_by_column(self, col_key):
        if not hasattr(self, "shuffle_sort_column"):
            self.shuffle_sort_column = "name"
            self.shuffle_sort_ascending = True

        if self.shuffle_sort_column == col_key:
            self.shuffle_sort_ascending = not self.shuffle_sort_ascending
        else:
            self.shuffle_sort_column = col_key
            self.shuffle_sort_ascending = True

        self.update_shuffle_sort_headers()
        self.populate_shuffle_tree(getattr(self, "shuffle_entries", []), resort=True)

    def update_shuffle_sort_headers(self):
        base_titles = {"type": "Type", "name": "Name", "date": "Date", "size": "Size", "domain": "Domain", "url": "URL"}
        current_col = getattr(self, "shuffle_sort_column", "name")
        current_asc = getattr(self, "shuffle_sort_ascending", True)

        for k in base_titles:
            arrow = ""
            if k == current_col:
                arrow = " ▲" if current_asc else " ▼"
            self.shuffle_tree.heading(k, text=base_titles[k] + arrow)

    def update_shuffle_stars(self):
        if not hasattr(self, "shuffle_tree") or not hasattr(self, "shuffle_entries"):
            return
        favorites = self.load_favorites()
        fav_urls = {f.get("url") for f in favorites if f.get("url")}
        for index, entry in enumerate(self.shuffle_entries):
            item_id = str(index)
            if self.shuffle_tree.exists(item_id):
                is_fav = entry.get("url") in fav_urls
                star_symbol = "★" if is_fav else ""
                self.shuffle_tree.set(item_id, "favorite", star_symbol)

    def populate_shuffle_tree(self, entries, resort=False):
        if not hasattr(self, "shuffle_tree"):
            return
        self.shuffle_tree.delete(*self.shuffle_tree.get_children())

        if resort and entries:
            orig_col = self.current_sort_column
            orig_asc = self.current_sort_ascending
            self.current_sort_column = getattr(self, "shuffle_sort_column", "name")
            self.current_sort_ascending = getattr(self, "shuffle_sort_ascending", True)
            sorted_entries = self.apply_sorting(entries)
            self.current_sort_column = orig_col
            self.current_sort_ascending = orig_asc
            self.shuffle_entries = sorted_entries
        else:
            self.shuffle_entries = list(entries)

        favorites = self.load_favorites()
        fav_urls = {f.get("url") for f in favorites if f.get("url")}

        for index, entry in enumerate(self.shuffle_entries):
            item_id = str(index)
            is_fav = entry.get("url") in fav_urls
            star_symbol = "★" if is_fav else ""
            item_size = "-" if entry.get("directory") else format_size(entry.get("size"))
            entry_domain = entry.get("domain") or self.domain_combo.get()

            self.shuffle_tree.insert("", "end", iid=item_id, values=(
                star_symbol,
                "File",
                entry.get("name", ""),
                entry.get("date", ""),
                item_size,
                entry_domain,
                urllib.parse.unquote(entry.get("url", ""))
            ))

        autofit_treeview_columns(self.shuffle_tree)

    def shuffle_current_domain(self):
        selected_name = self.domain_combo.get()
        selected_url = DOMAINS.get(selected_name, "")
        if not selected_url:
            return

        cache_path = get_cache_path(selected_name)
        if not self.domain_full_entries and os.path.exists(cache_path):
            self.load_or_check_cache(selected_name, selected_url)

        source_pool = self.domain_full_entries if self.domain_full_entries else self.current_entries
        files = [e for e in source_pool if not e.get("directory") and not e.get("is_parent_nav")]

        if not files:
            messagebox.showinfo("Shuffle", f"No files available in '{selected_name}'.\nPlease open or index this domain first.")
            return

        count = self.shuffle_count.get() if hasattr(self, "shuffle_count") else 50
        count = max(1, count)
        import random
        picked = random.sample(files, min(len(files), count))

        for e in picked:
            if not e.get("domain"):
                e["domain"] = selected_name

        self.populate_shuffle_tree(picked)
        if hasattr(self, "notebook") and hasattr(self, "shuffle_frame"):
            self.notebook.select(self.shuffle_frame)

    def double_click_shuffle(self, event=None):
        selection = self.shuffle_tree.selection()
        if not selection:
            return
        item_id = selection[0]
        try:
            entry = self.shuffle_entries[int(item_id)]
        except (IndexError, ValueError, AttributeError):
            return
        if not entry:
            return

        temp_dir = get_temp_directory()
        check_dir = temp_dir if os.path.exists(temp_dir) else os.path.dirname(temp_dir)
        hdd_space = format_size(shutil.disk_usage(check_dir).free)

        try:
            dialog = tb.Toplevel(self)
            dialog.title("File Action")
            dialog.geometry("450x140")
            dialog.resizable(False, False)
            dialog.transient(self)
            try:
                dialog.update_idletasks()
                dialog.grab_set()
            except Exception:
                pass

            filename = entry["name"]
            tb.Label(dialog, text=f"Choose action for: {filename}\n\n You have {hdd_space} left in your temp directory.", font=("Arial", 9, "bold"), justify="center", wraplength=310).pack(pady=(15, 10))
            btn_frame = tb.Frame(dialog)
            btn_frame.pack(pady=5)

            url = entry["url"]
            stream_btn = tb.Button(btn_frame, text="Stream", width=12, command=lambda: [dialog.destroy(), self.stream_specific_url(url)])
            stream_btn.pack(side="left", padx=8)
            download_btn = tb.Button(btn_frame, text="Download", width=12, command=lambda: [dialog.destroy(), self.download_specific_url(url)])
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
        except Exception:
            pass

    def on_shuffle_right_click(self, event):
        item_id = self.shuffle_tree.identify_row(event.y)
        if item_id:
            try:
                entry = self.shuffle_entries[int(item_id)]
                if not entry.get("directory") and not entry.get("is_parent_nav"):
                    self.toggle_favorite_by_entry(entry)
            except Exception:
                pass

    def open_shuffle_count_dialog(self):
        dialog = tb.Toplevel(self)
        dialog.title("Set Shuffle Amount")
        dialog.geometry("340x160")
        dialog.resizable(False, False)
        dialog.transient(self)
        try:
            dialog.grab_set()
        except Exception:
            pass

        tb.Label(
            dialog,
            text="Choose number of files to shuffle:",
            font=("Arial", 10, "bold")
        ).pack(pady=(18, 8), padx=15, anchor="w")

        combo_values = ["10", "25", "50", "75", "100", "150", "200", "250", "500", "1000"]
        combo = tb.Combobox(dialog, values=combo_values)
        combo.pack(pady=4, padx=15, fill="x")
        combo.set(str(self.shuffle_count.get()))
        combo.focus_set()

        def on_confirm(event=None):
            val_str = combo.get().strip()
            try:
                val = int(val_str)
                if val <= 0:
                    raise ValueError
                self.shuffle_count.set(val)
                self.save_settings()
                dialog.destroy()
            except ValueError:
                messagebox.showerror("Invalid Input", "Please enter a valid positive integer.")

        combo.bind("<Return>", on_confirm)

        btn_frame = tb.Frame(dialog)
        btn_frame.pack(pady=(12, 10), fill="x", padx=15)
        tb.Button(btn_frame, text="Cancel", command=dialog.destroy).pack(side="right", padx=(4, 0))
        tb.Button(btn_frame, text="OK", bootstyle="primary", command=on_confirm).pack(side="right")

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

    def play_audio_file(self, audio_file, playlist=None, start_index=None):
        """Check the player's dependency, then launch it as a standalone process."""
        script = player_script_for(audio_file)

        if script == "arcade_player.py" and not self.check_dependency_status("vgmplay"):
            messagebox.showerror("vgmplay Not Found", "vgmplay binary not found!")
            return
        if script == "usf_player.py" and not self.check_dependency_status("lazyusf"):
            messagebox.showerror("Lazyusf Not Found", "Lazyusf not found! Please install dependencies.")
            return
        if script == "gsf_player.py" and not self.check_dependency_status("playgsf"):
            messagebox.showerror("playgsf Not Found", "playgsf not found! Please install dependencies.")
            return
        if script == "twosf_player.py" and not self.check_dependency_status("2sfplay"):
            messagebox.showerror("2sfPlay Not Found", "2sfPlay not found! Please install dependencies.")
            return
        if script == "psf_player.py" and not self.check_dependency_status("psfplay"):
            messagebox.showerror(
                "AOSDK Library Not Found",
                "libaosdk.so/DLL not found! Please ensure the playback library is built."
            )
            return
        if script == "vgmstream_player.py":
            vgm_bin = get_vgmstream_path()
            if not vgm_bin or (not shutil.which(vgm_bin) and not os.path.exists(vgm_bin)):
                messagebox.showerror("vgmstream123 Not Found", "vgmstream123 not found!")
                return
        if script == "mpv_player.py":
            mpv_path = self.path_mpv.get().strip() or "mpv"
            if not (shutil.which(mpv_path) or os.path.exists(mpv_path)):
                messagebox.showerror("mpv Not Found", "mpv not found!")
                return

        if script == "gme_player.py":
            # GME builds its own subtrack playlist from a generated .m3u
            from gme_player import generate_gme_m3u_if_needed
            launch_player(generate_gme_m3u_if_needed(audio_file), script=script)
            return

        try:
            launch_player(audio_file, playlist=playlist, start_index=start_index, script=script)
        except Exception as e:
            messagebox.showerror("Player Error", f"Could not start player:\n\n{e}")

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
            "default_load_music_dir": self.default_load_music_dir.get(),
            "excluded_stream_domains": getattr(self, "excluded_stream_domains", []),
            "keep_download_window_open": self.keep_download_window_open.get(),
            "font_size": self.font_size.get(),
            "shuffle_count": self.shuffle_count.get()
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
        selected_url = DOMAINS.get(selected_name, "")
        
        # Do nothing if a category header or empty selection was picked
        if not selected_url:
            return

        self.search_text.set("")
        self.load_or_check_cache(selected_name, selected_url)
        self.nav_history = []
        self.nav_history_index = -1
        self.load_directory(selected_name, selected_url, is_root=True)

    def index_current_domain(self):
        selected_name = self.domain_combo.get()
        selected_url = DOMAINS.get(selected_name, "")
        
        # Do nothing if a category header or empty selection was picked
        if not selected_url:
            return

        if selected_name in EXCLUDED_FROM_INDEX_ALL:
            self.indexer_status_text.set("Indexing not supported for this domain")
            return

        self.start_domain_background_scraper(selected_name, selected_url, get_cache_path(selected_name))

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
        # Keep keys this method doesn't own (e.g. the updater's dir_mtimes)
        data = {}
        if os.path.exists(cache_path):
            try:
                with open(cache_path, "r", encoding="utf-8") as f:
                    existing = json.load(f)
                if isinstance(existing, dict):
                    data = existing
            except Exception:
                pass
        data.update({
            "entries": self.domain_full_entries,
            "dir_sizes": self.cached_dir_sizes,
            "scanned_dirs": list(getattr(self, "cached_scanned_dirs", set()))
        })
        try:
            with open(cache_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    
    def index_all_domains(self):
        IndexAllWindow(self, DOMAINS)

    def start_domain_background_scraper(self, domain_name, root_url, cache_path):
        self.stop_event.clear()
        self.progress_bar["value"] = 0
        self.indexer_status_text.set(f"Checking {domain_name} for new additions...")
        threading.Thread(target=self.domain_scraper_worker, args=(domain_name, root_url), daemon=True).start()

    def domain_scraper_worker(self, domain_name, root_url):
        """Incrementally update one domain's index (same updater as Index All)."""
        def progress_cb(listed, queued):
            self.messages.put(("index_progress", domain_name, listed, queued))
        try:
            status, added = update_domain_index(domain_name, root_url, progress_cb, self.stop_event)
        except Exception as e:
            self.messages.put(("index_finished", domain_name, "error", str(e)))
            return
        self.messages.put(("index_finished", domain_name, status, len(added)))

    def on_domain_index_finished(self, domain_name, status, detail):
        self.progress_bar["value"] = 100 if status != "error" else 0
        if status == "updated":
            text = f"{domain_name}: added {detail} new file(s) to the index."
            # Reload the index and refresh the view if this domain is open
            if self.domain_combo.get() == domain_name:
                self.load_or_check_cache(domain_name, DOMAINS[domain_name])
                if getattr(self, "current_url", None):
                    threading.Thread(target=self.load_directory_from_index_worker,
                                     args=(domain_name, self.current_url), daemon=True).start()
        elif status == "unchanged":
            text = f"{domain_name}: index is up to date, no new additions."
        elif status == "stopped":
            text = f"{domain_name}: indexing stopped."
        else:
            text = f"{domain_name}: indexing failed ({detail})."
        self.indexer_status_text.set(text)
        self.status_text.set(text)

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
            if domain_name == "Arcade":
                entries = []
                target_base = url.rstrip("/") + "/"
                is_root = (url.rstrip("/") == DOMAINS["Arcade"].rstrip("/"))
    
                for entry in self.domain_full_entries:
                    entry_url = entry.get("url", "").rstrip("/")
                    if entry_url == url.rstrip("/"):
                        continue
            
                    if is_root:
                        parsed_target = urllib.parse.urlparse(target_base)
                        parsed_entry = urllib.parse.urlparse(entry_url)
                        
                        rel_path = parsed_entry.path[len(parsed_target.path):].strip("/")
                        if rel_path and "/" not in rel_path:
                            if not any(e.get("url", "").rstrip("/") == entry_url for e in entries):
                                entries.append(entry)
                    else:
                        if entry_url.startswith(target_base):
                            rel_path = entry_url[len(target_base):].strip("/")
                            if rel_path:
                                if "/" not in rel_path:
                                    if not any(e.get("url", "").rstrip("/") == entry_url for e in entries):
                                        entries.append(entry)
                                else:
                                    sub_dir_name = rel_path.split("/")[0]
                                    sub_dir_url = target_base + sub_dir_name + "/"
                                    if not any(e.get("url", "").rstrip("/") == sub_dir_url.rstrip("/") for e in entries):
                                        entries.append({
                                            "name": sub_dir_name,
                                            "raw_name": sub_dir_name,
                                            "date": "",
                                            "url": sub_dir_url,
                                            "directory": True,
                                            "size": None
                                        })
                        
                        elif entry_url.startswith("https://vgmrips.net/files/"):
                            parsed_target = urllib.parse.urlparse(target_base)
                            folder_name = parsed_target.path.strip("/").split("/")[-1]
                            if folder_name and folder_name.lower() in entry_url.lower():
                                if not any(e.get("url", "").rstrip("/") == entry_url for e in entries):
                                    entries.append(entry)
                
                total_size = sum(e.get("size", 0) or 0 for e in entries if not e.get("directory"))
            else:
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
        temp_dir_free = format_size(shutil.disk_usage(get_temp_directory()).free)
        self.status_text.set(f"Temp directory: {temp_dir_free} | {len(self.visible_entries)} shown | Total DL Size: {format_size(folder_size)}")

        has_files = any(not e.get("directory") for e in (self.domain_full_entries if self.domain_full_entries else entries))
        self.update_stream_random_state(has_files)

    def load_all_index_entries(self):
        bundled_root = sys._MEIPASS if getattr(sys, "frozen", False) else os.path.dirname(os.path.abspath(__file__))
        index_directories = (
            os.path.join(bundled_root, "indexes"),
            os.path.join(get_cache_directory(), "indexes"),
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
        
        fav_urls = {f.get("url") for f in self.load_favorites()}

        if self.search_all_indexes_var.get():
            self.tree["displaycolumns"] = ("favorite", "type", "name", "date", "size", "domain", "url")
            source = self.all_index_entries
            filtered = [
                entry for entry in source
                if search
                and not entry.get("directory")
                and (search in entry["name"].lower() or search in entry["url"].lower() or search in entry.get("domain", "").lower())
            ]
        else:
            self.tree["displaycolumns"] = ("favorite", "type", "name", "date", "size", "url")
            source = self.domain_full_entries if (search and self.domain_full_entries) else self.current_entries
            filtered = [e for e in source if not search or search in e["name"].lower() or e.get("is_parent_nav")]
        self.visible_entries = self.apply_sorting(filtered)

        for index, entry in enumerate(self.visible_entries):
            item_id = str(index)
            is_fav = entry.get("url") in fav_urls
            star_symbol = "★" if (is_fav and not entry["directory"] and not entry.get("is_parent_nav")) else ""
            
            item_size = (
                "-"
                if entry["directory"]
                else format_size(entry.get("size"))
            )

            self.tree.insert("", "end", iid=item_id, values=(
                star_symbol,
                "Directory" if entry["directory"] else "File",
                entry["name"],
                entry.get("date", ""),
                item_size,
                entry.get("domain", ""),
                urllib.parse.unquote(entry["url"])
            ))

        autofit_treeview_columns(self.tree)

    def double_click_entry(self, event=None):
        temp_dir = get_temp_directory()
        check_dir = temp_dir if os.path.exists(temp_dir) else os.path.dirname(temp_dir)
        hdd_space = format_size(shutil.disk_usage(check_dir).free)
        
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

            dialog = tb.Toplevel(self)
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
            tb.Label(dialog, text=f"Choose action for: {filename}\n\n You have {hdd_space} left in your temp directory.", font=("Arial", 9, "bold"), justify="center", wraplength=310).pack(pady=(15, 10))
            btn_frame = tb.Frame(dialog)
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

        try:
            if os.path.exists(output_path):
                os.remove(output_path)
        except Exception as cleanup_error:
            print(f"Could not remove temporary archive file: {cleanup_error}")

        self.messages.put(("close_progress", progress_win))
        if extracted:
            self.messages.put(("open_file_browser", extract_dir))

    def open_file_browser(self, folder_path):
        ExtractedFilesBrowserWindow(self, folder_path, keep_open=self.keep_download_window_open.get())

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
        urls = []
        try:
            current_tab = self.notebook.tab(self.notebook.select(), "text")
        except Exception:
            current_tab = "Browse"

        if current_tab == "Shuffle" and hasattr(self, "shuffle_tree"):
            selection = self.shuffle_tree.selection()
            for item_id in selection:
                try:
                    entry = self.shuffle_entries[int(item_id)]
                    if not entry.get("directory") and not entry.get("is_parent_nav"):
                        urls.append(entry["url"])
                except Exception:
                    pass
            return urls
        elif current_tab == "Favorites" and hasattr(self, "fav_tree"):
            selection = self.fav_tree.selection()
            for item_id in selection:
                try:
                    entry = self.fav_display_mapping.get(item_id)
                    if entry and not entry.get("directory") and not entry.get("is_parent_nav"):
                        urls.append(entry["url"])
                except Exception:
                    pass
            return urls

        selection = self.tree.selection()
        for item_id in selection:
            try:
                entry = self.visible_entries[int(item_id)]
                if not entry.get("directory") and not entry.get("is_parent_nav"):
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
        try:
            current_tab = self.notebook.tab(self.notebook.select(), "text")
        except Exception:
            current_tab = "Browse"

        if current_tab == "Favorites":
            favs = self.load_favorites()
            entries = [e for e in favs if not e.get("directory") and not e.get("is_parent_nav")]
        elif current_tab == "Shuffle":
            entries = [e for e in getattr(self, "shuffle_entries", []) if not e.get("directory") and not e.get("is_parent_nav")]
        else:
            current_tab = "Browse"
            source = self.current_entries if self.current_entries else self.visible_entries
            entries = [e for e in source if not e.get("directory") and not e.get("is_parent_nav")]

        urls = [e["url"] for e in entries if e.get("url")]
        if not urls:
            messagebox.showinfo("No files", f"No files found in the {current_tab} tab to download.")
            return

        dest_dir = self.destination.get().strip() or os.path.expanduser("~/Downloads")
        check_dir = dest_dir if os.path.exists(dest_dir) else (os.path.dirname(dest_dir) if os.path.dirname(dest_dir) else os.path.expanduser("~"))
        try:
            free_bytes = shutil.disk_usage(check_dir).free
            free_space_str = format_size(free_bytes)
        except Exception:
            free_bytes = 0
            free_space_str = "Unknown"

        total_size = sum(e.get("size", 0) for e in entries if isinstance(e.get("size"), (int, float)))
        has_known_sizes = any(isinstance(e.get("size"), (int, float)) and e.get("size") > 0 for e in entries)
        total_size_str = format_size(total_size) if has_known_sizes else "Unknown size"

        # Open confirmation dialog window
        dialog = tb.Toplevel(self)
        dialog.title(f"Download Confirmation - {current_tab}")
        dialog.geometry("540x280")
        dialog.resizable(False, False)
        dialog.transient(self)
        try:
            dialog.grab_set()
        except Exception:
            pass

        content = tb.Frame(dialog, padding=16)
        content.pack(fill="both", expand=True)

        tb.Label(
            content,
            text=f"Download All Files from {current_tab}",
            font=("Arial", 11, "bold")
        ).pack(anchor="w", pady=(0, 10))

        info_frame = tb.Frame(content)
        info_frame.pack(fill="x", pady=2)

        tb.Label(info_frame, text=f"Total Files: {len(urls)}", font=("Arial", 9)).grid(row=0, column=0, sticky="w", pady=2)
        tb.Label(info_frame, text=f"Total File Size: {total_size_str}", font=("Arial", 9, "bold")).grid(row=1, column=0, sticky="w", pady=2)

        loc_frame = tb.Frame(info_frame)
        loc_frame.grid(row=2, column=0, sticky="ew", pady=2)
        loc_lbl = tb.Label(loc_frame, text=f"Default Download Location: {dest_dir}", font=("Arial", 9), wraplength=430, justify="left")
        loc_lbl.pack(side="left")

        space_lbl = tb.Label(info_frame, text=f"Space Available in Location: {free_space_str}", font=("Arial", 9, "bold"))
        space_lbl.grid(row=3, column=0, sticky="w", pady=2)

        def change_location():
            nonlocal dest_dir, free_bytes
            chosen = filedialog.askdirectory(title="Choose destination folder", initialdir=dest_dir)
            if chosen:
                dest_dir = chosen
                self.destination.set(chosen)
                self.save_destination_history(chosen)
                loc_lbl.configure(text=f"Default Download Location: {dest_dir}")
                try:
                    free_bytes = shutil.disk_usage(dest_dir).free
                    space_lbl.configure(text=f"Space Available in Location: {format_size(free_bytes)}")
                except Exception:
                    pass

        tb.Button(loc_frame, text="Change...", bootstyle="link", command=change_location).pack(side="left", padx=4)

        if has_known_sizes and free_bytes and free_bytes < total_size:
            tb.Label(
                content,
                text="⚠️ Warning: Available space may be less than total download size!",
                font=("Arial", 9, "bold"),
                foreground="#d9534f"
            ).pack(anchor="w", pady=(4, 2))

        tb.Label(
            content,
            text="Do you want to proceed with downloading all files?",
            font=("Arial", 9, "italic")
        ).pack(anchor="w", pady=(10, 8))

        btn_frame = tb.Frame(content)
        btn_frame.pack(fill="x", side="bottom")

        def on_proceed():
            dialog.destroy()
            if not os.path.isdir(dest_dir):
                try:
                    os.makedirs(dest_dir, exist_ok=True)
                except Exception as e:
                    messagebox.showerror("Error", f"Could not create destination folder:\n{e}")
                    return

            self.stop_event.clear()
            progress_win = DownloadProgressWindow(self, title=f"Downloading {current_tab} Files...")
            threading.Thread(
                target=self.download_files_worker,
                args=(urls, dest_dir, progress_win),
                daemon=True
            ).start()

        cancel_btn = tb.Button(btn_frame, text="Cancel", width=10, command=dialog.destroy)
        cancel_btn.pack(side="right", padx=(6, 0))

        proceed_btn = tb.Button(btn_frame, text="Proceed", bootstyle="primary", width=12, command=on_proceed)
        proceed_btn.pack(side="right")
        proceed_btn.focus_set()

        dialog.bind("<Return>", lambda e: on_proceed())
        dialog.bind("<Escape>", lambda e: dialog.destroy())

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
                elif msg[0] == "index_progress":
                    _, _, listed, queued = msg
                    self.progress_bar["value"] = min(99, listed / max(1, queued) * 100)
                elif msg[0] == "index_finished":
                    self.on_domain_index_finished(*msg[1:])
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