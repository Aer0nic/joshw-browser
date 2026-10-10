import os
import shutil
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import tkinter as tk
from tkinter import ttk, messagebox
from config import get_cache_directory
from network import fetch_directory, safe_filename, unique_path


def format_download_size(num_bytes):
    if num_bytes is None or num_bytes < 0:
        return "0 B"
    val = float(num_bytes)
    units = ["B", "KB", "MB", "GB", "TB"]
    unit_idx = 0
    while val >= 1000 and unit_idx < len(units) - 1:
        val /= 1000
        unit_idx += 1
    return f"{int(val)} {units[unit_idx]}" if unit_idx == 0 else f"{val:.1f} {units[unit_idx]}"


def format_speed(bytes_per_sec):
    if bytes_per_sec < 1024:
        return f"{bytes_per_sec:.1f} B/s"
    elif bytes_per_sec < 1024 * 1024:
        return f"{bytes_per_sec / 1024:.1f} KB/s"
    else:
        return f"{bytes_per_sec / (1024 * 1024):.1f} MB/s"


def format_eta(seconds):
    if seconds is None or seconds < 0 or seconds > 86400:
        return "Calculations..."
    secs = int(seconds)
    if secs < 60:
        return f"{secs}s"
    elif secs < 3600:
        return f"{secs // 60}m {secs % 60}s"
    else:
        return f"{secs // 3600}h {(secs % 3600) // 60}m"


class DownloadProgressWindow(tk.Toplevel):
    def __init__(self, parent, title="Downloading Files"):
        super().__init__(parent)
        self.title(title)
        self.geometry("450x220")
        self.resizable(False, False)
        self.transient(parent)
        try:
            self.update_idletasks()
            self.grab_set()
        except Exception:
            pass

        self.file_label_text = tk.StringVar(value="Pre-indexing files to download...")
        self.progress_val = tk.DoubleVar(value=0.0)
        self.size_progress_text = tk.StringVar(value="0 B / 0 B")
        self.speed_text = tk.StringVar(value="Speed: 0 KB/s")
        self.eta_text = tk.StringVar(value="ETA: --")
        self.count_text = tk.StringVar(value="")

        padding_frame = ttk.Frame(self, padding=15)
        padding_frame.pack(fill="both", expand=True)

        ttk.Label(padding_frame, textvariable=self.file_label_text, wraplength=410, font=("Arial", 10, "bold")).pack(anchor="w", pady=(0, 4))
        
        count_size_frame = ttk.Frame(padding_frame)
        count_size_frame.pack(fill="x", pady=(0, 8))
        ttk.Label(count_size_frame, textvariable=self.count_text, font=("Arial", 9)).pack(side="left")
        ttk.Label(count_size_frame, textvariable=self.size_progress_text, font=("Arial", 9)).pack(side="right")

        self.p_bar = ttk.Progressbar(padding_frame, orient="horizontal", mode="determinate", variable=self.progress_val, length=410, maximum=100)
        self.p_bar.pack(fill="x", pady=(0, 10))

        info_frame = ttk.Frame(padding_frame)
        info_frame.pack(fill="x", pady=(0, 12))
        ttk.Label(info_frame, textvariable=self.speed_text, font=("Arial", 9)).pack(side="left")
        ttk.Label(info_frame, textvariable=self.eta_text, font=("Arial", 9)).pack(side="right")

        self.cancel_btn = ttk.Button(padding_frame, text="Cancel Download", command=self.on_cancel)
        self.cancel_btn.pack(anchor="center")
        self.is_cancelled = False

    def on_cancel(self):
        self.is_cancelled = True
        self.cancel_btn.configure(state="disabled")
        self.file_label_text.set("Cancelling downloads...")

    def update_progress(self, filename, downloaded_bytes, total_bytes, current_file_idx, total_files, speed_bps, eta_seconds):
        self.file_label_text.set(f"Downloading: {filename}")
        self.count_text.set(f"File {current_file_idx} of {total_files}")
        d_str = format_download_size(downloaded_bytes)
        t_str = format_download_size(total_bytes) if total_bytes and total_bytes > 0 else "Unknown"
        self.size_progress_text.set(f"{d_str} / {t_str}")

        if total_bytes and total_bytes > 0:
            pct = min(max((downloaded_bytes / total_bytes) * 100, 0), 100)
            self.progress_val.set(pct)
        else:
            self.progress_val.set(0)

        self.speed_text.set(f"Speed: {format_speed(speed_bps)}")
        self.eta_text.set(f"ETA: {format_eta(eta_seconds)}")


