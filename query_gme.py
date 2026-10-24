import ctypes
import sys
import platform
from pathlib import Path

def find_gme_lib():
    system = platform.system()
    names = ["gme.dll", "libgme.dll"] if system == "Windows" else ["libgme.so.0", "libgme.so"]
    for name in names:
        try:
            return ctypes.CDLL(name)
        except OSError:
            continue
    raise RuntimeError("Could not find libgme shared library.")

def inspect_file(file_path):
    gme = find_gme_lib()
    
    # Bind functions safely
    gme.gme_open_file.argtypes = [ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_int]
    gme.gme_open_file.restype = ctypes.c_char_p

    gme.gme_track_count.argtypes = [ctypes.c_void_p]
    gme.gme_track_count.restype = ctypes.c_int

    gme.gme_delete.argtypes = [ctypes.c_void_p]
    gme.gme_delete.restype = None

    emu = ctypes.c_void_p(None)
    file_bytes = str(Path(file_path).resolve()).encode("utf-8")

    err = gme.gme_open_file(file_bytes, ctypes.byref(emu), 44100)
    if err:
        print(f"Error opening file: {err.decode('utf-8', errors='ignore')}")
        return

    # Query track count directly via primitive integer return function
    track_count = gme.gme_track_count(emu)
    print(f"--- File: {Path(file_path).name} ---")
    print(f"Total Tracks / Subsongs: {track_count}")

    gme.gme_delete(emu)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 query_gme.py <path>")
    else:
        inspect_file(sys.argv[1])
