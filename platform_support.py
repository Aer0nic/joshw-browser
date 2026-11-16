"""
Cross-platform helpers (Linux + Windows).

Everything OS-specific that the players and browser need lives here, so the
player modules only make small, uniform calls:

  IS_WINDOWS / IS_LINUX           - which OS we are on
  find_tool(name)                 - locate a backend executable / library
  hidden_popen_kwargs()           - Popen kwargs: no console window on Windows
  suspend_process / resume_process- pause/resume a backend process
  set_process_volume              - per-process volume (Windows mixer); False if unsupported
  open_path                       - open a folder/file with the OS default handler
  PcmPipeProcess                  - a Popen look-alike that plays a decoder's PCM/WAV
                                    stdout through sounddevice and prints progress lines

Bundled Windows backends live in assets/libraries/windows/<tool>/ (each tool in
its own folder because its DLLs must sit next to the .exe). See
tools/fetch_windows_deps.py for where each one comes from.
"""
import ctypes
import io
import os
import queue
import shutil
import signal
import struct
import subprocess
import sys
import threading
import time
from array import array
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
IS_LINUX = sys.platform.startswith("linux")

# ---------------------------------------------------------------------------
# Locating backends
# ---------------------------------------------------------------------------
# logical tool name -> (folder under assets/libraries/windows, windows exe/dll name)
WINDOWS_TOOLS = {
    "vgmstream-cli": ("vgmstream", "vgmstream-cli.exe"),
    "sidplayfp":     ("sidplayfp", "sidplayfp.exe"),
    "vgmplay":       ("vgmplay", "VGMPlay.exe"),
    "playbae":       ("neobae", "playbae.exe"),
    "mpv":           ("mpv", "mpv.exe"),
    "7z":            ("7zip", "7za.exe"),
    "libgme":        ("gme", "libgme.dll"),
    "aosdk":         ("decoders", "aosdk.dll"),
    "2sfdec":        ("decoders", "2sfdec.exe"),
    "gsfdec":        ("decoders", "gsfdec.exe"),
    "usfdec":        ("decoders", "usfdec.exe"),
    "hvl2wav":       ("decoders", "hvl2wav.exe"),
}


def _project_root():
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent


def windows_tools_dir():
    return _project_root() / "assets" / "libraries" / "windows"


def _user_tools_dir():
    # Same folder config.get_cache_directory() uses, without importing config
    if IS_WINDOWS:
        return Path(os.path.expanduser("~/Documents")) / "joshw"
    return Path(os.path.expanduser("~/.config")) / "joshw"


def find_tool(name):
    """Return the path to a backend as a string, or None if it can't be found.

    Windows: bundled assets/libraries/windows/<folder>/<exe>, then the user's
    joshw folder, then PATH.  Linux: the user's joshw folder, then PATH.
    """
    if IS_WINDOWS:
        folder, exe = WINDOWS_TOOLS.get(name, (name, name + ".exe"))
        for candidate in (windows_tools_dir() / folder / exe,
                          _user_tools_dir() / folder / exe,
                          _user_tools_dir() / exe):
            if candidate.exists():
                return str(candidate)
        found = shutil.which(exe) or shutil.which(name)
        return found
    for candidate in (_user_tools_dir() / name,):
        if candidate.exists():
            return str(candidate)
    return shutil.which(name)


# ---------------------------------------------------------------------------
# Processes
# ---------------------------------------------------------------------------
CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
DETACHED_PROCESS = 0x00000008


def hidden_popen_kwargs():
    """Extra Popen kwargs so backends never flash a console window on Windows."""
    if IS_WINDOWS:
        si = subprocess.STARTUPINFO()
        si.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        si.wShowWindow = 0  # SW_HIDE
        return {"creationflags": CREATE_NO_WINDOW, "startupinfo": si}
    return {}


def detached_popen_kwargs():
    """Popen kwargs for a process that should outlive its launcher."""
    if IS_WINDOWS:
        return {"creationflags": CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS, "close_fds": True}
    return {"start_new_session": True}


