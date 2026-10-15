# Native PSF and PSF2 Playback Integration

Native Sony PlayStation (PSF) and PlayStation 2 (PSF2) audio playback for JoshW Browser, powered directly by the Audio Overload SDK (AOSDK) emulation cores without converting tracks to intermediate WAV files or relying on external GUI players.

---

## 1. Architecture Overview

```
 ┌─────────────────────────────────────────────────────────────┐
 │                JoshW Browser Tkinter UI                     │
 │          (browser.py & PSFPlayerWindow in players.py)       │
 └──────────────┬──────────────────────────────▲───────────────┘
                │ Control Commands             │ Status/Progress
                │ (play/pause/stop/reset/vol)  │ (thread-safe queue.Queue)
                ▼                              │
 ┌─────────────────────────────────────────────┴───────────────┐
 │        PSFAudioWorker (Dedicated Background Thread)        │
 │              - volume scaling (array.array)                 │
 │              - output streaming via sounddevice             │
 └──────────────┬──────────────────────────────────────────────┘
                │ PCM Render & Control
                ▼
 ┌─────────────────────────────────────────────────────────────┐
 │         psf_ctypes.py (Python ctypes Safe Binding)          │
 │              - Explicit argtypes & restypes                 │
 │              - Strict handle lifecycle & memory safety       │
 │              - Typed exceptions (AOSDKOpenError, etc.)      │
 └──────────────┬──────────────────────────────────────────────┘
                │ C ABI (libaosdk.so)
                ▼
 ┌─────────────────────────────────────────────────────────────┐
 │           aosdk_wrapper.c / aosdk_wrapper.h                 │
 │              - Case-insensitive _lib resolution             │
 │              - Thread safety mutex (pthread_mutex)          │
 │              - Tag & Corlett duration parsing               │
 └──────────────┬──────────────────────────────────────────────┘
                │ Native Emulation Engines
                ▼
 ┌──────────────────────────────┬──────────────────────────────┐
 │     PSF Engine (MIPS PSX)    │    PSF2 Engine (IOP/SPU2)    │
 │       eng_psf/ & peops/      │      eng_psf/ & peops2/      │
 └──────────────────────────────┴──────────────────────────────┘
```

- **Direct Native Emulation**: Audio is decoded in memory directly from PSF/PSF2 container files via simulated MIPS R3000 / IOP processors and SPU / SPU2 sound hardware cores.
- **Audio Output**: Streams directly to the Linux audio device (ALSA/PulseAudio/PipeWire) via `sounddevice.RawOutputStream`.
- **Thread Safety & UI Responsiveness**: Decoding and audio output run on a dedicated background worker thread (`PSFAudioWorker`). All communication between the worker thread and the Tkinter UI is handled through a thread-safe `queue.Queue` and periodic `root.after()` polling. No Tkinter widget or UI method is ever called from the audio thread.

---

## 2. Dependencies

### System Requirements (Linux)
- C Compiler: `gcc` or `clang` (supports C99/C11 and 64-bit architectures)
- Standard Build Tools: `bash`, `make`, `pkg-config`
- Compression Library: `zlib` (`zlib1g-dev` / `libz.so`)
- Math & POSIX Threads: `libm`, `libpthread`
- Audio Backing Libraries: `libportaudio2` / `libasound2` (standard on modern Linux distributions)

### Python Requirements
- Python 3.8+
- `sounddevice` (and `cffi`)
- Standard Python libraries: `ctypes`, `threading`, `queue`, `array`, `tkinter`

---

## 3. Build & Run Commands

### 1. Build the AOSDK Shared Library
The build script automatically detects the CPU architecture (`x86_64`, `aarch64`, etc.), discovers system dependencies, compiles the shared library with optimization (`-O3 -fPIC -shared`), and installs it to `~/.config/joshw/libaosdk.so`:

```bash
./build_aosdk.sh
```

To verify the library:
```bash
ls -lh ~/.config/joshw/libaosdk.so
nm -D ~/.config/joshw/libaosdk.so | grep aosdk_
```

### 2. Run the CLI Smoke Test
A standalone smoke test verifies that native decoding, Corlett metadata extraction, and real audio device streaming work properly without writing any converted audio files:

