import os
import shutil
import subprocess
import time
from pathlib import Path
import threading
import tempfile
import urllib.request
import tkinter as tk
from tkinter import ttk, messagebox
from config import get_cache_directory, USER_AGENT


def check_and_download_sid_firmware():
    """Silently checks and downloads kernal and basic firmware files on startup if missing."""
    try:
        sidplayfp_dir = Path(os.path.expanduser("~/.local/share/sidplayfp"))
        sidplayfp_dir.mkdir(parents=True, exist_ok=True)

        kernal_file = sidplayfp_dir / "kernal"
        basic_file = sidplayfp_dir / "basic"

        if not kernal_file.exists():
            url_kernal = "https://www.zimmers.net/anonftp/pub/cbm/firmware/computers/c64/kernal.901227-03.bin"
            try:
                urllib.request.urlretrieve(url_kernal, str(kernal_file))
            except Exception:
                pass

        if not basic_file.exists():
            url_basic = "https://www.zimmers.net/anonftp/pub/cbm/firmware/computers/c64/basic.901226-01.bin"
            try:
                urllib.request.urlretrieve(url_basic, str(basic_file))
            except Exception:
                pass
    except Exception:
        pass


def install_dependencies_prompt(parent):
    prompt_text = (
        "The Josh Archive Browser requires some dependencies to run its built-in music player properly, "
        "please hit OK to start installation! This will open a terminal window and ask for your Sudo password. "
        "Once entered, it will run:\n\n"
        "sudo apt update\n"
        "sudo apt install git cmake libgme0 build-essential sidplayfp xterm xdotool -y\n\n"
        "It will then grab the source files for Lazyusf and VGMStream and build them, placing the files into the ~/.config/joshw folder. "
        "It will also grab my program nsf_tracks from my github, which helps the music player define the amount of tracks within a .nsf file.\n\n"
        "If you agree to this, hit the OK button to start, otherwise hit Cancel. You can start this process at any time from the Options button on the main menu!"
    )
    ans = messagebox.askokcancel("Install Dependencies", prompt_text)
    if ans:
        InstallDependenciesWorker(parent)


