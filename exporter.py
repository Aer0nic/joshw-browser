"""
exporter.py - render a track to WAV or FLAC with the same backend that plays it.

    export_track(src, out_path, fmt="wav", progress=None, stop_event=None,
                 subtrack=None, duration_hint=None)

src may carry a GME subtrack as "file.nsf#3". progress(fraction_or_None, text)
is called from the exporting thread. Per-track length/fade overrides
(library.get_override) are honoured: backends that can play longer are told
so, and a final pass trims + fades to the exact length.

Backends:
  GME formats (NSF, SPC, GBS, VGM/VGZ, ...)  libgme, in-process
  PSF / PSF2                                 AOSDK, in-process
  GSF / 2SF / USF                            gsfdec / 2sfdec / usfdec (WAV on stdout)
                                             Linux: lazyusf can also write USF itself
  vgmstream formats                          vgmstream-cli -o
  SID                                        sidplayfp -w
  MIDI / RMF / XMF ...                       playbae -o (with the player's soundfont)
  everything else (MP3, OGG, modules, ...)   mpv --ao=pcm
"""
import ctypes
import os
import shutil
import struct
import subprocess
import tempfile
import threading
import wave
from array import array

from config import get_binary_path, get_cache_directory, resource_path
from library import get_override
from platform_support import IS_WINDOWS, apply_gain, find_tool, hidden_popen_kwargs, windows_safe_path

GME_EXTS = {".nsf", ".nsfe", ".spc", ".gbs", ".hes", ".kss", ".vgm", ".vgz", ".ay", ".sap", ".gym"}
PSF_EXTS = {".psf", ".minipsf", ".psf2", ".minipsf2"}
DECODER_EXTS = {".gsf": "gsfdec", ".minigsf": "gsfdec", ".2sf": "2sfdec", ".mini2sf": "2sfdec",
                ".usf": "usfdec", ".miniusf": "usfdec"}
NEOBAE_EXTS = {".mid", ".midi", ".kar", ".rmf", ".rmi", ".xmf", ".mxmf", ".zmf", ".mthc"}
DEFAULT_LENGTH = 180.0  # untagged tracks: same 3 minutes the players use
DEFAULT_FADE = 10.0


class ExportCancelled(Exception):
    pass


class ExportError(Exception):
    pass


def can_export(src):
    ext = os.path.splitext(str(src).rsplit("#", 1)[0])[1].lower()
    return ext not in (".xmi", ".m3u")


def export_track(src, out_path, fmt="wav", progress=None, stop_event=None, subtrack=None,
                 duration_hint=None):
    src = str(src)
    track_id = src  # overrides are stored per "file#subtrack" for subtracks
    if "#" in src and not os.path.exists(src):
        base, sub = src.rsplit("#", 1)
        if sub.isdigit() and os.path.exists(base):
            src, subtrack = base, int(sub)
    fmt = fmt.lower()
    if fmt not in ("wav", "flac"):
        raise ExportError(f"Unsupported export format: {fmt}")
    if not os.path.exists(src):
        raise ExportError(f"File not found: {src}")
    progress = progress or (lambda f, t: None)
    stop_event = stop_event or threading.Event()
    override = get_override(track_id)

    out_path = str(out_path)
    work = tempfile.mkdtemp(prefix="joshw_export_")
    raw_wav = os.path.join(work, "render.wav")
    try:
        ext = os.path.splitext(src)[1].lower()
        progress(None, "Rendering...")
        if ext in GME_EXTS:
            _render_gme(src, raw_wav, subtrack or 0, override, progress, stop_event)
        elif ext in PSF_EXTS:
            _render_psf(src, raw_wav, override, progress, stop_event)
        elif ext in DECODER_EXTS:
            _render_psf_family(src, raw_wav, DECODER_EXTS[ext], override, progress, stop_event)
        elif ext == ".sid":
            _render_sid(src, raw_wav, subtrack, override, duration_hint, progress, stop_event)
        elif ext in NEOBAE_EXTS:
            _render_neobae(src, raw_wav, override, progress, stop_event)
        elif _is_vgmstream(ext):
            _render_vgmstream(src, raw_wav, subtrack, progress, stop_event)
        else:
            _render_mpv(src, raw_wav, override, progress, stop_event)
        if stop_event.is_set():
            raise ExportCancelled()
        progress(None, "Writing file...")
        length, fade = override if override else (None, 0.0)
        _finalize(raw_wav, out_path, fmt, length, fade, stop_event)
        progress(1.0, "Done")
        return out_path
    finally:
        shutil.rmtree(work, ignore_errors=True)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _is_vgmstream(ext):
    try:
        from player_launcher import VGMSTREAM_EXTS
        return ext in VGMSTREAM_EXTS
    except Exception:
        return False