class IndexAllWindow(tk.Toplevel):
    def __init__(self, parent, domains_dict):
        super().__init__(parent)
        self.title("Index All Domains")
        self.geometry("680x580")
        self.transient(parent)
        self.grab_set()

        self.parent = parent
        self.stop_event = threading.Event()
        self.domain_items = list(domains_dict.items())

        container = ttk.Frame(self, padding=12)
        container.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(container, highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=self.canvas.yview)
        self.scrollable_frame = ttk.Frame(self.canvas)

        self.scrollable_frame.bind("<Configure>", lambda e: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.canvas.configure(yscrollcommand=scrollbar.set)

        self.canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.rows = []
        for domain_name, url in self.domain_items:
            row_frame = ttk.Frame(self.scrollable_frame, padding=4)
            row_frame.pack(fill="x", anchor="w")

            lbl_name = tk.Label(row_frame, text=domain_name, anchor="w", width=24, font=("Arial", 9, "bold"))
            lbl_name.pack(side="left", padx=5)

            p_bar = ttk.Progressbar(row_frame, orient="horizontal", mode="indeterminate", length=120)
            lbl_status = ttk.Label(row_frame, text="Waiting...", font=("Arial", 9), width=32, anchor="w")
            lbl_status.pack(side="left", padx=5)

            self.rows.append({"name": domain_name, "url": url, "lbl_name": lbl_name, "lbl_status": lbl_status, "p_bar": p_bar})

        bottom_frame = ttk.Frame(self, padding=12)
        bottom_frame.pack(fill="x", side="bottom")

        self.final_time_label = ttk.Label(bottom_frame, text="Total Time: 00:00:00", font=("Arial", 10, "bold"))
        self.final_time_label.pack(side="left", padx=5)

        self.close_cancel_btn = ttk.Button(bottom_frame, text="Cancel", command=self.on_cancel)
        self.close_cancel_btn.pack(side="right", padx=5)

        self.is_finished = False
        self.total_elapsed = 0.0
        self.current_active_row = None
        self.current_domain_start_time = 0.0
        self.timer_job = None

        threading.Thread(target=self.run_indexing, daemon=True).start()
        self.tick_timer()

    def tick_timer(self):
        if not self.is_finished and self.current_active_row:
            elapsed = time.time() - self.current_domain_start_time
            hours, mins, secs = int(elapsed // 3600), int((elapsed % 3600) // 60), int(elapsed % 60)
            curr_text = self.current_active_row.get("last_status_msg", "Indexing...")
            self.current_active_row["lbl_status"].configure(text=f"{curr_text} ({hours:02d}:{mins:02d}:{secs:02d})")
        if not self.is_finished:
            self.timer_job = self.after(250, self.tick_timer)

    def on_cancel(self):
        if self.timer_job:
            self.after_cancel(self.timer_job)
        if self.is_finished:
            self.destroy()
        else:
            self.stop_event.set()
            self.close_cancel_btn.configure(state="disabled")
            for r in self.rows:
                if r["lbl_status"].cget("text").startswith("Indexing"):
                    r["p_bar"].stop()
                    r["p_bar"].pack_forget()
                    r["lbl_status"].configure(text="Cancelled")
            self.final_time_label.configure(text=f"Cancelled. Total Time: {self.format_total_time(self.total_elapsed)}")
            self.close_cancel_btn.configure(text="Close", state="normal", command=self.destroy)

    def format_total_time(self, seconds):
        return f"{int(seconds // 3600):02d}:{int((seconds % 3600) // 60):02d}:{int(seconds % 60):02d}"

    def run_indexing(self):
        self.parent.stop_event.clear()
        total_time_sum = 0.0
        for row in self.rows:
            if self.stop_event.is_set():
                break
            domain_name, url = row["name"], row["url"]
            self.current_active_row = row
            self.current_domain_start_time = time.time()
            self.after(0, lambda r=row: self.start_row_indexing(r))

            def progress_cb(scanned_dirs, total_dirs, files_count):
                if self.stop_event.is_set():
                    return
                row["last_status_msg"] = f"Scanned {scanned_dirs}/{total_dirs} dirs ({files_count} files)"

            start_t = time.time()
            entries = self.parent.crawl_domain_remote(domain_name, url, report_progress=False, progress_callback=progress_cb, stop_event=self.stop_event, incremental=True)
            elapsed = time.time() - start_t
            self.current_active_row = None

            if self.stop_event.is_set():
                break

            if entries is not None:
                self.parent.domain_full_entries = entries
                self.parent.cached_dir_sizes = {}
                self.parent.save_cache(domain_name)

            total_time_sum += elapsed
            self.after(0, lambda r=row, el=elapsed, t_sum=total_time_sum, count=(len(entries) if entries else 0): self.finish_row_indexing(r, el, t_sum, count))

        if not self.stop_event.is_set():
            self.is_finished = True
            if self.timer_job:
                self.after_cancel(self.timer_job)
            self.after(0, lambda: self.all_finished(total_time_sum))

    def start_row_indexing(self, row):
        row["last_status_msg"] = "Indexing..."
        row["lbl_status"].configure(text="Indexing... (00:00:00)")
        row["p_bar"].pack(side="left", padx=5)
        row["p_bar"].start(10)

    def finish_row_indexing(self, row, elapsed, total_sum, files_count):
        row["p_bar"].stop()
        row["p_bar"].pack_forget()
        row["lbl_name"].configure(fg="green")
        hours_e, mins_e, secs_e = int(elapsed // 3600), int((elapsed % 3600) // 60), int(elapsed % 60)
        row["lbl_status"].configure(text=f"✓ Done ({files_count} files, {hours_e:02d}:{mins_e:02d}:{secs_e:02d})")
        self.total_elapsed = total_sum
        self.final_time_label.configure(text=f"Total Time: {self.format_total_time(total_sum)}")

    def all_finished(self, total_sum):
        self.is_finished = True
        self.final_time_label.configure(text=f"Total Time: {self.format_total_time(total_sum)}")
        self.close_cancel_btn.configure(text="Close", command=self.destroy)