```bash
# Test PlayStation 1 PSF
./smoke_test_psf.py aosdk/samples/pe.psf --seconds 3.0

# Test PlayStation 2 PSF2
./smoke_test_psf.py aosdk/samples/01.psf2 --seconds 3.0

# Test miniPSF with multi-library dependencies
./smoke_test_psf.py test_tracks/calcolo/C.001_0_0.psf --seconds 2.0
```

Options:
- `--seconds <N>`: Duration to stream to the audio device (default: 3.0 seconds).
- `--volume <factor>`: Software volume multiplier (default: 1.0).

### 3. Run the Automated Unit Test Suite
The test suite validates library loading, invalid paths, unsupported extensions, seek restrictions, metadata extraction, and thread-safe worker lifecycle:

```bash
python3 -m unittest tests/test_psf_player.py -v
```

### 4. Launch the Main Application
```bash
python3 main.py
```
PSF and PSF2 files will automatically open in the `PSFPlayerWindow`.

---

## 4. Supported Formats & Technical Specifications

| Format | Extension | Console | Audio Hardware |
|---|---|---|---|
| **PSF** | `.psf` | Sony PlayStation 1 | Sony SPU (24 channels, ADPCM, reverb) |
| **miniPSF** | `.minipsf`, `.psf` | Sony PlayStation 1 | SPU with external `.psflib` files |
| **PSF2** | `.psf2` | Sony PlayStation 2 | SPU2 (48 channels, IOP core) |
| **miniPSF2** | `.minipsf2`, `.psf2` | Sony PlayStation 2 | SPU2 with external `.psf2lib` files |

### Audio Specifications
- **Sample Rate**: 44,100 Hz (44.1 kHz)
- **Channels**: 2 (Stereo)
- **Format**: Signed 16-bit interleaved PCM (`int16_t` L/R pairs)
- **Frame Rate**:
  - NTSC: 60 Hz (735 stereo samples per frame)
  - PAL: 50 Hz (882 stereo samples per frame, detected via `_refresh=50` tag)

---

## 5. Auxiliary Library Resolution (`_lib`)

A common challenge on Linux is that PSF/PSF2 sets created on Windows reference auxiliary libraries with Windows path separators (`\`) or case mismatches (e.g., `_lib=SOUND\COMMON.PSFLIB` while the file on disk is `sound/common.psflib`).

The C wrapper implements automatic path resolution:
1. Translates backslashes `\` to Unix slashes `/`.
2. Resolves relative paths against the folder containing the active PSF file.
3. If an exact file is not found, performs a case-insensitive directory scan to match files regardless of filesystem case sensitivity.

---

## 6. Limitations & Seeking Behavior

- **Arbitrary Seeking / Scrubbing**:
  Emulated sound formats such as PSF and PSF2 do not store linear PCM or frame-indexed audio. Instead, they execute native MIPS CPU instructions that alter simulated sound chip registers and sound RAM over time.
  Because the emulation state machine cannot be rewound to an arbitrary timestamp without running the entire instruction sequence from the beginning, **arbitrary scrubbing is unsupported**.
- **Seeking to Start (0:00)**:
  Seeking to 0.0 or clicking the **Restart** button resets the MIPS CPU and SPU hardware registers to their initial state, cleanly replaying the track from the start.
- **Reporting**:
  Attempting an arbitrary seek returns `AOSDK_ERROR_SEEK_UNSUPPORTED` in C and raises `AOSDKSeekError` in Python with a clear explanatory message.

---

## 7. Licensing

The Audio Overload SDK combines components under several open-source licenses:

1. **AOSDK Core**:
   - Copyright (c) 2007-2009 R. Belmont and Richard Bannister.
   - Licensed under the **BSD 3-Clause License**.
2. **PSX CPU & MAME Hardware Cores**:
   - Copyright (c) 1997-2008 Nicola Salmoria and the MAME Team.
   - Non-commercial redistribution permitted with source code disclosure.
3. **PeOPS & PeOPS2 SPU Sound Emulators**:
   - Copyright (c) Pete Bernert.
   - Licensed under the **GNU General Public License (GPL)**.
4. **zlib**:
   - Copyright (c) 1995-2004 Jean-loup Gailly and Mark Adler.
   - Licensed under the **zlib License**.

The complete licensing terms and copyright notices are preserved in `aosdk/license.txt`.

