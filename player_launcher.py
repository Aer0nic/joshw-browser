"""
Launching players as standalone processes.

Every player runs in its own Python process with its own root window
(tb.Window), so it is not a child of the browser and keeps playing if the
browser closes. This module holds:

  * PLAYER_SCRIPTS / player_script_for() - extension -> player script routing
  * launch_player()                      - spawn a player process (with playlist)
  * parse_player_args() / run_player()   - the matching __main__ side
  * load_saved_theme() / attach_settings_sync() - theme & font sync across processes
"""
import json
import os
import subprocess
import sys
import tempfile

from config import get_cache_directory, resource_path

# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------
NEOBAE_EXTS = {".mid", ".midi", ".kar", ".rmi", ".rmf", ".zmf", ".xmf", ".mxmf", ".mthc"}
VGMSTREAM_EXTS = {".ast", ".dsp", ".brstm", ".rstm", ".bfsar", ".bfwav", ".adp", ".str",
                  ".thp", ".was", ".h4m", ".vag", ".wem"}
GME_EXTS = {".nsf", ".nsfe", ".spc", ".gbs", ".hes", ".kss", ".ay", ".sap", ".gym", ".vgm"}
MPV_EXTS = {".mp3", ".ogg", ".flac", ".wav", ".m4a", ".aac", ".opus", ".wma", ".ape", ".wv",
            ".mod", ".s3m", ".xm", ".it", ".mka", ".tta", ".ac3", ".dts", ".aiff", ".aifc",
            ".au", ".ra", ".xma", ".xmd"}


def player_script_for(audio_file):
    """Return the player script name that handles this file's extension."""
    ext = os.path.splitext(audio_file)[1].lower()
    if ext == ".vgz":
        return "arcade_player.py"
    if ext in NEOBAE_EXTS:
        return "neobae_player.py"
    if ext in VGMSTREAM_EXTS:
        return "vgmstream_player.py"
    if ext == ".sid":
        return "sid_player.py"
    if ext in (".usf", ".miniusf"):
        return "usf_player.py"
    if ext in (".gsf", ".minigsf"):
        return "gsf_player.py"
    if ext in (".2sf", ".mini2sf"):
        return "twosf_player.py"
    if ext in (".psf", ".minipsf", ".psf2", ".minipsf2"):
        return "psf_player.py"
    if ext in GME_EXTS:
        return "gme_player.py"
    if ext in MPV_EXTS:
        return "mpv_player.py"
    return "vgmstream_player.py"


# ---------------------------------------------------------------------------
# Launching (browser / player side)
# ---------------------------------------------------------------------------
# script -> (module, window class) for running a player inside a frozen app
PLAYER_CLASSES = {
    "arcade_player.py": ("arcade_player", "ArcadePlayerWindow"),
    "neobae_player.py": ("neobae_player", "PlayBAEPlayerWindow"),
    "vgmstream_player.py": ("vgmstream_player", "VGMStreamPlayerWindow"),
    "sid_player.py": ("sid_player", "SIDPlayerWindow"),
    "usf_player.py": ("usf_player", "USFPlayerWindow"),
    "gsf_player.py": ("gsf_player", "GSFPlayerWindow"),
    "twosf_player.py": ("twosf_player", "TwoSFPlayerWindow"),
    "psf_player.py": ("psf_player", "PSFPlayerWindow"),
    "gme_player.py": ("gme_player", "GMEPlayerWindow"),
    "mpv_player.py": ("mpv_player", "MPVPlayerWindow"),
}


def _player_command(script):
    """Command prefix that starts a player script as its own process.

    * PyInstaller build: the app executable re-launched with --player <script>
      (the .py files are not shipped separately; main.py dispatches).
    * Loose files on Windows: pythonw.exe so no console window appears.
    * Loose files on Linux: the current interpreter.
    """
    if getattr(sys, "frozen", False):
        return [sys.executable, "--player", script]
    python = sys.executable
    if sys.platform == "win32":
        pythonw = os.path.join(os.path.dirname(python), "pythonw.exe")
        if os.path.exists(pythonw):
            python = pythonw
    return [python, str(resource_path(script))]


def run_player_from_main(argv):
    """Entry point used by main.py for `--player <script> <args...>`."""
    script = argv[0] if argv else ""
    if script not in PLAYER_CLASSES:
        print(f"Unknown player: {script}")
        sys.exit(2)
    module_name, class_name = PLAYER_CLASSES[script]
    import importlib
    window_cls = getattr(importlib.import_module(module_name), class_name)
    sys.argv = [script] + list(argv[1:])
    run_player(window_cls)