if IS_WINDOWS:
    _PROCESS_SUSPEND_RESUME = 0x0800
    _kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _ntdll = ctypes.WinDLL("ntdll")
    _kernel32.OpenProcess.restype = ctypes.c_void_p
    _kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
    _kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    _ntdll.NtSuspendProcess.argtypes = [ctypes.c_void_p]
    _ntdll.NtResumeProcess.argtypes = [ctypes.c_void_p]

    def _nt_call(pid, func):
        handle = _kernel32.OpenProcess(_PROCESS_SUSPEND_RESUME, False, int(pid))
        if not handle:
            return False
        try:
            return func(handle) == 0
        finally:
            _kernel32.CloseHandle(handle)


def suspend_process(proc):
    """Pause a backend. Works for Popen objects and PcmPipeProcess."""
    if proc is None:
        return False
    if hasattr(proc, "pause"):
        proc.pause()
        return True
    try:
        if proc.poll() is not None:
            return False
    except Exception:
        pass
    try:
        if IS_WINDOWS:
            return _nt_call(proc.pid, _ntdll.NtSuspendProcess)
        os.kill(proc.pid, signal.SIGSTOP)
        return True
    except Exception:
        return False


def resume_process(proc):
    if proc is None:
        return False
    if hasattr(proc, "resume"):
        proc.resume()
        return True
    try:
        if IS_WINDOWS:
            return _nt_call(proc.pid, _ntdll.NtResumeProcess)
        os.kill(proc.pid, signal.SIGCONT)
        return True
    except Exception:
        return False


try:
    import numpy as _np
except Exception:  # numpy is optional; fall back to the array module
    _np = None


