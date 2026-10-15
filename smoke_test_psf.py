#!/usr/bin/env python3
"""
smoke_test_psf.py - CLI smoke test for native PSF/PSF2 playback via AOSDK.

Opens a PSF or PSF2 file and streams audio directly to the sound device
using sounddevice, without writing any converted WAV file.
"""

import argparse
import sys
import os
import time

# Ensure workspace site-packages is in sys.path
_venv_site = os.path.join(os.path.dirname(__file__), ".venv", "lib", f"python{sys.version_info.major}.{sys.version_info.minor}", "site-packages")
if os.path.exists(_venv_site) and _venv_site not in sys.path:
    sys.path.insert(0, _venv_site)

from psf_ctypes import AOSDKContext, AOSDKError, find_aosdk_lib

try:
    import sounddevice as sd
except ImportError:
    print("ERROR: 'sounddevice' module is not installed.", file=sys.stderr)
    print("Please install it or activate .venv.", file=sys.stderr)
    sys.exit(1)


def format_time(seconds: float) -> str:
    if seconds is None or seconds < 0:
        return "Unknown"
    s = int(seconds)
    m, s = divmod(s, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h:02d}:{m:02d}:{s:02d}"
    return f"{m:02d}:{s:02d}"


def run_smoke_test(filepath: str, duration_sec: float = 3.0, volume: float = 1.0) -> int:
    if not os.path.isfile(filepath):
        print(f"ERROR: File not found: {filepath}", file=sys.stderr)
        return 1

    try:
        lib_path = find_aosdk_lib()
        print(f"Loaded AOSDK library from: {lib_path}")
    except Exception as e:
        print(f"ERROR locating AOSDK library: {e}", file=sys.stderr)
        return 1

    print(f"\nOpening track: {filepath}")
    start_time = time.time()

    try:
        with AOSDKContext() as ctx:
            ctx.open(filepath)
            track_format = ctx.format_name
            total_duration = ctx.get_duration()
            fade_duration = ctx.get_fade()
            tags = ctx.get_tags()

            print(f"  Format:   {track_format}")
            print(f"  Duration: {format_time(total_duration)} ({total_duration}s), Fade: {fade_duration}s")
            if tags:
                print("  Metadata Tags:")
                for k, v in tags.items():
                    print(f"    {k}: {v}")

            print(f"\nInitializing audio stream (sounddevice, 44100 Hz, stereo, int16)...")
            output_device = sd.default.device
            print(f"  Audio Output Device: {output_device}")

            samples_to_render = int(44100 * duration_sec)
            chunk_size = 2048
            samples_rendered = 0

            with sd.RawOutputStream(samplerate=44100, channels=2, dtype='int16') as stream:
                print(f"  Playing {duration_sec:.1f} seconds of audio directly to output device...")
                playback_start = time.time()

                while samples_rendered < samples_to_render and not ctx.is_done():
                    to_req = min(chunk_size, samples_to_render - samples_rendered)
                    count, pcm = ctx.render(to_req)
                    if count <= 0:
                        break

                    # Apply software volume scaling if volume != 1.0
                    if volume != 1.0:
                        import array
                        arr = array.array('h')
                        arr.frombytes(pcm)
                        for i in range(len(arr)):
                            arr[i] = int(arr[i] * volume)
                        pcm = arr.tobytes()

                    stream.write(pcm)
                    samples_rendered += count

                playback_elapsed = time.time() - playback_start
                print(f"  Rendered & played {samples_rendered} samples in {playback_elapsed:.2f}s.")

            ctx.stop()
            print("  Playback stopped cleanly.")

    except AOSDKError as e:
        print(f"\nAOSDK Error during playback: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"\nUnexpected error: {e}", file=sys.stderr)
        return 1

    total_elapsed = time.time() - start_time
    print(f"\nSmoke test PASSED successfully in {total_elapsed:.2f}s! No temporary files were written.\n")
    return 0


def main():
    parser = argparse.ArgumentParser(description="CLI smoke test for native PSF/PSF2 playback.")
    parser.add_argument("file", nargs="?", default="aosdk/samples/pe.psf",
                        help="Path to .psf or .psf2 file (default: aosdk/samples/pe.psf)")
    parser.add_argument("--seconds", "-s", type=float, default=3.0,
                        help="Seconds of audio to render/play (default: 3.0)")
    parser.add_argument("--volume", "-v", type=float, default=1.0,
                        help="Volume factor between 0.0 and 2.0 (default: 1.0)")
    args = parser.parse_args()

    sys.exit(run_smoke_test(args.file, duration_sec=args.seconds, volume=args.volume))


if __name__ == "__main__":
    main()