def _check_stop(stop_event):
    if stop_event.is_set():
        raise ExportCancelled()


def _run_cli(cmd, stop_event, progress, expect_file=None, expect_seconds=None, cwd=None):
    """Run a backend that writes a file itself; report progress from its size."""
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, cwd=cwd,
                            **hidden_popen_kwargs())
    err = []
    t = threading.Thread(target=lambda: err.append(proc.stderr.read()), daemon=True)
    t.start()
    try:
        while proc.poll() is None:
            if stop_event.wait(0.3):
                proc.kill()
                raise ExportCancelled()
            if expect_file and expect_seconds and os.path.exists(expect_file):
                done = os.path.getsize(expect_file) / (44100 * 4.0)
                progress(min(0.99, done / expect_seconds), "Rendering...")
    finally:
        if proc.poll() is None:
            proc.kill()
    t.join(1)
    if proc.returncode not in (0, None) and not (expect_file and os.path.exists(expect_file)
                                                 and os.path.getsize(expect_file) > 44):
        tail = (err[0] or b"").decode("utf-8", "replace").strip().splitlines()[-3:] if err else []
        raise ExportError(f"{os.path.basename(cmd[0])} failed (code {proc.returncode})"
                          + (": " + " / ".join(tail) if tail else ""))


def _wav_writer(path, rate=44100, channels=2):
    w = wave.open(path, "wb")
    w.setnchannels(channels)
    w.setsampwidth(2)
    w.setframerate(rate)
    return w


# ---------------------------------------------------------------------------
# in-process renderers
# ---------------------------------------------------------------------------
def _render_gme(src, out_wav, track, override, progress, stop_event):
    from gme_player import GMEContext
    with GMEContext(src) as ctx:
        ctx.load_track(track, loop_forever=False, loop_enabled=False, loop_count=1)
        rate = ctx.sample_rate
        if override:
            length, fade = override
        else:
            length = ctx.base_length_ms / 1000.0
            fade_ms = ctx.fade_ms
            fade = 5.0 if (fade_ms <= 0 or fade_ms == ctx.base_length_ms) else fade_ms / 1000.0
        total = int((length + fade) * rate)
        fade_start, fade_len = int(length * rate), max(1, int(fade * rate))
        done = 0
        with _wav_writer(out_wav, rate) as w:
            while done < total:
                _check_stop(stop_event)
                n = min(4096, total - done)
                buf = (ctypes.c_short * (n * 2))()
                if not ctx.render(n, buf):
                    break
                pcm = bytes(buf)
                if done + n > fade_start:
                    pcm = apply_gain(pcm, [max(0.0, min(1.0, 1 - (done + i - fade_start) / fade_len))
                                           for i in range(n)])
                w.writeframes(pcm)
                done += n
                if done % (rate * 2) < n:
                    progress(done / total, "Rendering...")


def _render_psf(src, out_wav, override, progress, stop_event):
    from psf_ctypes import AOSDKContext
    with AOSDKContext() as ctx:
        ctx.open(windows_safe_path(src))
        length = ctx.get_duration()
        fade = ctx.get_fade() if length else DEFAULT_FADE
        if override:
            length, fade = override
        length = length or DEFAULT_LENGTH
        total = int((length + fade) * 44100)
        done = 0
        with _wav_writer(out_wav) as w:
            while done < total:
                _check_stop(stop_event)
                n, pcm = ctx.render(min(4096, total - done))
                if n <= 0:
                    break
                w.writeframes(pcm)
                done += n
                if ctx.is_done():
                    break
                if done % 88200 < n:
                    progress(done / total, "Rendering...")


