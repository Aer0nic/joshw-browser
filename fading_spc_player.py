import ctypes
import os
import sys
import time
import array

def load_libgme():
    names = ["libgme.so.0", "libgme.so", "gme.dll", "libgme.dll"]
    for name in names:
        try:
            lib = ctypes.CDLL(name)
            lib.gme_open_file.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.c_int]
            lib.gme_open_file.restype = ctypes.c_char_p
            lib.gme_track_count.argtypes = [ctypes.c_void_p]
            lib.gme_track_count.restype = ctypes.c_int
            lib.gme_track_info.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_int]
            lib.gme_track_info.restype = ctypes.c_char_p
            lib.gme_start_track.argtypes = [ctypes.c_void_p, ctypes.c_int]
            lib.gme_start_track.restype = ctypes.c_char_p
            lib.gme_play.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.POINTER(ctypes.c_short)]
            lib.gme_play.restype = ctypes.c_char_p
            lib.gme_free_info.argtypes = [ctypes.c_void_p]
            lib.gme_delete.argtypes = [ctypes.c_void_p]
            # Optional: check if gme_set_fade exists in this build
            if hasattr(lib, "gme_set_fade"):
                lib.gme_set_fade.argtypes = [ctypes.c_void_p, ctypes.c_int]
            return lib
        except Exception:
            continue
    raise RuntimeError("Could not load libgme shared library.")

def play_spc_with_fade(file_path, track_index=0, sample_rate=44100):
    try:
        import sounddevice as sd
    except ImportError:
        print("Error: 'sounddevice' module is required for playback. Install via pip install sounddevice")
        return

    if not os.path.isfile(file_path):
        print(f"Error: File not found -> '{file_path}'")
        return

    lib = load_libgme()
    emu = ctypes.c_void_p(None)
    
    err = lib.gme_open_file(file_path.encode("utf-8"), ctypes.byref(emu), sample_rate)
    if err:
        print(f"Error opening SPC file: {err.decode('utf-8', errors='ignore')}")
        return

    # Extract metadata safely using memory offsets with fallback handling
    info_ptr = ctypes.c_void_p()
    lib.gme_track_info(emu, ctypes.byref(info_ptr), track_index)
    
    base_length_ms = 180000  # Default 3 minutes if unreadable
    fade_ms = 0

    if info_ptr.value:
        try:
            val = ctypes.c_int.from_address(info_ptr.value).value
            if val > 0:
                base_length_ms = val
        except Exception:
            pass

        try:
            fade_val = ctypes.c_int.from_address(info_ptr.value + 12).value
            if fade_val > 0:
                fade_ms = fade_val
        except Exception:
            pass
            
        lib.gme_free_info(info_ptr)

    # Apply your fade logic rule:
    # If fade is missing, zero, or identical to length, default to 5 seconds (5000ms) fadeout
    default_fade_ms = 5000
    if fade_ms <= 0 or fade_ms == base_length_ms:
        actual_fade_ms = default_fade_ms
    else:
        actual_fade_ms = fade_ms

    total_duration_ms = base_length_ms + actual_fade_ms
    fade_start_ms = base_length_ms

    print(f"\nPlaying: {os.path.basename(file_path)} [Track {track_index + 1}]")
    print(f"  Base Length: {base_length_ms / 1000.0:.2f} s")
    print(f"  Fade-out:    {actual_fade_ms / 1000.0:.2f} s (Graceful ending)")
    print(f"  Total Dur:   {total_duration_ms / 1000.0:.2f} s\n")

    # Start the track
    lib.gme_start_track(emu, track_index)

    # Disable libgme's internal track length cutoff if the function is supported, 
    # forcing it to loop/play indefinitely until our Python timer halts it.
    if hasattr(lib, "gme_set_fade"):
        # Setting fade to -1 or a massive number disables internal engine track termination
        lib.gme_set_fade(emu, -1)

    chunk_samples = 4096
    total_samples_rendered = 0
    
    base_samples = int((base_length_ms / 1000.0) * sample_rate)
    fade_samples = int((actual_fade_ms / 1000.0) * sample_rate)
    max_samples = base_samples + fade_samples
    fade_start_sample = base_samples

    total_duration_sec = total_duration_ms / 1000.0

    try:
        stream = sd.RawOutputStream(
            samplerate=sample_rate,
            channels=2,
            dtype="int16",
            blocksize=chunk_samples,
            latency="high",
            device=4
        )
        stream.start()

        last_printed_tenths = -1

        while total_samples_rendered < max_samples:
            samples_to_render = min(chunk_samples, max_samples - total_samples_rendered)
            
            buf = (ctypes.c_short * (samples_to_render * 2))()
            err = lib.gme_play(emu, samples_to_render * 2, buf)
            
            pcm_data = bytes(buf)
            if not pcm_data:
                break

            # Once we pass the base length, apply the smooth volume reduction ramp
            if total_samples_rendered >= fade_start_sample:
                arr = array.array("h")
                arr.frombytes(pcm_data)
                
                for i in range(0, len(arr), 2):
                    current_absolute_sample = total_samples_rendered + (i // 2)
                    fade_progress_sample = current_absolute_sample - fade_start_sample
                    
                    # Linearly scale volume from 1.0 down to 0.0 over the fade window
                    multiplier = max(0.0, 1.0 - (fade_progress_sample / fade_samples))
                    
                    arr[i] = int(arr[i] * multiplier)         # Left channel
                    arr[i+1] = int(arr[i+1] * multiplier)     # Right channel
                
                pcm_data = arr.tobytes()

            stream.write(pcm_data)
            total_samples_rendered += samples_to_render

            # Terminal timer update
            current_sec = total_samples_rendered / sample_rate
            current_tenths = int(current_sec * 10)
            
            if current_tenths != last_printed_tenths:
                last_printed_tenths = current_tenths
                sys.stdout.write(f"\r[Time: {current_sec:05.1f}s / {total_duration_sec:.1f}s] Playing...")
                sys.stdout.flush()

        print("\n\nPlayback finished cleanly with a graceful fade-out.")
        stream.stop()
        stream.close()

    except Exception as e:
        print(f"\nPlayback error: {e}")
    finally:
        lib.gme_delete(emu)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 fading_spc_player.py <path_to_spc>")
        sys.exit(1)
    
    play_spc_with_fade(sys.argv[1])