class InstallDependenciesWorker:
    def __init__(self, parent):
        self.parent = parent
        threading.Thread(target=self.run_installation, daemon=True).start()

    def run_installation(self):
        cache_dir = Path(get_cache_directory())
        cache_dir.mkdir(parents=True, exist_ok=True)
        sidplayfp_dir = Path(os.path.expanduser("~/.local/share/sidplayfp"))
        sidplayfp_dir.mkdir(parents=True, exist_ok=True)

        script_lines = [
            "sudo apt update",
            "sudo apt install git cmake libgme0 build-essential sidplayfp xterm xdotool -y"
        ]

        temp_build_dir = Path(tempfile.mkdtemp(prefix="joshw_dep_"))
        try:
            lazyusf_bin = cache_dir / "lazyusf"
            if not lazyusf_bin.exists():
                script_lines.append(f"git clone https://github.com/derselbst/lazyusf.git {temp_build_dir / 'lazyusf'}")
                script_lines.append(f"cd {temp_build_dir / 'lazyusf'} && mkdir build && cd build && cmake .. && make")
                script_lines.append(f"find {temp_build_dir / 'lazyusf'} -name 'lazyusf' -type f -exec cp {{}} {cache_dir} \\\\;")
                script_lines.append(f"chmod +x {lazyusf_bin}")

            vgmstream_bin = cache_dir / "vgmstream123"
            if not vgmstream_bin.exists():
                script_lines.append(f"git clone https://github.com/vgmstream/vgmstream.git {temp_build_dir / 'vgmstream'}")
                script_lines.append(f"cd {temp_build_dir / 'vgmstream'} && bash make-build-cmake.sh")
                script_lines.append(f"find {temp_build_dir / 'vgmstream'} -name 'vgmstream123' -type f -exec cp {{}} {cache_dir} \\\\;")
                script_lines.append(f"chmod +x {vgmstream_bin}")

            nsf_tracks_bin = cache_dir / "nsf_tracks"
            if not nsf_tracks_bin.exists():
                script_lines.append(f"wget -O {nsf_tracks_bin} https://raw.githubusercontent.com/Aer0nic/joshw-browser/main/tools/nsf_tracks || echo 'nsf_tracks fetch optional'")
                script_lines.append(f"chmod +x {nsf_tracks_bin} || true")

            kernal_file = sidplayfp_dir / "kernal"
            if not kernal_file.exists():
                script_lines.append(f"wget -O {kernal_file} https://www.zimmers.net/anonftp/pub/cbm/firmware/computers/c64/kernal.901227-03.bin")

            basic_file = sidplayfp_dir / "basic"
            if not basic_file.exists():
                script_lines.append(f"wget -O {basic_file} https://www.zimmers.net/anonftp/pub/cbm/firmware/computers/c64/basic.901226-01.bin")

            full_script = " && \\\n".join(script_lines)

            terminals = ["gnome-terminal", "konsole", "xfce4-terminal", "xterm"]
            launched = False
            for term in terminals:
                if shutil.which(term):
                    if term == "gnome-terminal":
                        subprocess.Popen([term, "--", "bash", "-c", f"{full_script}; echo 'Installation complete. Press Enter to close.'; read"])
                    elif term == "konsole":
                        subprocess.Popen([term, "-e", "bash", "-c", f"{full_script}; echo 'Installation complete. Press Enter to close.'; read"])
                    else:
                        subprocess.Popen([term, "-e", f"bash -c \"{full_script}; echo 'Installation complete. Press Enter to close.'; read\""])
                    launched = True
                    break
            if not launched:
                subprocess.Popen(["x-terminal-emulator", "-e", f"bash -c \"{full_script}; echo 'Installation complete. Press Enter to close.'; read\""])

            if self.parent.winfo_exists():
                self.parent.after(0, self.post_install_check)

        except Exception as e:
            if self.parent.winfo_exists():
                self.parent.after(0, lambda err=e: messagebox.showerror("Installation Error", f"An error occurred during dependency installation:\n\n{err}"))
        finally:
            shutil.rmtree(temp_build_dir, ignore_errors=True)

    def post_install_check(self):
        self.parent.update_dependencies_submenu()
        if (
            self.parent.check_dependency_status("lazyusf")
            and self.parent.check_dependency_status("vgmstream")
            and self.parent.check_dependency_status("sidplayfp")
        ):
            try:
                self.parent.options_dropdown.entryconfig(self.parent.install_deps_item_index, state="disabled")
            except Exception:
                pass
        messagebox.showinfo("Dependencies", "Dependency installation routine completed. Check the Dependencies submenu for visual confirmation.")


class StepLogDialog(tk.Toplevel):
    def __init__(self, parent, title, error_message, terminal_cmd):
        super().__init__(parent)
        self.title(title)
        self.geometry("540x320")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self.terminal_cmd = terminal_cmd

        ttk.Label(self, text="Premade Indexes Not Found", font=("Arial", 10, "bold"), foreground="red").pack(anchor="w", padx=12, pady=(12, 4))
        ttk.Label(self, text=error_message, font=("Arial", 9), wraplength=510, justify="left").pack(anchor="w", padx=12, pady=(0, 10))

        info_frame = ttk.Frame(self, padding=12)
        info_frame.pack(fill="both", expand=True)

        text_widget = tk.Text(info_frame, wrap="word", font=("Courier", 9), height=5)
        text_widget.pack(side="left", fill="both", expand=True)
        text_widget.insert("1.0", f"Command to be run in terminal:\n\n{self.terminal_cmd}")
        text_widget.configure(state="disabled")

        scrollbar = ttk.Scrollbar(info_frame, orient="vertical", command=text_widget.yview)
        scrollbar.pack(side="right", fill="y")
        text_widget.configure(yscrollcommand=scrollbar.set)

        btn_frame = ttk.Frame(self, padding=12)
        btn_frame.pack(fill="x")
        ttk.Button(btn_frame, text="OK", command=self.on_ok_clicked, width=12).pack(side="right")

    def on_ok_clicked(self):
        try:
            terminals = ["gnome-terminal", "konsole", "xfce4-terminal", "xterm"]
            launched = False
            for term in terminals:
                if shutil.which(term):
                    if term == "gnome-terminal":
                        subprocess.Popen([term, "--", "bash", "-c", f"{self.terminal_cmd}; echo 'Done. Press Enter to close.'; read"])
                    elif term == "konsole":
                        subprocess.Popen([term, "-e", "bash", "-c", f"{self.terminal_cmd}; echo 'Done. Press Enter to close.'; read"])
                    else:
                        subprocess.Popen([term, "-e", f"bash -c \"{self.terminal_cmd}; echo 'Done. Press Enter to close.'; read\""])
                    launched = True
                    break
            if not launched:
                subprocess.Popen(["x-terminal-emulator", "-e", f"bash -c \"{self.terminal_cmd}; echo 'Done. Press Enter to close.'; read\""])
        except Exception as e:
            messagebox.showerror("Error", f"Could not open terminal:\n\n{e}")
        
        self.destroy()


