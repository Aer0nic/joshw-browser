import tkinter as tk
from tkinter import ttk


class ThemeTest(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Tkinter Theme Test")
        self.geometry("900x720")
        self.minsize(700, 550)

        self.style = ttk.Style(self)
        themes = self.style.theme_names()
        self.theme_var = tk.StringVar(value=self.style.theme_use())
        self.status_var = tk.StringVar(
            value="Choose a theme to preview it. Some themes may not support every style."
        )

        self._build_controls(themes)
        self._build_gallery()

    def _build_controls(self, themes):
        controls = ttk.LabelFrame(self, text="Theme controls", padding=10)
        controls.pack(fill="x", padx=12, pady=(12, 6))

        ttk.Label(controls, text="Theme:").grid(row=0, column=0, sticky="w")

        theme_box = ttk.Combobox(
            controls,
            textvariable=self.theme_var,
            values=themes,
            state="readonly",
            width=20,
        )
        theme_box.grid(row=0, column=1, sticky="ew", padx=(6, 14))
        theme_box.bind("<<ComboboxSelected>>", self._change_theme)

        ttk.Label(controls, text="Accent:").grid(row=0, column=2, sticky="w")

        self.accent_var = tk.StringVar(value="blue")
        accent_box = ttk.Combobox(
            controls,
            textvariable=self.accent_var,
            values=("blue", "green", "red", "purple", "orange"),
            state="readonly",
            width=10,
        )
        accent_box.grid(row=0, column=3, padx=6)
        accent_box.bind("<<ComboboxSelected>>", self._update_accent)

        ttk.Button(controls, text="Reset", command=self._reset).grid(
            row=0, column=4, padx=(8, 0)
        )

        controls.columnconfigure(1, weight=1)

    def _build_gallery(self):
        outer = ttk.Frame(self)
        outer.pack(fill="both", expand=True, padx=12, pady=6)

        self.canvas = tk.Canvas(outer, highlightthickness=0)
        scrollbar = ttk.Scrollbar(
            outer, orient="vertical", command=self.canvas.yview
        )
        self.gallery = ttk.Frame(self.canvas, padding=12)

        self.gallery.bind(
            "<Configure>",
            lambda _event: self.canvas.configure(
                scrollregion=self.canvas.bbox("all")
            ),
        )

        self.gallery_window = self.canvas.create_window(
            (0, 0), window=self.gallery, anchor="nw"
        )

        self.canvas.bind(
            "<Configure>",
            lambda event: self.canvas.itemconfigure(
                self.gallery_window, width=event.width
            ),
        )

        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self._add_section("Buttons")
        row = ttk.Frame(self.gallery)
        row.pack(fill="x", pady=4)

        ttk.Button(row, text="Normal button").pack(side="left", padx=4)
        ttk.Button(row, text="Disabled", state="disabled").pack(
            side="left", padx=4
        )
        ttk.Button(
            row, text="Accent button", style="Accent.TButton"
        ).pack(side="left", padx=4)

        self._add_section("Text and entry fields")
        ttk.Label(self.gallery, text="A regular label").pack(
            anchor="w", pady=3
        )
        ttk.Label(
            self.gallery, text="A heading label", style="Heading.TLabel"
        ).pack(anchor="w", pady=3)

        self.entry_var = tk.StringVar(value="Edit this text")
        ttk.Entry(self.gallery, textvariable=self.entry_var).pack(
            fill="x", pady=4
        )
        ttk.Entry(self.gallery, state="disabled").pack(fill="x", pady=4)

        self._add_section("Selection controls")
        self.check_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            self.gallery, text="Checkbutton", variable=self.check_var
        ).pack(anchor="w", pady=3)

        self.radio_var = tk.StringVar(value="Option 1")
        for value in ("Option 1", "Option 2"):
            ttk.Radiobutton(
                self.gallery,
                text=value,
                value=value,
                variable=self.radio_var,
            ).pack(anchor="w", pady=2)

        self.combo_var = tk.StringVar(value="Choice A")
        ttk.Combobox(
            self.gallery,
            textvariable=self.combo_var,
            values=("Choice A", "Choice B", "Choice C"),
            state="readonly",
        ).pack(fill="x", pady=5)

        self._add_section("Progress and scale")
        ttk.Progressbar(
            self.gallery,
            mode="determinate",
            maximum=100,
            value=65,
        ).pack(fill="x", pady=5)

        self.scale_var = tk.DoubleVar(value=40)
        ttk.Scale(
            self.gallery,
            from_=0,
            to=100,
            variable=self.scale_var,
            command=self._scale_changed,
        ).pack(fill="x", pady=5)

        self.scale_label = ttk.Label(self.gallery, text="Scale: 40")
        self.scale_label.pack(anchor="w")

        self._add_section("Lists and tables")
        list_frame = ttk.Frame(self.gallery)
        list_frame.pack(fill="x", pady=4)

        listbox = tk.Listbox(list_frame, height=4, exportselection=False)
        for item in ("List item one", "List item two", "List item three"):
            listbox.insert(tk.END, item)
        listbox.pack(side="left", fill="both", expand=True, padx=(0, 6))

        tree = ttk.Treeview(
            list_frame,
            columns=("value",),
            show="tree headings",
            height=4,
        )
        tree.heading("#0", text="Item")
        tree.heading("value", text="Value")
        tree.insert("", tk.END, text="First row", values=("123",))
        tree.insert("", tk.END, text="Second row", values=("456",))
        tree.pack(side="left", fill="both", expand=True)

        self._add_section("Tabs")
        notebook = ttk.Notebook(self.gallery)

        tab1 = ttk.Frame(notebook, padding=12)
        tab2 = ttk.Frame(notebook, padding=12)
        ttk.Label(tab1, text="Content in the first tab").pack(anchor="w")
        ttk.Label(tab2, text="Content in the second tab").pack(anchor="w")

        notebook.add(tab1, text="First tab")
        notebook.add(tab2, text="Second tab")
        notebook.pack(fill="x", pady=4)

        self._add_section("Other widgets")
        ttk.Separator(self.gallery).pack(fill="x", pady=6)
        ttk.Spinbox(self.gallery, from_=1, to=10, width=8).pack(
            anchor="w", pady=4
        )
        ttk.Label(self.gallery, textvariable=self.status_var).pack(
            anchor="w", pady=6
        )

        self._configure_custom_styles()

    def _add_section(self, title):
        ttk.Label(
            self.gallery,
            text=title,
            style="Section.TLabel",
        ).pack(anchor="w", pady=(14, 4))

    def _change_theme(self, _event=None):
        theme = self.theme_var.get()
        try:
            self.style.theme_use(theme)
        except tk.TclError as error:
            self.status_var.set(f"Could not apply theme: {error}")
            return

        self._configure_custom_styles()
        self.status_var.set(f"Current theme: {theme}")

    def _configure_custom_styles(self):
        accents = {
            "blue": "#2878d0",
            "green": "#238636",
            "red": "#d1242f",
            "purple": "#8250df",
            "orange": "#bc4c00",
        }
        accent = accents.get(self.accent_var.get(), "#2878d0")

        self.style.configure("Section.TLabel", font=("", 11, "bold"))
        self.style.configure("Heading.TLabel", font=("", 15, "bold"))
        self.style.configure(
            "Accent.TButton",
            foreground="white",
            background=accent,
            padding=(12, 6),
        )
        self.style.map(
            "Accent.TButton",
            background=[
                ("pressed", accent),
                ("active", accent),
            ],
            foreground=[("disabled", "#aaaaaa")],
        )

    def _update_accent(self, _event=None):
        self._configure_custom_styles()

    def _scale_changed(self, value):
        self.scale_label.configure(text=f"Scale: {int(float(value))}")

    def _reset(self):
        themes = self.style.theme_names()
        default_theme = "default" if "default" in themes else themes[0]

        self.theme_var.set(default_theme)
        self.style.theme_use(default_theme)
        self.accent_var.set("blue")
        self._configure_custom_styles()
        self.status_var.set(f"Reset. Current theme: {default_theme}")


if __name__ == "__main__":
    ThemeTest().mainloop()

