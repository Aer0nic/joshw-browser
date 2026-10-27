"""
psf_ctypes.py - Python ctypes wrapper for AOSDK PSF/PSF2 C shared library.

Provides safe handle management, explicit ctypes function signatures,
rich exception hierarchy, and high-level Python API for playing Sony
PlayStation (PSF) and PlayStation 2 (PSF2) audio tracks.
"""

import ctypes
import ctypes.util
import os
import sys
from typing import Optional, Dict, Tuple

# Add workspace .venv to sys.path if running with system python
_venv_site = os.path.join(os.path.dirname(__file__), ".venv", "lib", f"python{sys.version_info.major}.{sys.version_info.minor}", "site-packages")
if os.path.exists(_venv_site) and _venv_site not in sys.path:
    sys.path.insert(0, _venv_site)

from config import get_cache_directory, resource_path


# --- Exceptions ---

class AOSDKError(Exception):
    """Base exception for all AOSDK operations."""
    pass


class AOSDKLoadError(AOSDKError):
    """Raised when the AOSDK shared library cannot be located or loaded."""
    pass


class AOSDKOpenError(AOSDKError):
    """Raised when opening or parsing a PSF/PSF2 file fails."""
    pass


class AOSDKRenderError(AOSDKError):
    """Raised when rendering audio samples fails."""
    pass


class AOSDKSeekError(AOSDKError):
    """Raised when an unsupported seek operation is attempted."""
    pass


# --- Constants & Return Codes ---

AOSDK_OK = 0
AOSDK_ERROR_NULL_POINTER = -1
AOSDK_ERROR_FILE_NOT_FOUND = -2
AOSDK_ERROR_INVALID_FORMAT = -3
AOSDK_ERROR_ENGINE_INIT = -4
AOSDK_ERROR_SEEK_UNSUPPORTED = -5
AOSDK_ERROR_NOT_OPEN = -6
AOSDK_ERROR_BUFFER_TOO_SMALL = -7
AOSDK_ERROR_INTERNAL = -8

AOSDK_FORMAT_NONE = 0
AOSDK_FORMAT_PSF = 1
AOSDK_FORMAT_PSF2 = 2

AOSDK_SAMPLE_RATE = 44100
AOSDK_CHANNELS = 2


def find_aosdk_lib() -> str:
    """
    Search for libaosdk.so in priority order:
    1. Environment variable AOSDK_LIB_PATH
    2. Bundled inside PyInstaller runtime (_MEIPASS) or local project (assets/libraries/libaosdk.so)
    3. ~/.config/joshw/libaosdk.so
    4. Project directory fallbacks
    5. System library paths
    """
    env_path = os.environ.get("AOSDK_LIB_PATH")
    if env_path and os.path.isfile(env_path):
        return env_path

    cache_dir = get_cache_directory()
    
    # Use resource_path for bundled PyInstaller support
    bundled_lib = resource_path("assets/libraries/libaosdk.so")
    bundled_lib_root = resource_path("libaosdk.so")
    bundled_lib_bin = resource_path("lib/libaosdk.so")

    candidates = [
        str(bundled_lib),
        str(bundled_lib_bin),
        str(bundled_lib_root),
        os.path.join(cache_dir, "libaosdk.so"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "aosdk", "libaosdk.so"),
        "/usr/local/lib/libaosdk.so",
        "/usr/lib/libaosdk.so",
    ]

    for p in candidates:
        if os.path.isfile(p):
            return p

    sys_found = ctypes.util.find_library("aosdk")
    if sys_found:
        return sys_found

    raise AOSDKLoadError(
        f"Could not find 'libaosdk.so'. Searched in: {', '.join(candidates)}. "
        "Please build it using ./build_aosdk.sh."
    )