# ---------------------------------------------------------------------------
# command-line backends
# ---------------------------------------------------------------------------
def _decoder_path(name):
    """Windows: bundled decoder. Linux: natively built copy (Install Dependencies)."""
    return find_tool(name)


def _render_psf_family(src, out_wav, decoder, override, progress, stop_event):
    exe = _decoder_path(decoder)
    if not exe:
        if decoder == "usfdec" and not IS_WINDOWS:
            return _render_lazyusf(src, out_wav, override, progress, stop_event)
        raise ExportError(f"The {decoder} decoder isn't installed. Run Options > Install Dependencies.")
    cmd = [exe]
    if override:
        length, fade = override
        if decoder == "gsfdec":
            cmd += ["-i", "-t", str(int((length + fade) * 1000))]
        else:
            cmd += ["--play-time", f"{length:.3f}", "--fade-time", f"{fade:.3f}"]
    cmd.append(windows_safe_path(src))
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                            **hidden_popen_kwargs())
    try:
        header = proc.stdout.read(44)
        if len(header) < 44 or header[:4] != b"RIFF":
            raise ExportError(f"{decoder} could not decode this file")
        channels, rate = struct.unpack("<HI", header[22:28])
        data_size = struct.unpack("<I", header[40:44])[0]
        total = None if data_size in (0, 0xFFFFFFFF) else data_size // (2 * channels)
        if total is None and override:
            total = int(sum(override) * rate)
        done = 0
        with _wav_writer(out_wav, rate, channels) as w:
            while True:
                _check_stop(stop_event)
                chunk = proc.stdout.read(65536)
                if not chunk:
                    break
                w.writeframes(chunk[: len(chunk) - len(chunk) % (2 * channels)])
                done += len(chunk) // (2 * channels)
                if total:
                    progress(min(0.99, done / total), "Rendering...")
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()


def _render_lazyusf(src, out_wav, override, progress, stop_event):
    """Linux fallback: lazyusf writes .au itself; convert to WAV."""
    lazyusf = str(get_binary_path("lazyusf"))
    work = os.path.dirname(out_wav)
    au = os.path.join(work, "lazyusf_out.au")
    cmd = [lazyusf, "-o", au[:-3]]
    if override:
        cmd += ["--play-time", str(int(override[0])), "--fade-time", str(int(override[1])), "-f", "2"]
    cmd.append(src)
    _run_cli(cmd, stop_event, progress)
    if not os.path.exists(au):
        cand = [f for f in os.listdir(work) if f.endswith(".au")]
        if not cand:
            raise ExportError("lazyusf produced no output")
        au = os.path.join(work, cand[0])
    _au_to_wav(au, out_wav)


def _au_to_wav(au, out_wav):
    with open(au, "rb") as f:
        hdr = f.read(24)
        magic, offset, size, enc, rate, ch = struct.unpack(">6I", hdr)
        if magic != 0x2E736E64 or enc != 3:
            raise ExportError("unexpected .au format from lazyusf")
        f.seek(offset)
        with _wav_writer(out_wav, rate, ch) as w:
            while True:
                data = f.read(65536)
                if not data:
                    break
                samples = array("h")
                samples.frombytes(data[: len(data) - len(data) % 2])
                samples.byteswap()  # .au is big-endian, WAV little-endian
                w.writeframes(samples.tobytes())


def _render_sid(src, out_wav, subtrack, override, duration_hint, progress, stop_event):
    sid = str(get_binary_path("sidplayfp"))
    seconds = override[0] + override[1] if override else (duration_hint or DEFAULT_LENGTH)
    m, s = divmod(int(round(seconds)), 60)
    cmd = [sid, f"-w{out_wav}", f"-t{m}:{s:02d}"]
    if subtrack:
        cmd.append(f"-o{int(subtrack)}")
    cmd.append(windows_safe_path(src))
    _run_cli(cmd, stop_event, progress, expect_file=out_wav, expect_seconds=seconds)