def launch_player(audio_file, playlist=None, start_index=None, script=None):
    """Start the right player for audio_file as an independent process.

    The playlist is handed over through a temporary JSON file because it can
    be long (and PSF playlists hold dicts rather than plain paths).
    """
    script = script or player_script_for(audio_file)
    cmd = _player_command(script) + [str(audio_file)]

    if playlist:
        fd, playlist_path = tempfile.mkstemp(prefix="player_playlist_", suffix=".json",
                                             dir=get_cache_directory())
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"playlist": list(playlist), "start_index": start_index}, f, ensure_ascii=False)
        cmd += ["--playlist", playlist_path]
    elif start_index is not None:
        cmd += ["--start-index", str(int(start_index))]

    # Detached: closing the browser doesn't take the player down with it
    from platform_support import detached_popen_kwargs
    return subprocess.Popen(cmd, **detached_popen_kwargs())


# ---------------------------------------------------------------------------
# __main__ side (player process)
# ---------------------------------------------------------------------------
def parse_player_args(argv=None):
    """Return (audio_file, playlist, start_index) from a player's command line."""
    args = list(sys.argv[1:] if argv is None else argv)
    audio_file, playlist, start_index = None, None, None

    i = 0
    while i < len(args):
        a = args[i]
        if a == "--playlist" and i + 1 < len(args):
            path = args[i + 1]
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                playlist = data.get("playlist") or None
                if data.get("start_index") is not None:
                    start_index = int(data["start_index"])
            except Exception as e:
                print(f"Could not read playlist file {path}: {e}")
            finally:
                try:
                    os.remove(path)
                except OSError:
                    pass
            i += 2
        elif a == "--start-index" and i + 1 < len(args):
            try:
                start_index = int(args[i + 1])
            except ValueError:
                pass
            i += 2
        else:
            if audio_file is None:
                audio_file = a
            i += 1

    return audio_file, playlist, start_index


def run_player(window_cls, **extra_kwargs):
    """Standard __main__ body: build the player window from argv and run it."""
    audio_file, playlist, start_index = parse_player_args()
    if not audio_file and not playlist:
        print(f"Usage: {os.path.basename(sys.argv[0])} <audio_file> [--playlist file.json] [--start-index N]")
        sys.exit(1)
    if not audio_file:
        first = playlist[0]
        audio_file = first.get("file") if isinstance(first, dict) else first
    app = window_cls(None, audio_file, playlist=playlist, start_index=start_index, **extra_kwargs)
    from player_core import attach_player_core
    attach_player_core(app)
    app.mainloop()


# ---------------------------------------------------------------------------
# Theme / font sync between processes
# ---------------------------------------------------------------------------
THEME_PREVIEW_FILE = "theme_preview.txt"


def _settings_path():
    return os.path.join(get_cache_directory(), "settings.json")


def _preview_path():
    return os.path.join(get_cache_directory(), THEME_PREVIEW_FILE)


def load_saved_theme(default="cosmo"):
    try:
        with open(_settings_path(), "r", encoding="utf-8") as f:
            return json.load(f).get("theme", default) or default
    except Exception:
        return default


def write_theme_preview(theme_name):
    """Browser side: broadcast a temporary (hover) theme to player processes."""
    try:
        with open(_preview_path(), "w", encoding="utf-8") as f:
            f.write(theme_name)
    except Exception:
        pass


def clear_theme_preview():
    try:
        os.remove(_preview_path())
    except OSError:
        pass


def _desired_theme():
    try:
        with open(_preview_path(), "r", encoding="utf-8") as f:
            name = f.read().strip()
            if name:
                return name
    except OSError:
        pass
    return load_saved_theme()


def attach_settings_sync(window, interval_ms=300, on_theme_changed=None, on_font_changed=None):
    """Keep a standalone player's theme (incl. hover previews) and font size in
    step with the browser by polling the shared settings files."""
    try:
        from config import load_saved_font_size
    except Exception:
        load_saved_font_size = None

    state = {"theme": None, "font": load_saved_font_size() if load_saved_font_size else None}
    try:
        state["theme"] = window.style.theme_use()
    except Exception:
        pass

    def tick():
        try:
            if not window.winfo_exists():
                return
        except Exception:
            return

        theme = _desired_theme()
        if theme and theme != state["theme"]:
            try:
                if theme in window.style.theme_names():
                    window.style.theme_use(theme)
                    state["theme"] = theme
                    if on_theme_changed:
                        on_theme_changed(theme)
            except Exception as e:
                print(f"Could not apply theme '{theme}': {e}")
                state["theme"] = theme  # don't retry every tick

        if load_saved_font_size and on_font_changed:
            try:
                size = load_saved_font_size()
                if size != state["font"]:
                    state["font"] = size
                    on_font_changed(size)
            except Exception:
                pass

        window.after(interval_ms, tick)

    window.after(interval_ms, tick)