def apply_gain(pcm, gain):
    """Scale signed 16-bit little-endian PCM bytes by gain, with clipping.

    Uses numpy when installed (tens of times faster than a Python loop, which
    matters for players that do volume in-process), otherwise array.array.
    gain may also be a sequence/array of per-frame gains (for fades), given
    as one value per *sample frame*; channels are then inferred from length.
    """
    if not pcm:
        return pcm
    scalar = isinstance(gain, (int, float))
    if scalar and gain == 1.0:
        return pcm
    usable = len(pcm) - (len(pcm) % 2)
    if _np is not None:
        a = _np.frombuffer(pcm[:usable], dtype="<i2").astype(_np.float32)
        if scalar:
            a *= gain
        else:
            g = _np.asarray(gain, dtype=_np.float32)
            ch = max(1, len(a) // max(1, len(g)))
            a *= _np.repeat(g, ch)[: len(a)]
        return _np.clip(a, -32768, 32767).astype("<i2").tobytes()
    samples = array("h")
    samples.frombytes(pcm[:usable])
    if sys.byteorder != "little":
        samples.byteswap()
    if scalar:
        out = array("h", (max(-32768, min(32767, int(v * gain))) for v in samples))
    else:
        ch = max(1, len(samples) // max(1, len(gain)))
        out = array("h", (max(-32768, min(32767, int(v * gain[i // ch]))) for i, v in enumerate(samples)))
    if sys.byteorder != "little":
        out.byteswap()
    return out.tobytes()


def windows_safe_path(path):
    """A path the bundled C decoders can open on Windows.

    They use narrow (ANSI) fopen, so names with characters outside the system
    code page (e.g. Japanese on an English install) can't be opened. Return
    the 8.3 short name in that case (all-ASCII, same file). Elsewhere, or if
    short names are disabled on that drive, the path is returned unchanged.
    """
    path = str(path)
    if not IS_WINDOWS:
        return path
    try:
        path.encode("mbcs", errors="strict")
        return path
    except (UnicodeEncodeError, LookupError):
        pass
    try:
        from ctypes import wintypes
        get_short = ctypes.windll.kernel32.GetShortPathNameW
        get_short.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
        get_short.restype = wintypes.DWORD
        size = get_short(path, None, 0)
        if size:
            buf = ctypes.create_unicode_buffer(size)
            if get_short(path, buf, size):
                short = buf.value
                short.encode("mbcs", errors="strict")
                return short
    except Exception:
        pass
    return path


def set_process_volume(proc, fraction):
    """Set a backend's volume (0.0-1.5).

    Returns True when handled here (PcmPipeProcess anywhere, or any process on
    Windows via its audio session). Returns False on Linux for normal
    processes, so callers keep using their wpctl/pactl code.
    """
    if proc is None:
        return False
    if hasattr(proc, "set_volume"):
        proc.set_volume(fraction)
        return True
    if not IS_WINDOWS:
        return False
    if not _set_session_volume(proc.pid, fraction):
        # Session not created yet (process just started): keep trying briefly
        apply_volume_when_ready(proc, fraction)
    return True


def apply_volume_when_ready(proc, fraction, attempts=20, delay=0.15):
    """Windows: a process's audio session appears shortly after it starts, so
    retry setting its volume for a few seconds (run in a background thread)."""
    def worker():
        for _ in range(attempts):
            if proc is None or proc.poll() is not None:
                return
            if _set_session_volume(proc.pid, fraction):
                return
            time.sleep(delay)
    threading.Thread(target=worker, daemon=True).start()


def _set_session_volume(pid, fraction):
    """True once a Windows audio session for pid was found and set."""
    try:
        import comtypes
        from pycaw.pycaw import AudioUtilities
        try:
            comtypes.CoInitialize()
        except OSError:
            pass
        found = False
        for session in AudioUtilities.GetAllSessions():
            if session.Process and session.Process.pid == pid:
                session.SimpleAudioVolume.SetMasterVolume(max(0.0, min(1.0, float(fraction))), None)
                found = True
        return found
    except Exception:
        return False


def open_path(path):
    """Open a file or folder with the desktop's default handler."""
    try:
        if IS_WINDOWS:
            os.startfile(str(path))  # noqa: S606 - opening the user's own folder
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
        return True
    except Exception as e:
        print(f"Could not open {path}: {e}")
        return False


# ---------------------------------------------------------------------------
# PCM pipe playback
# ---------------------------------------------------------------------------
def format_hms(seconds, decimals=2):
    seconds = max(0.0, float(seconds))
    m, s = divmod(seconds, 60)
    h, m = divmod(int(m), 60)
    if h:
        return f"{h:02d}:{m:02d}:{s:0{3 + decimals}.{decimals}f}"
    return f"{m:02d}:{s:0{3 + decimals}.{decimals}f}"


class PcmPipeProcess:
    """Play a decoder's audio output and look like a subprocess.Popen.

    decoder_cmd writes either a WAV stream (wav=True, e.g. vgmstream-cli -p)
    or raw signed 16-bit little-endian PCM (wav=False) to stdout. Audio goes
    out through sounddevice; volume and pause are applied here, in-process.

    The player reads progress from .stdout (text, line based) exactly like it
    would from its Linux backend: progress_line(position_s, total_s) returns
    the line to emit (or None), called ~5 times a second. info_lines are
    emitted first (e.g. metadata).

    Supports: pid, stdout, poll(), wait(), terminate(), kill(), pause(),
    resume(), set_volume().
    """

    CHUNK_FRAMES = 2048

    def __init__(self, decoder_cmd, progress_line=None, wav=True, sample_rate=44100,
                 channels=2, total_seconds=None, start_offset=0.0, volume=1.0,
                 info_lines=(), cwd=None, forward_stderr=False):
        self._progress_line = progress_line
        self._wav = wav
        self.sample_rate = sample_rate
        self.channels = channels
        self.total_seconds = total_seconds
        self.start_offset = float(start_offset or 0.0)
        self._volume = float(volume)
        self._fade = None
        self._frames_played = 0
        self._paused = threading.Event()
        self._stopped = threading.Event()
        self.returncode = None
        self._lock = threading.Lock()

        r, w = os.pipe()
        self.stdout = io.open(r, "r", encoding="utf-8", errors="replace", newline=None)
        self._out = io.open(w, "w", encoding="utf-8", buffering=1)

        self._decoder = subprocess.Popen(
            decoder_cmd, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE if forward_stderr else subprocess.DEVNULL,
            stdin=subprocess.DEVNULL, cwd=cwd, **hidden_popen_kwargs()
        )
        self.pid = self._decoder.pid
        for line in info_lines:
            self._emit(line)
        if forward_stderr:
            # Decoder's text output (track info) goes to .stdout like a normal CLI player
            def pump():
                for raw in iter(self._decoder.stderr.readline, b""):
                    self._emit(raw.decode("utf-8", "replace"))
            threading.Thread(target=pump, daemon=True).start()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    # --- Popen-like API -----------------------------------------------------
    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self._thread.join(timeout)
        if self._thread.is_alive():
            raise subprocess.TimeoutExpired("PcmPipeProcess", timeout)
        return self.returncode

    def terminate(self):
        self._stopped.set()
        self._paused.clear()
        try:
            self._decoder.kill()
        except Exception:
            pass

    kill = terminate

    def send_signal(self, sig):
        if sig == getattr(signal, "SIGSTOP", None):
            self.pause()
        elif sig == getattr(signal, "SIGCONT", None):
            self.resume()
        else:
            self.terminate()

    # --- controls -----------------------------------------------------------
    def pause(self):
        self._paused.set()

    def resume(self):
        self._paused.clear()

    def set_volume(self, fraction):
        with self._lock:
            self._volume = max(0.0, float(fraction))

    def fade_out(self, seconds):
        """Fade to silence over `seconds`, then end (like the decoder finishing)."""
        with self._lock:
            if self._fade is None:
                self._fade = (getattr(self, "_frames_played", 0),
                              max(1, int(float(seconds) * self.sample_rate)))

    # --- internals ----------------------------------------------------------
    def _emit(self, line):
        try:
            self._out.write(line.rstrip("\n") + "\n")
        except Exception:
            pass

    def _read_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self._decoder.stdout.read(n - len(buf))
            if not chunk:
                break
            buf += chunk
        return buf

    def _parse_wav_header(self):
        """Read RIFF chunks up to 'data'. Returns data size in bytes (or None)."""
        riff = self._read_exact(12)
        if len(riff) < 12 or riff[:4] != b"RIFF" or riff[8:12] != b"WAVE":
            raise RuntimeError("decoder did not produce a WAV stream")
        while True:
            hdr = self._read_exact(8)
            if len(hdr) < 8:
                raise RuntimeError("truncated WAV header")
            cid, size = hdr[:4], struct.unpack("<I", hdr[4:])[0]
            if cid == b"fmt ":
                fmt = self._read_exact(size)
                _, channels, rate = struct.unpack("<HHI", fmt[:8])
                bits = struct.unpack("<H", fmt[14:16])[0]
                if bits != 16:
                    raise RuntimeError(f"unsupported WAV bit depth {bits}")
                self.channels, self.sample_rate = channels, rate
            elif cid == b"data":
                return None if size in (0, 0xFFFFFFFF) else size
            else:
                self._read_exact(size + (size & 1))

    def _run(self):
        import sounddevice as sd
        stream = None
        code = 0
        try:
            if self._wav:
                data_size = self._parse_wav_header()
                if data_size and not self.total_seconds:
                    frames = data_size // (2 * self.channels)
                    self.total_seconds = self.start_offset + frames / float(self.sample_rate)

            stream = sd.RawOutputStream(samplerate=self.sample_rate, channels=self.channels,
                                        dtype="int16", blocksize=self.CHUNK_FRAMES)
            stream.start()
            frame_bytes = 2 * self.channels
            chunk_bytes = self.CHUNK_FRAMES * frame_bytes
            frames_played = 0
            last_report = 0.0
            silence = bytes(chunk_bytes)

            while not self._stopped.is_set():
                if self._paused.is_set():
                    stream.write(silence)  # keep the device fed while paused
                    continue
                data = self._read_exact(chunk_bytes)
                if not data:
                    break
                usable = len(data) - (len(data) % frame_bytes)
                data = data[:usable]
                frames = usable // frame_bytes
                with self._lock:
                    vol = self._volume
                    fade = self._fade  # (start_frame, length_frames) or None
                if fade is not None:
                    start, length = fade
                    gains = [vol * max(0.0, 1.0 - (frames_played + i - start) / float(length))
                             for i in range(frames)]
                    data = apply_gain(data, gains)
                    if frames_played + frames >= start + length:
                        break  # faded out completely: end of track
                elif vol != 1.0:
                    data = apply_gain(data, vol)
                stream.write(data)
                frames_played += frames
                self._frames_played = frames_played

                now = time.monotonic()
                if self._progress_line and now - last_report >= 0.2:
                    last_report = now
                    pos = self.start_offset + frames_played / float(self.sample_rate)
                    line = self._progress_line(pos, self.total_seconds)
                    if line:
                        self._emit(line)
        except Exception as e:
            self._emit(f"Error: {e}")
            code = 1
        finally:
            if stream is not None:
                try:
                    if not self._stopped.is_set():
                        stream.stop()  # let the tail of the buffer play out
                    stream.close()
                except Exception:
                    pass
            try:
                self._decoder.kill()
                self._decoder.wait(timeout=2)
            except Exception:
                pass
            if self._stopped.is_set():
                code = -15
            elif code == 0 and self._decoder.returncode not in (0, None, -9, 1):
                code = self._decoder.returncode
            self.returncode = code
            try:
                self._out.close()
            except Exception:
                pass


class SoundDeviceSink:
    """Stands in for an `aplay -t raw -f S16_LE` process on Windows.

    Players that stream raw PCM into aplay's stdin can write into .stdin of
    this object instead; audio goes out through sounddevice.

    Only the sink's own thread ever touches the PortAudio stream. On Windows,
    stopping/closing a stream from one thread while another is blocked in
    write() crashes the process, so writers just hand chunks over through a
    small queue. While paused (or starved) the device is fed silence, like
    PcmPipeProcess does.
    """

    _EOF = object()
    BLOCK_FRAMES = 2048

    class _Writer:
        def __init__(self, sink):
            self._sink = sink

        def write(self, data):
            sink = self._sink
            while True:
                if sink._aborted.is_set() or sink._closing:
                    raise BrokenPipeError("sink closed")
                try:
                    sink._queue.put(bytes(data), timeout=0.1)
                    return len(data)
                except queue.Full:
                    continue

        def flush(self):
            pass

        def close(self):
            self._sink._close(drain=True)

    def __init__(self, sample_rate=44100, channels=2):
        self.sample_rate = sample_rate
        self.channels = channels
        self._queue = queue.Queue(maxsize=4)
        self._aborted = threading.Event()
        self._paused = threading.Event()
        self._closing = False
        self._started = threading.Event()
        self._error = None
        self.stdin = self._Writer(self)
        self.pid = os.getpid()
        self.returncode = None
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        self._started.wait(5)
        if self._error is not None:
            raise self._error

    def _run(self):
        stream = None
        try:
            import sounddevice as sd
            stream = sd.RawOutputStream(samplerate=self.sample_rate, channels=self.channels,
                                        dtype="int16", blocksize=self.BLOCK_FRAMES)
            stream.start()
        except Exception as e:
            self._error = e
            self._started.set()
            self.returncode = 1
            return
        self._started.set()
        silence = bytes(self.BLOCK_FRAMES * 2 * self.channels)
        try:
            while not self._aborted.is_set():
                if self._paused.is_set():
                    stream.write(silence)
                    continue
                try:
                    data = self._queue.get(timeout=0.02)
                except queue.Empty:
                    if self._closing:
                        break
                    stream.write(silence)  # starved: keep the device fed
                    continue
                if data is self._EOF:
                    break
                stream.write(data)
        except Exception:
            pass
        finally:
            try:
                if self._aborted.is_set():
                    stream.abort()
                else:
                    stream.stop()  # let the tail of the buffer play out
                stream.close()
            except Exception:
                pass
            if self.returncode is None:
                self.returncode = 0

    def _close(self, drain=True):
        if not drain:
            self._aborted.set()
            self._paused.clear()
            return
        if self._closing:
            return
        self._closing = True
        self._paused.clear()
        try:
            self._queue.put(self._EOF, timeout=1)
        except queue.Full:
            pass

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        self._close(drain=True)
        self._thread.join(timeout)
        if self._thread.is_alive():
            raise subprocess.TimeoutExpired("SoundDeviceSink", timeout)
        return self.returncode

    def terminate(self):
        self._close(drain=False)
        self._thread.join(2)

    kill = terminate

    def pause(self):
        self._paused.set()

    def resume(self):
        self._paused.clear()


def mpv_ipc_path(tag):
    """Where mpv's --input-ipc-server should listen for this player."""
    if IS_WINDOWS:
        return r"\\.\pipe\mpv-joshw-" + tag
    import tempfile
    return os.path.join(tempfile.gettempdir(), f"mpv-python-{tag}.sock")


class NamedPipeSocket:
    """Socket-like client for mpv's Windows IPC named pipe.

    Implements the subset of socket.socket the mpv player uses:
    connect(), sendall(), recv() (honouring settimeout), settimeout(), close().
    """

    def __init__(self):
        self._f = None
        self._timeout = None

    def connect(self, path):
        self._f = open(path, "r+b", buffering=0)

    def settimeout(self, timeout):
        self._timeout = timeout

    def sendall(self, data):
        if self._f is None:
            raise OSError("pipe not connected")
        self._f.write(data)

    def _available(self):
        import msvcrt
        handle = msvcrt.get_osfhandle(self._f.fileno())
        avail = ctypes.c_uint32(0)
        ok = ctypes.windll.kernel32.PeekNamedPipe(ctypes.c_void_p(handle), None, 0, None,
                                                   ctypes.byref(avail), None)
        if not ok:
            raise OSError("mpv pipe closed")
        return avail.value

    def recv(self, bufsize):
        import socket
        if self._f is None:
            raise OSError("pipe not connected")
        deadline = None if self._timeout is None else time.monotonic() + self._timeout
        while True:
            avail = self._available()
            if avail:
                return self._f.read(min(bufsize, avail))
            if deadline is not None and time.monotonic() >= deadline:
                raise socket.timeout("timed out")
            time.sleep(0.005)

    def close(self):
        if self._f is not None:
            try:
                self._f.close()
            except Exception:
                pass
            self._f = None


# ---------------------------------------------------------------------------
# AHX / HivelyTracker (.ahx, .hvl): rendered to WAV by hvl2wav, then played
# or exported like any WAV (mpv/FFmpeg can't decode these synth formats).
# ---------------------------------------------------------------------------
HVL_EXTS = {".ahx", ".hvl"}


class HivelyError(Exception):
    pass


def hvl_to_wav(src, out_wav=None, max_seconds=None):
    """Render an .ahx/.hvl file to a 44.1 kHz stereo WAV and return its path.

    Without out_wav the result is cached in the temp folder (keyed by the
    file's path, size and mtime), so replaying or looping doesn't re-render.
    max_seconds overrides the song-end detection (hvl2wav stops at the song's
    end by itself, or after 10 minutes for songs that never end).
    """
    import hashlib
    import tempfile
    exe = find_tool("hvl2wav")
    if not exe:
        raise HivelyError("hvl2wav was not found. Run Options > Install Dependencies "
                          "to build it (it plays .ahx and .hvl files).")
    src = os.path.abspath(str(src))
    if out_wav is None:
        try:
            from config import get_temp_directory
            cache_dir = os.path.join(get_temp_directory(), ".hivelytracker")
        except Exception:
            cache_dir = os.path.join(tempfile.gettempdir(), "joshw_hivelytracker")
        os.makedirs(cache_dir, exist_ok=True)
        st = os.stat(src)
        key = hashlib.sha1(f"{src}|{st.st_size}|{st.st_mtime_ns}|{max_seconds}".encode("utf-8")).hexdigest()[:16]
        out_wav = os.path.join(cache_dir, key + ".wav")  # ASCII name: hvl2wav uses narrow fopen
        if os.path.exists(out_wav) and os.path.getsize(out_wav) > 44:
            return out_wav
    out_wav = str(out_wav)
    cmd = [exe]
    if max_seconds:
        total = max(1, int(round(float(max_seconds))))
        cmd.append(f"-t{total // 60}:{total % 60:02d}")
    cmd += [f"-o{windows_safe_path(os.path.dirname(os.path.abspath(out_wav)))}{os.sep}{os.path.basename(out_wav)}",
            windows_safe_path(src)]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180,
                                **hidden_popen_kwargs())
    except (OSError, subprocess.SubprocessError) as e:
        raise HivelyError(f"hvl2wav failed: {e}")
    if not os.path.exists(out_wav) or os.path.getsize(out_wav) <= 44:
        output = result.stdout.decode("utf-8", "replace").strip().splitlines()
        raise HivelyError("hvl2wav could not render this file" + (f": {output[-1]}" if output else "."))
    return out_wav