def _render_neobae(src, out_wav, override, progress, stop_event):
    exe = str(get_binary_path("playbae"))
    cmd = [exe, "-q", "-o", out_wav]
    ext = os.path.splitext(src)[1].lower()
    if ext in {".mid", ".midi", ".kar", ".xmf", ".mxmf", ".mthc"}:
        try:
            from neobae_player import get_saved_soundfont
            sf = get_saved_soundfont()
        except Exception:
            sf = None
        if sf and sf != "(None)":
            for cand in (os.path.join(get_cache_directory(), sf), str(resource_path(f"soundfonts/{sf}"))):
                if os.path.exists(cand):
                    cmd += ["-p", cand]
                    break
    if override:
        cmd += ["-t", str(int(override[0] + override[1]))]
    cmd += ["-f", windows_safe_path(src)]
    _run_cli(cmd, stop_event, progress)


def _render_vgmstream(src, out_wav, subtrack, progress, stop_event):
    exe = find_tool("vgmstream-cli") if IS_WINDOWS else (shutil.which("vgmstream-cli") or
                                                          str(get_binary_path("vgmstream-cli")))
    if not exe or not (os.path.exists(exe) or shutil.which(exe)):
        raise ExportError("vgmstream-cli not found")
    cmd = [exe, "-o", out_wav]
    if subtrack:
        cmd += ["-s", str(int(subtrack))]
    cmd.append(windows_safe_path(src))
    _run_cli(cmd, stop_event, progress)


def _render_mpv(src, out_wav, override, progress, stop_event):
    mpv = find_tool("mpv") if IS_WINDOWS else shutil.which("mpv")
    if not mpv:
        raise ExportError("mpv not found")
    cmd = [mpv, "--no-config", "--no-video", "--really-quiet", "--ao=pcm",
           f"--ao-pcm-file={out_wav}", "--ao-pcm-waveheader=yes",
           "--audio-format=s16", "--audio-samplerate=44100", "--audio-channels=stereo"]
    if override:
        cmd.append(f"--end={override[0] + override[1]:.3f}")
    cmd.append(src)
    _run_cli(cmd, stop_event, progress)


# ---------------------------------------------------------------------------
# final pass: trim / fade, WAV or FLAC
# ---------------------------------------------------------------------------
def _finalize(raw_wav, out_path, fmt, length, fade, stop_event):
    if not os.path.exists(raw_wav) or os.path.getsize(raw_wav) <= 44:
        raise ExportError("The backend produced no audio")
    with wave.open(raw_wav, "rb") as r:
        rate, ch, width = r.getframerate(), r.getnchannels(), r.getsampwidth()
        if width != 2:
            raise ExportError(f"Unexpected sample width {width}")
        n_frames = r.getnframes()
        if n_frames <= 0 or n_frames >= 0x7FFFFFFF // (2 * ch):
            # streamed WAVs may carry a bogus size: count from the file size
            n_frames = (os.path.getsize(raw_wav) - 44) // (2 * ch)
        end = n_frames
        fade_start = fade_len = None
        if length is not None:
            end = min(n_frames, int((length + fade) * rate))
            if fade > 0:
                fade_start, fade_len = int(length * rate), max(1, int(fade * rate))
        tmp_out = out_path + ".part"
        if fmt == "flac":
            import soundfile as sf
            writer = sf.SoundFile(tmp_out, "w", samplerate=rate, channels=ch, subtype="PCM_16", format="FLAC")
            write = lambda pcm: writer.buffer_write(pcm, dtype="int16")
            close = writer.close
        else:
            writer = _wav_writer(tmp_out, rate, ch)
            write, close = writer.writeframes, writer.close
        try:
            pos = 0
            while pos < end:
                _check_stop(stop_event)
                n = min(65536, end - pos)
                pcm = r.readframes(n)
                if not pcm:
                    break
                n = len(pcm) // (2 * ch)
                if fade_start is not None and pos + n > fade_start:
                    pcm = apply_gain(pcm, [max(0.0, min(1.0, 1 - (pos + i - fade_start) / fade_len))
                                           for i in range(n)])
                write(pcm)
                pos += n
        except BaseException:
            close()
            try:
                os.remove(tmp_out)
            except OSError:
                pass
            raise
        close()
    os.replace(tmp_out, out_path)