class IndexPremadeWindow:
    def __init__(self, parent, show_steps=False):
        self.parent = parent
        self.show_steps = show_steps
        threading.Thread(target=self.git_clone_worker, daemon=True).start()

    def git_clone_worker(self):
        cache_dir = Path(get_cache_directory())
        cache_dir.mkdir(parents=True, exist_ok=True)
        
        repo_dir = cache_dir / "joshw-browser"
        indexes_src_dir = repo_dir / "indexes"
        clone_url = "https://github.com/Aer0nic/joshw-browser.git"

        try:
            if repo_dir.exists():
                shutil.rmtree(repo_dir, ignore_errors=True)

            subprocess.run(["git", "clone", clone_url, str(repo_dir)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            if indexes_src_dir.exists():
                for f in indexes_src_dir.glob("domain_cache*"):
                    if f.is_file():
                        shutil.copy2(f, cache_dir / f.name)

            if repo_dir.exists():
                shutil.rmtree(repo_dir, ignore_errors=True)

            found_caches = list(cache_dir.glob("*domain_cache*"))

            if found_caches:
                if self.parent.winfo_exists():
                    self.parent.after(0, lambda: messagebox.showinfo(
                        "Success", 
                        "Premade indexes downloaded and installed successfully!"
                    ))
            else:
                terminal_cmd = f"cd {cache_dir} && git clone {clone_url} && cd joshw-browser/indexes/ && cp domain_cache* {cache_dir}"
                error_msg = "Premade index files containing 'domain_cache' were not found after cloning. Please do it manually. A terminal will now open when you click OK."
                if self.parent.winfo_exists():
                    self.parent.after(0, lambda: StepLogDialog(self.parent, "Installation Error", error_msg, terminal_cmd))

        except Exception as e:
            terminal_cmd = f"cd {cache_dir} && git clone {clone_url} && cd joshw-browser/indexes/ && cp domain_cache* {cache_dir}"
            error_msg = f"An error occurred during git clone or file copying:\n\n{e}\n\nPlease perform it manually. A terminal will now open when you click OK."
            if self.parent.winfo_exists():
                self.parent.after(0, lambda: StepLogDialog(self.parent, "Installation Error", error_msg, terminal_cmd))


class IndexAllWindow(tk.Toplevel):
    def __init__(self, parent, domains):
        super().__init__(parent)
        self.parent = parent
        self.domains = domains
        self.title("Index All Domains")
        self.geometry("580x520")
        self.minsize(520, 400)
        self.transient(parent)
        self.grab_set()

        self.domain_rows = {}
        self.total_start_time = time.time()
        self.is_running = True

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.build_ui()

        threading.Thread(target=self.index_all_worker, daemon=True).start()
        self.update_timers()

    def format_hms(self, seconds):
        s = int(seconds)
        hours = s // 3600
        minutes = (s % 3600) // 60
        secs = s % 60
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    def build_ui(self):
        main_frame = ttk.Frame(self, padding=10)
        main_frame.pack(fill="both", expand=True)

        ttk.Label(main_frame, text="Indexing all domains sequentially...", font=("Arial", 10, "bold")).pack(anchor="w", pady=(0, 8))

        container = ttk.Frame(main_frame)
        container.pack(fill="both", expand=True, pady=(0, 10))

        canvas = tk.Canvas(container, highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        for domain_name in self.domains.keys():
            row_frame = ttk.Frame(scrollable_frame, padding=(2, 4))
            row_frame.pack(fill="x", expand=True)

            lbl = ttk.Label(row_frame, text=domain_name, font=("Arial", 9), width=24, anchor="w")
            lbl.pack(side="left", padx=(0, 6))

            pbar_container = tk.Frame(row_frame, width=160, height=18)
            pbar_container.pack_propagate(False)
            pbar_container.pack(side="left", padx=6)

            pbar = ttk.Progressbar(pbar_container, orient="horizontal", mode="determinate", maximum=100)
            pbar.pack(fill="both", expand=True)

            pct_lbl = tk.Label(pbar_container, text="0%", font=("Arial", 8, "bold"), bg="white", fg="black")
            pct_lbl.place(relx=0.5, rely=0.5, anchor="center")

            time_lbl = ttk.Label(row_frame, text="00:00:00", font=("Arial", 9), width=10, anchor="e")
            time_lbl.pack(side="right", padx=(6, 0))

            self.domain_rows[domain_name] = {
                "label": lbl,
                "progress": pbar,
                "pct_label": pct_lbl,
                "time_label": time_lbl,
                "start_time": None,
                "elapsed": 0.0,
                "completed": False
            }

        bottom_frame = ttk.Frame(main_frame)
        bottom_frame.pack(fill="x", pady=(4, 0))

        self.total_time_var = tk.StringVar(value="Total Elapsed: 00:00:00")
        total_time_lbl = ttk.Label(bottom_frame, textvariable=self.total_time_var, font=("Arial", 9, "bold"))
        total_time_lbl.pack(side="right")

    def update_timers(self):
        if not self.is_running or not self.winfo_exists():
            return

        total_elapsed = time.time() - self.total_start_time
        self.total_time_var.set(f"Total Elapsed: {self.format_hms(total_elapsed)}")

        for data in self.domain_rows.values():
            if data["start_time"] is not None and not data["completed"]:
                elapsed = time.time() - data["start_time"]
                data["time_label"].configure(text=self.format_hms(elapsed))

        self.after(500, self.update_timers)

    def index_all_worker(self):
        for domain_name, root_url in self.domains.items():
            if not self.is_running or not self.winfo_exists():
                break

            row_data = self.domain_rows[domain_name]
            row_data["start_time"] = time.time()

            def progress_cb(scanned, discovered, total_files):
                if not self.winfo_exists():
                    return
                val = min(99.0, (scanned / max(1, discovered)) * 100)
                row_data["progress"]["value"] = val
                pct_str = f"{int(val)}%"
                row_data["pct_label"].configure(text=pct_str)

            try:
                all_entries = self.parent.crawl_domain_remote(
                    domain_name, root_url, report_progress=True, progress_callback=progress_cb, incremental=True
                )
                if all_entries is not None and self.is_running and self.winfo_exists():
                    self.parent.domain_full_entries = all_entries
                    self.parent.save_cache(domain_name)
            except Exception:
                pass

            if self.winfo_exists():
                elapsed = time.time() - row_data["start_time"]
                row_data["elapsed"] = elapsed
                row_data["completed"] = True
                row_data["progress"]["value"] = 100
                row_data["pct_label"].configure(text="100%")
                row_data["time_label"].configure(text=self.format_hms(elapsed))
                row_data["label"].configure(text=f"✓ {domain_name}", foreground="green")

        if self.is_running and self.winfo_exists():
            self.parent.after(0, self.finish_indexing)

    def finish_indexing(self):
        self.is_running = False
        try:
            messagebox.showinfo("Success", "All domains have been successfully indexed sequentially!")
        except Exception:
            pass
        self.destroy()

    def on_close(self):
        self.is_running = False
        self.destroy()


class DownloadProgressWindow(tk.Toplevel):
    def __init__(self, parent, title="Downloading..."):
        super().__init__(parent)
        self.title(title)
        self.geometry("380x130")
        self.resizable(False, False)
        self.transient(parent)
        self.is_cancelled = False

        self.file_label_text = tk.StringVar(value="Preparing download...")
        ttk.Label(self, textvariable=self.file_label_text, font=("Arial", 9)).pack(pady=15)
        self.pbar = ttk.Progressbar(self, orient="horizontal", mode="determinate", length=340)
        self.pbar.pack(pady=5)
        ttk.Button(self, text="Cancel", command=self.cancel).pack(pady=5)

    def update_progress(self, filename, downloaded, total, c_idx, t_idx, speed, eta):
        self.file_label_text.set(f"Downloading: {filename} ({downloaded}/{total} bytes)")
        if total > 0:
            self.pbar["value"] = (downloaded / total) * 100

    def cancel(self):
        self.is_cancelled = True
        self.destroy()


check_and_download_sid_firmware()