class _AOSDKBinding:
    """Singleton holder for ctypes function signatures."""
    _instance = None

    def __init__(self, lib_path: Optional[str] = None):
        if lib_path is None:
            lib_path = find_aosdk_lib()

        try:
            self.lib = ctypes.CDLL(lib_path)
        except OSError as e:
            raise AOSDKLoadError(f"Failed to load shared library '{lib_path}': {e}") from e

        self._bind_functions()

    def _bind_functions(self):
        c_void_p = ctypes.c_void_p
        c_char_p = ctypes.c_char_p
        c_int = ctypes.c_int
        c_double = ctypes.c_double
        c_int16 = ctypes.c_int16
        c_size_t = ctypes.c_size_t
        POINTER = ctypes.POINTER

        # aosdk_create
        self.lib.aosdk_create.argtypes = []
        self.lib.aosdk_create.restype = c_void_p

        # aosdk_destroy
        self.lib.aosdk_destroy.argtypes = [c_void_p]
        self.lib.aosdk_destroy.restype = None

        # aosdk_open
        self.lib.aosdk_open.argtypes = [c_void_p, c_char_p]
        self.lib.aosdk_open.restype = c_int

        # aosdk_render
        self.lib.aosdk_render.argtypes = [c_void_p, POINTER(c_int16), c_int]
        self.lib.aosdk_render.restype = c_int

        # aosdk_stop
        self.lib.aosdk_stop.argtypes = [c_void_p]
        self.lib.aosdk_stop.restype = c_int

        # aosdk_reset
        self.lib.aosdk_reset.argtypes = [c_void_p]
        self.lib.aosdk_reset.restype = c_int

        # aosdk_seek
        self.lib.aosdk_seek.argtypes = [c_void_p, c_double]
        self.lib.aosdk_seek.restype = c_int

        # aosdk_is_open
        self.lib.aosdk_is_open.argtypes = [c_void_p]
        self.lib.aosdk_is_open.restype = c_int

        # aosdk_is_done
        self.lib.aosdk_is_done.argtypes = [c_void_p]
        self.lib.aosdk_is_done.restype = c_int

        # aosdk_get_format
        self.lib.aosdk_get_format.argtypes = [c_void_p]
        self.lib.aosdk_get_format.restype = c_int

        # aosdk_get_sample_rate
        self.lib.aosdk_get_sample_rate.argtypes = [c_void_p]
        self.lib.aosdk_get_sample_rate.restype = c_int

        # aosdk_get_channels
        self.lib.aosdk_get_channels.argtypes = [c_void_p]
        self.lib.aosdk_get_channels.restype = c_int

        # aosdk_get_duration
        self.lib.aosdk_get_duration.argtypes = [c_void_p, POINTER(c_double)]
        self.lib.aosdk_get_duration.restype = c_int

        # aosdk_get_fade
        self.lib.aosdk_get_fade.argtypes = [c_void_p, POINTER(c_double)]
        self.lib.aosdk_get_fade.restype = c_int

        # aosdk_get_position
        self.lib.aosdk_get_position.argtypes = [c_void_p]
        self.lib.aosdk_get_position.restype = c_double

        # aosdk_get_last_error
        self.lib.aosdk_get_last_error.argtypes = [c_void_p]
        self.lib.aosdk_get_last_error.restype = c_char_p

        # aosdk_get_tag
        self.lib.aosdk_get_tag.argtypes = [c_void_p, c_char_p, c_char_p, c_size_t]
        self.lib.aosdk_get_tag.restype = c_int


def get_binding(lib_path: Optional[str] = None) -> _AOSDKBinding:
    if _AOSDKBinding._instance is None:
        _AOSDKBinding._instance = _AOSDKBinding(lib_path)
    return _AOSDKBinding._instance


