import ctypes
import sys

def dump_struct(file_path):
    # Load libgme dynamically
    lib = ctypes.CDLL("libgme.so.0" if os.path.exists("libgme.so.0") else "libgme.so")
    lib.gme_open_file.argtypes = [ctypes.c_char_p, ctypes.c_void_p, ctypes.c_int]
    lib.gme_open_file.restype = ctypes.c_char_p
    lib.gme_track_count.argtypes = [ctypes.c_void_p]
    lib.gme_track_count.restype = ctypes.c_int
    lib.gme_track_info.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_int]
    lib.gme_track_info.restype = ctypes.c_char_p
    lib.gme_free_info.argtypes = [ctypes.c_void_p]
    lib.gme_delete.argtypes = [ctypes.c_void_p]

    emu = ctypes.c_void_p(None)
    lib.gme_open_file(file_path.encode("utf-8"), ctypes.byref(emu), 44100)
    
    info_ptr = ctypes.c_void_p()
    lib.gme_track_info(emu, ctypes.byref(info_ptr), 0)
    
    if info_ptr.value:
        print(f"Base address: {hex(info_ptr.value)}")
        print("Dumping first 16 integers (64 bytes) from info struct:")
        for offset in range(0, 64, 4):
            val = ctypes.c_int.from_address(info_ptr.value + offset).value
            print(f"  Offset +{offset:2d}: {val} (hex: {hex(val)})")
            
        lib.gme_free_info(info_ptr)
    lib.gme_delete(emu)

if __name__ == "__main__":
    import os
    dump_struct(sys.argv[1])
