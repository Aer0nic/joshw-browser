# joshw-browser

An open-source desktop audio browser and streaming player tailored specifically for exploring and playing raw video game music (VGM) archives hosted across the **joshw.info** repositories (e.g., `psf.joshw.info`, `usf.joshw.info`, `spc.joshw.info`, `gcn.joshw.info`, etc.).

## ⚠️ Important: First-Time Setup Notice

When launching `joshw-browser` for the first time, you **must** complete two setup steps before browsing or playing audio:

### 1. Run "Install Dependencies" (Mandatory via App)

It is **mandatory** to run the application's built-in **"Install Dependencies"** feature on first launch to use the vast amount of features in the program.

While manual `apt` commands install base Linux system libraries, the application's internal installer performs essential setup tasks beyond `sudo apt`:

* Clones, compiles, and builds specialized command-line decoders (`vgmstream123`, `playgsf`, `lazyusf`, `playbae`, `2sfPlay`, and `libaosdk.so`) from source using Git and CMake.

* Configures local binary paths (placing executables and shared libraries inside the local `bin/` directory).

* Verifies audio output drivers and chip format support (USF, GSF, NCSF/2SF, PSF, SPC, MIDI, etc.).

> **How to run:** Click **Install Dependencies** from the Option menu in the application interface upon first launch and let the compilation complete in the terminal window.

### 2. Grab Premade Indexes

To browse and search game music collections instantly without needing to scrape remote HTTP directories live:

1. Click the **Grab Premade Indexes** (or **Fetch/Update Indexes**) button.

2. The app will fetch pre-built database files containing complete catalog structures.

3. Once completed, platform archives and game directories can be searched and navigated instantly.

> **Note on Storage:** The initial dependency compilation process requires up to \~1.5 GB of temporary drive space for source code repositories and build artifacts. Once compilation finishes, temporary build files are cleaned up automatically.

## Features Guide

### 🎵 Audio Streaming & Native Playback

* **Direct Archive Streaming:** Stream tracks on-the-fly directly from `joshw.info` servers without manual extraction.

* **Local Storage Playback:** Support for loading files from local storage for playback.

* **Console Format Support:** Plays native video game sound formats (PSF, PSF2, USF, GSF, 2SF, NCSF, SPC, NSF, SID, MOD, VGM, MIDI, Beatnik RMF, and compressed 7z archives).

* **Player Controls:** Full play, pause, stop, track seeking, and volume control integrated into the main window.

### 🎲 Stream Random Button

* **Random Track Streaming:** Click the **Stream Random** button to pick and stream a random track from across the entire indexed catalog.

* **Filter by Platform:** Select a platform first to limit random playback to a specific console (e.g., Nintendo 64 or PlayStation).

### ⭐ Favorites Management

* **Save Favorite Albums:** Bookmark entire game sets to your Favorites list via Right Click.

* **Quick Access Tab:** Access saved favorites anytime from the dedicated Favorites tab without needing to search or re-navigate the archive tree.

* **Persistent Storage:** Favorites list is automatically saved locally and retained across application sessions.

### 📥 Downloading OSTs

* **File Download:** Save game music directly to your local computer, either one at a time or in bulk.

### 🔍 Fast Offline Search & Indexing

* **Instant Filtering:** Search through thousands of game titles across all supported console platforms using locally cached indexes.

* **Catalog Updates:** Re-fetch premade indexes anytime to stay up-to-date with newly added game dumps on `joshw.info`.

## Minimum Required Files to Run

If running directly from source, the application requires only a minimalist file structure:

```
joshw-browser/
├── main.py              # Main application entry point (Tkinter/ttkbootstrap UI & logic)
└── requirements.txt      # Python library requirements

```

*Note: If you are running the single-file PyInstaller binary release, no Python environment or `.py` source files are required—only the single executable and the compiled binary tools placed in the expected `bin/` directory.*

## Prerequisites & Manual System Building

If you prefer to perform manual installation without using the application's automated **"Install Dependencies"** menu item, follow these steps to install system packages, clone external repositories, apply source code modifications, build decoders from source, and place them into the `bin/` directory inside `joshw-browser`.

### Step 1: Install System Packages

Run the following command on Debian, Ubuntu, Linux Mint, or antiX:

```
sudo apt update && sudo apt install -y git cmake libgme0 build-essential sidplayfp xterm xdotool zlib1g-dev libao-dev libsdl2-dev pkg-config pulseaudio-utils libsndfile-dev libmp3lame-dev libogg-dev libvorbis-dev libopus-dev libopusfile-dev libflac-dev liblzma-dev libxmp-dev mpv ca-certificates python3-tk

```

### Step 2: Create Local `bin/` Directory

In your `joshw-browser` application directory, create a local `bin` folder where external executables and shared libraries must reside:

```
mkdir -p bin

```

### Step 3: Tool-by-Tool Compilation Guide

