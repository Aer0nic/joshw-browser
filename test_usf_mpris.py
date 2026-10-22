import os
import sys
import subprocess
import threading
import signal
import tkinter as tk
from pathlib import Path

# Import your mpris_helper module
try:
    from mpris_helper import setup_mpris_server
except ImportError:
    print("Error: Could not import mpris_helper.py. Make sure it's in the same directory.")
    sys.exit(1)


class StandaloneUSFTestWindow(tk.Tk):
    def __init__(self, audio_file):
        super().__init__()
        self.audio_file = audio_file
        self.title(f"Standalone USF MPRIS Test - {os.path.basename(audio_file)}")
        self.geometry("350x150")

        self.process = None
        self.is_playing = False
        self.is_paused = False

        # Status and duration fields required by mpris_helper
        self.status_var = tk.StringVar(value="Stopped")
        self.current_position_seconds = 0.0
        self.raw_duration_seconds = 180.0  # Default mock duration

        # UI Labels & Buttons for testing locally
        self.label = tk.Label(self, text=f"Playing: {os.path.basename(audio_file)}", wraplength=320)
        self.label.pack(pady=10)

        self.status_label = tk.Label(self, textvariable=self.status_var, font=("Arial", 10, "bold"))
        self.status_label.pack(pady=5)

        btn_frame = tk.Frame(self)
        btn_frame.pack(pady=10)

        tk.Button(btn_frame, text="Play/Pause", command=self.toggle_play_pause).pack(side="left", padx=5)
        tk.Button(btn_frame, text="Stop", command=self.stop).pack(side="left", padx=5)
        tk.Button(btn_frame, text="Quit", command=self.close).pack(side="left", padx=5)

        self.protocol("WM_DELETE_WINDOW", self.close)

        # Initialize MPRIS Server matching your bus name
        self.mpris_server, self.mpris_adapter = setup_mpris_server(self, player_name="USFPlayer")

        # Automatically start playback
        self.load_and_play_current()

    def load_and_play_current(self):
        self.stop_current_process()

        lazyusf_bin = Path(os.path.expanduser("~/.config/joshw/lazyusf"))
        if not lazyusf_bin.exists():
            print(f"[Error] lazyusf binary not found at {lazyusf_bin}")
            return

        cmd = [str(lazyusf_bin), "--playback", "-f", "2", str(self.audio_file)]

        try:
            self.process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                bufsize=1
            )
            self.is_playing = True
            self.is_paused = False
            self.status_var.set("Playing")
            print("[TestPlayer] lazyusf process started successfully.")
        except Exception as e:
            print(f"[TestPlayer] Failed to start lazyusf: {e}")

    def toggle_play_pause(self):
        if not self.process or self.process.poll() is not None:
            self.load_and_play_current()
            return

        if not self.is_paused:
            try:
                self.process.send_signal(signal.SIGSTOP)
                self.is_paused = True
                self.status_var.set("Paused")
                print("[TestPlayer] Process suspended (Paused).")
            except Exception as e:
                print(f"[TestPlayer] Error pausing: {e}")
        else:
            try:
                self.process.send_signal(signal.SIGCONT)
                self.is_paused = False
                self.status_var.set("Playing")
                print("[TestPlayer] Process resumed (Playing).")
            except Exception as e:
                print(f"[TestPlayer] Error resuming: {e}")

    def stop_current_process(self):
        self.is_playing = False
        if self.process:
            try:
                self.process.terminate()
                self.process.wait(timeout=0.3)
                if self.process.poll() is None:
                    self.process.kill()
            except Exception:
                pass
            self.process = None

    def stop(self):
        self.stop_current_process()
        self.status_var.set("Stopped")
        self.is_paused = False
        print("[TestPlayer] Stopped.")

    def close(self):
        self.stop_current_process()
        try:
            if hasattr(self, "mpris_server") and self.mpris_server:
                self.mpris_server.unpublish()
        except Exception:
            pass
        self.destroy()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 test_usf_mpris.py <path_to_song.miniusf>")
        sys.exit(1)

    song_path = sys.argv[1]
    if not os.path.exists(song_path):
        print(f"File not found: {song_path}")
        sys.exit(1)

    app = StandaloneUSFTestWindow(song_path)
    app.mainloop()