class AOSDKContext:
    """
    Python wrapper managing a native AOSDK playback context.
    Ensures safe lifecycle management and clean cleanup.
    """

    def __init__(self, lib_path: Optional[str] = None):
        self._closed = True
        self._handle = None
        self._current_file = None

        self._binding = get_binding(lib_path)
        handle = self._binding.lib.aosdk_create()
        if not handle:
            raise AOSDKError("Failed to allocate native AOSDK playback context.")
        
        self._handle = handle
        self._closed = False

    def _check_valid(self):
        if self._closed or not self._handle:
            raise AOSDKError("AOSDKContext has been closed.")

    def close(self):
        """Releases all native resources associated with this context."""
        if not self._closed and self._handle:
            try:
                self._binding.lib.aosdk_destroy(self._handle)
            except Exception:
                pass
            self._handle = None
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def __del__(self):
        self.close()

    def get_last_error(self) -> str:
        self._check_valid()
        msg = self._binding.lib.aosdk_get_last_error(self._handle)
        return msg.decode("utf-8", errors="replace") if msg else ""

    def open(self, filepath: str) -> None:
        """
        Opens a PSF or PSF2 file.
        Raises AOSDKOpenError on failure.
        """
        self._check_valid()
        if not os.path.exists(filepath):
            raise AOSDKOpenError(f"File not found: '{filepath}'")

        path_bytes = os.path.abspath(filepath).encode("utf-8")
        ret = self._binding.lib.aosdk_open(self._handle, path_bytes)
        if ret != AOSDK_OK:
            err = self.get_last_error()
            raise AOSDKOpenError(f"Failed to open '{filepath}': {err} (code {ret})")
        self._current_file = filepath

    def stop(self) -> None:
        """Stops active playback."""
        self._check_valid()
        self._binding.lib.aosdk_stop(self._handle)
        self._current_file = None

    def reset(self) -> None:
        """Resets playback to the beginning of the track."""
        self._check_valid()
        ret = self._binding.lib.aosdk_reset(self._handle)
        if ret != AOSDK_OK:
            err = self.get_last_error()
            raise AOSDKError(f"Failed to reset track: {err} (code {ret})")

    def seek(self, seconds: float) -> None:
        """
        Seeks to the given position in seconds.
        If seconds <= 0.05, resets to start.
        If non-zero seek is attempted, raises AOSDKSeekError.
        """
        self._check_valid()
        ret = self._binding.lib.aosdk_seek(self._handle, ctypes.c_double(seconds))
        if ret == AOSDK_ERROR_SEEK_UNSUPPORTED:
            err = self.get_last_error()
            raise AOSDKSeekError(err or "Seeking is unsupported for PSF/PSF2 emulation tracks.")
        elif ret != AOSDK_OK:
            err = self.get_last_error()
            raise AOSDKError(f"Seek failed: {err} (code {ret})")

    def render(self, num_stereo_samples: int) -> Tuple[int, bytes]:
        """
        Renders up to num_stereo_samples of interleaved signed 16-bit stereo PCM audio.
        Returns (samples_rendered, raw_pcm_bytes).
        """
        self._check_valid()
        if num_stereo_samples <= 0:
            return 0, b""

        total_int16 = num_stereo_samples * 2
        buf = (ctypes.c_int16 * total_int16)()
        rendered = self._binding.lib.aosdk_render(self._handle, buf, ctypes.c_int(num_stereo_samples))
        if rendered < 0:
            err = self.get_last_error()
            raise AOSDKRenderError(f"Rendering error: {err} (code {rendered})")

        if rendered == 0:
            return 0, b""

        # Return the exact slice of PCM bytes
        byte_count = rendered * 4  # 2 channels * 2 bytes per sample
        return rendered, bytes(buf)[:byte_count]

    def is_open(self) -> bool:
        if self._closed or not self._handle:
            return False
        return bool(self._binding.lib.aosdk_is_open(self._handle))

    def is_done(self) -> bool:
        if self._closed or not self._handle:
            return True
        return bool(self._binding.lib.aosdk_is_done(self._handle))

    @property
    def sample_rate(self) -> int:
        return AOSDK_SAMPLE_RATE

    @property
    def channels(self) -> int:
        return AOSDK_CHANNELS

    @property
    def format_name(self) -> str:
        self._check_valid()
        fmt = self._binding.lib.aosdk_get_format(self._handle)
        if fmt == AOSDK_FORMAT_PSF:
            return "PlayStation (PSF)"
        elif fmt == AOSDK_FORMAT_PSF2:
            return "PlayStation 2 (PSF2)"
        return "Unknown"

    def get_duration(self) -> Optional[float]:
        """Returns track duration in seconds from tags, or None if unspecified."""
        self._check_valid()
        d = ctypes.c_double()
        ret = self._binding.lib.aosdk_get_duration(self._handle, ctypes.byref(d))
        if ret == AOSDK_OK and d.value > 0.0:
            return d.value
        return None

    def get_fade(self) -> float:
        """Returns fade duration in seconds, or 0.0 if unspecified."""
        self._check_valid()
        f = ctypes.c_double()
        ret = self._binding.lib.aosdk_get_fade(self._handle, ctypes.byref(f))
        if ret == AOSDK_OK and f.value >= 0.0:
            return f.value
        return 0.0

    def get_position(self) -> float:
        """Returns current playback position in seconds based on samples rendered."""
        self._check_valid()
        return float(self._binding.lib.aosdk_get_position(self._handle))

    def get_tag(self, tag_name: str) -> Optional[str]:
        """Retrieves a single Corlett tag by name."""
        self._check_valid()
        buf = ctypes.create_string_buffer(512)
        ret = self._binding.lib.aosdk_get_tag(
            self._handle,
            tag_name.encode("utf-8"),
            buf,
            ctypes.c_size_t(len(buf))
        )
        if ret == AOSDK_OK:
            val = buf.value.decode("utf-8", errors="replace").strip()
            return val if val else None
        return None

    def get_tags(self) -> Dict[str, str]:
        """Retrieves all common metadata tags."""
        tags = {}
        common_keys = [
            "title", "game", "artist", "year", "genre",
            "copyright", "psfby", "comment", "length", "fade",
            "_lib", "_lib2", "_lib3"
        ]
        for key in common_keys:
            val = self.get_tag(key)
            if val is not None:
                tags[key] = val
        return tags