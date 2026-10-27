import ctypes
import os
import sys

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
            lib.gme_free_info.argtypes = [ctypes.c_void_p]
            lib.gme_delete.argtypes = [ctypes.c_void_p]
            return lib
        except Exception:
            continue
    raise RuntimeError("Could not load libgme shared library.")

def print_spc_metadata(file_path):
    if not os.path.isfile(file_path):
        print(f"Error: File not found -> '{file_path}'")
        return

    lib = load_libgme()
    emu = ctypes.c_void_p(None)
    
    err = lib.gme_open_file(file_path.encode("utf-8"), ctypes.byref(emu), 44100)
    if err:
        print(f"Error opening SPC file: {err.decode('utf-8', errors='ignore')}")
        return

    track_count = lib.gme_track_count(emu)
    print(f"\n--- File: {os.path.basename(file_path)} ---")
    print(f"Total Subtracks/Tracks: {track_count}\n")

    for i in range(track_count):
        info_ptr = ctypes.c_void_p()
        lib.gme_track_info(emu, ctypes.byref(info_ptr), i)
        
        if info_ptr.value:
            base_addr = info_ptr.value
            
            # Read integer fields safely using memory offsets
            length_ms = ctypes.c_int.from_address(base_addr).value
            fade_ms = ctypes.c_int.from_address(base_addr + 12).value
            
            total_ms = (length_ms if length_ms > 0 else 0) + (fade_ms if fade_ms > 0 else 0)
            
            print(f"Track [{i + 1}]:")
            print(f"  Length:    {length_ms} ms ({length_ms / 1000.0:.2f} s)")
            print(f"  Fade-out:  {fade_ms} ms ({fade_ms / 1000.0:.2f} s)")
            print(f"  Total Dur: {total_ms} ms ({total_ms / 1000.0:.2f} s)")
            print("-" * 40)
            
            lib.gme_free_info(info_ptr)

    lib.gme_delete(emu)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 read_spc_data.py <path_to_spc>")
        sys.exit(1)
    
    print_spc_metadata(sys.argv[1])
