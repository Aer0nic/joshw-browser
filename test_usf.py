import sys
import os
import subprocess
from pathlib import Path

def main():
    if len(sys.argv) < 2:
        print(f"Usage: python3 {sys.argv[0]} <path_to_miniusf>")
        sys.exit(1)

    audio_file = sys.argv[1]
    cache_dir = Path.home() / ".config" / "joshw"
    lazyusf_bin = cache_dir / "lazyusf"

    if not lazyusf_bin.exists():
        print(f"Error: {lazyusf_bin} not found.")
        sys.exit(1)

    abs_path = str(Path(audio_file).resolve())
    
    # 1. Launch lazyusf binary
    print(f"Launching lazyusf for: {abs_path}")
    process = subprocess.Popen(
        [str(lazyusf_bin), abs_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )

    # 2. Read the dynamic metadata line from stderr
    sample_rate = 44100
    is_little_endian = True

    print("Waiting for metadata from stderr...")
    while True:
        line = process.stderr.readline()
        if not line:
            break
        line_str = line.decode('utf-8', errors='ignore').strip()
        print(f"[STDERR] {line_str}")
        if line_str.startswith("LAZYUSF_META:"):
            parts = line_str.split(":")
            if len(parts) >= 2:
                try:
                    sample_rate = int(parts[1])
                except ValueError:
                    pass
            if len(parts) >= 3:
                try:
                    is_little_endian = (int(parts[2]) == 1)
                except ValueError:
                    pass
            break

    format_arg = "s16le" if is_little_endian else "s16be"
    print(f"\n[CONFIG] Sample Rate: {sample_rate} Hz | Endian: {format_arg}")
    print("Streaming audio directly to paplay...")

    # 3. Configure paplay with explicit raw stream details and a stable latency buffer
    audio_cmd = [
        "paplay",
        "--raw",
        "--channels=2",
        f"--rate={sample_rate}",
        f"--format={format_arg}",
        "--latency-msec=60"  # Forces PulseAudio to maintain a stable buffer window to eliminate choppiness
    ]

    # 4. DIRECT PIPE: Connect lazyusf stdout straight into paplay stdin.
    paplay_proc = subprocess.Popen(
        audio_cmd,
        stdin=process.stdout,
        stderr=subprocess.PIPE
    )

    # Allow process.stdout to be closed safely by paplay when finished
    process.stdout.close()

    # Wait for playback to finish naturally
    paplay_proc.wait()
    process.wait()

    print("Playback completed smoothly!")

if __name__ == "__main__":
    main()
