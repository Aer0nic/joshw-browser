# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for Linux and Windows:  pyinstaller main.spec
# On Windows, run `python tools/fetch_windows_deps.py mpv` first so mpv.exe
# (kept out of git, >100 MB) is bundled too.
import sys
from PyInstaller.utils.hooks import collect_all

IS_WINDOWS = sys.platform == "win32"

# assets/ carries icons plus assets/libraries/{libaosdk.so, windows/...}
datas = [('assets', 'assets'), ('LICENSE', '.')]
if not IS_WINDOWS:
    # Install Dependencies builds the WAV decoders from these on Linux
    datas.append(('tools/windows_decoders', 'tools/windows_decoders'))
binaries = []
if not IS_WINDOWS:
    binaries.append(('assets/libraries/libaosdk.so', 'assets/libraries'))

# Players are started as `<app> --player <script>` and imported by name,
# so PyInstaller can't see them: list them explicitly.
player_modules = [
    'arcade_player', 'neobae_player', 'vgmstream_player', 'sid_player', 'usf_player',
    'gsf_player', 'twosf_player', 'psf_player', 'gme_player', 'mpv_player',
    'psf_ctypes', 'players', 'player_launcher', 'platform_support', 'windows_setup',
    'player_core', 'player_ipc', 'media_session', 'library', 'exporter', 'now_playing',
]
hiddenimports = ['PIL._tkinter_finder', 'sounddevice', '_cffi_backend', 'mido',
                 'mido.backends.rtmidi', 'numpy', 'soundfile', 'pystray'] + player_modules
if not IS_WINDOWS:
    hiddenimports += ['jeepney', 'jeepney.io.blocking', 'pystray._xorg', 'pystray._appindicator']
else:
    hiddenimports += ['pystray._win32']
if IS_WINDOWS:
    hiddenimports += ['pycaw', 'pycaw.pycaw', 'comtypes', 'comtypes.stream', 'psutil']

for pkg in ['ttkbootstrap', 'PIL', 'soundfile'] + (['pycaw', 'comtypes'] if IS_WINDOWS else []):
    tmp_ret = collect_all(pkg)
    datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='joshw-browser' if IS_WINDOWS else 'main',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=not IS_WINDOWS,  # UPX-packed exes trip Windows antivirus heuristics
    upx_exclude=[],
    runtime_tmpdir=None,
    console=not IS_WINDOWS,  # no console window behind the GUI on Windows
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='assets/icons/browser.png' if IS_WINDOWS else None,
)