#### 1. `vgmstream123` (General VGM & Audio Stream Decoder)

```
git clone --recursive https://github.com/vgmstream/vgmstream.git /tmp/vgmstream
cd /tmp/vgmstream
mkdir build && cd build
cmake .. -DBUILD_CLI=ON -DBUILD_VGMSTREAM123=ON
make -j$(nproc)

# Copy output binary
cp cli/vgmstream123 /path/to/joshw-browser/bin/
chmod +x /path/to/joshw-browser/bin/vgmstream123
rm -rf /tmp/vgmstream

```

#### 2. `playgsf` (Game Boy Advance / GSF Player)

`playgsf` handles GSF playback via `libao`.

```
git clone https://github.com/coto/playgsf.git /tmp/playgsf
cd /tmp/playgsf
make -j$(nproc)

# Copy output binary
cp playgsf /path/to/joshw-browser/bin/
chmod +x /path/to/joshw-browser/bin/playgsf
rm -rf /tmp/playgsf

```

#### 3. `lazyusf` (Nintendo 64 / USF Decoder with Source Modifications)

`lazyusf` requires source modifications prior to compilation to allow stdout raw PCM streaming or headless command-line audio output compatible with `joshw-browser` subprocessing.

##### Required Source Modifications (`main.c` / `usf.c`):

1. **Disable Interactive TUI / Keypress Prompt:** Comment out or remove interactive input loops (`getchar()`, `getch()`, or pause statements on track completion).

2. **Standard Output (stdout) PCM Redirection:** Ensure audio buffers are written to `stdout` (`fwrite(buffer, sizeof(short), samples, stdout)`) rather than locking directly to an OSS/ALSA device if streaming mode is triggered.

3. **Non-blocking Exit Signals:** Ensure `SIGTERM` / `SIGINT` signals trigger an immediate clean exit without prompt.

##### Compilation & Installation:

```
git clone https://github.com/manq/lazyusf.git /tmp/lazyusf
cd /tmp/lazyusf

# Apply source code modifications to main.c / Makefile as described above
make -j$(nproc)

# Copy output binary
cp lazyusf /path/to/joshw-browser/bin/
chmod +x /path/to/joshw-browser/bin/lazyusf
rm -rf /tmp/lazyusf

```

#### 4. `2sfPlay` (Nintendo DS / 2SF / NCSF Player)

`2sfPlay` specifically handles Nintendo DS sound format archives (2SF and NCSF).

```
git clone https://github.com/khang2/2sfplay.git /tmp/2sfplay
cd /tmp/2sfplay
make -j$(nproc)

cp 2sfplay /path/to/joshw-browser/bin/2sfPlay
chmod +x /path/to/joshw-browser/bin/2sfPlay
rm -rf /tmp/2sfplay

```

#### 5. `playbae` (Beatnik Audio Engine / MIDI & Interactive Audio Player)

`playbae` utilizes the Beatnik Audio Engine to play formats including **MIDI/KAR**, **RMI**, **RMF**, **ZMF**, **XMF/MXMF**, and **Nokia MTHC**.

```
git clone https://github.com/Audious/playbae.git /tmp/playbae
cd /tmp/playbae
make -j$(nproc)

cp playbae /path/to/joshw-browser/bin/
chmod +x /path/to/joshw-browser/bin/playbae
rm -rf /tmp/playbae

```

#### 6. `libaosdk.so` (Audio Overload SDK Shared Library)

`libaosdk.so` is required for sound format rendering across various retro sound chips (PSF, PSF2, SSF, QSF, etc.).

```
git clone https://github.com/R.Belmont/Audio-Overload-SDK.git /tmp/aosdk
cd /tmp/aosdk

# Build shared library
make -f Makefile.linux SHARED=1 -j$(nproc)

# Copy dynamic shared object file into bin/
cp libaosdk.so /path/to/joshw-browser/bin/
chmod +x /path/to/joshw-browser/bin/libaosdk.so
rm -rf /tmp/aosdk

```

### Step 4: Final Verification of Directory Structure

After building and placing all tools, your local `bin/` folder inside `joshw-browser` should contain:

```
joshw-browser/
├── bin/
│   ├── 2sfPlay          # Nintendo DS 2SF/NCSF Player
│   ├── libaosdk.so      # Audio Overload SDK Dynamic Shared Library
│   ├── lazyusf          # N64 USF Player (Modified)
│   ├── playbae          # Beatnik Engine (MIDI/KAR, RMI, RMF, ZMF, XMF, MTHC)
│   ├── playgsf          # Game Boy Advance GSF Player
│   └── vgmstream123     # General VGM CLI Audio Decoder
├── main.py
└── requirements.txt

```

## Usage

### Running from Source

```
pip install -r requirements.txt
python3 main.py

```

### Running the Single-File Binary

```
chmod +x joshw-browser
./joshw-browser

```

## License

Distributed under the MIT License. See `LICENSE` for more information.
