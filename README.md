# joshw-browser

A desktop browser and player for the video game music archives on **joshw.info** (`psf.joshw.info`, `usf.joshw.info`, `spc.joshw.info`, `gcn.joshw.info` and 30+ more). Browse and search every archive offline, stream any track with a double-click, and play it with the right emulator for its format: PSF, USF, GSF, 2SF, SPC, NSF, SID, VGM, BRSTM, MIDI and many more.

Runs on **Linux** and **Windows 10/11**.

## Contents

- [Quick start](#quick-start)
- [First launch](#first-launch)
- [Browsing and searching](#browsing-and-searching)
- [Streaming, downloading and local files](#streaming-downloading-and-local-files)
- [Favorites, Shuffle and History](#favorites-shuffle-and-history)
- [Game info and box art](#game-info-and-box-art)
- [Keeping indexes up to date](#keeping-indexes-up-to-date)
- [The players](#the-players)
- [Now Playing, tray icon and media keys](#now-playing-tray-icon-and-media-keys)
- [Exporting and length overrides](#exporting-and-length-overrides)
- [Options, View and About menus](#options-view-and-about-menus)
- [Supported archives](#supported-archives)
- [Windows](#windows)
- [Linux backends and manual building](#linux-backends-and-manual-building)
- [Where your data is stored](#where-your-data-is-stored)
- [Project layout](#project-layout)

## Quick start

**Linux** (Debian, Ubuntu, Mint, Fedora, Arch, openSUSE):

```sh
./run.sh
```

`run.sh` creates a local `.venv`, installs `requirements.txt` and starts the app. If Python's `venv` module is missing it tells you what to install (`sudo apt install python3-venv python3-tk` on Debian/Ubuntu).

**Windows:** double-click **`run.bat`**. See [Windows](#windows).

**Manually:**

```sh
pip install -r requirements.txt
python3 main.py
```

## First launch

The first time the app starts, it walks you through two steps:

1. **Install Dependencies.** On Linux this opens a terminal that installs system packages with your package manager (apt, dnf, pacman or zypper) and builds the format decoders from source (see [Linux backends](#linux-backends-and-manual-building)). On Windows the decoders are already bundled, so it only downloads the few pieces that aren't. You can run it again any time from **Options → Install Dependencies**; **Options → Dependencies** shows what is installed.
2. **Download Premade Indexes.** Downloads ready-made listings of every archive so browsing and searching work instantly, without crawling joshw.info yourself. They may be a little out of date; [Index Current / Index All](#keeping-indexes-up-to-date) catch up on anything newer.

> Building the Linux decoders needs up to about 1.5 GB of temporary disk space, which is cleaned up afterwards.

## Browsing and searching

- **Pick an archive** from the **Archive** dropdown and click **Open**. Double-click folders to enter them; use **Up**, and **<** / **>** to go back and forward.
- **Columns:** Type, Name, Date, Size, Domain and URL, plus **Year**, **Developer** and **Publisher**, which are read from joshw's `Title (date)(developer)(publisher)[platform]` folder names. Click a heading to sort. Columns size themselves to fit their contents.
- **Search** filters the current archive as you type. Every word must match, in any order, so `zelda 1998` works. Press Enter to save a search to the history dropdown; **Clear** empties that history.
- **Search all indexes** searches every downloaded archive at once, game folders included. The indexes load in the background, and results are capped at 3,000 rows to keep the list responsive.
- **New only** lists just what the last index update added (see [What's new](#keeping-indexes-up-to-date)).
- **Right-click** any row for a menu: add or remove **Favorite**, **Stream / Open**, **Download...**, **Game info** and **Copy URL**.
- **help!** opens a quick reference inside the app.

## Streaming, downloading and local files

- **Stream:** double-click a file (or select it and press Enter) and choose **Stream**. It downloads to a temporary folder and opens in the matching player, with the rest of the folder as its playlist.
- **Several files at once (VGMusic Siterip and Chiptune):** select multiple `.mid` files or tracker modules, then press Enter or right-click one of them. **Stream** downloads them all and opens them together as one playlist (VGMusic in the MIDI player, Chiptune in the mpv player), which moves on to the next track when each one ends; **Download** saves them as usual.
- **Archives** (`.7z`, `.zip`, `.tar`, `.gz`, `.bz2`) are extracted automatically and a file browser opens on the result, so you can choose what to play.
- **Stream Random!** picks a random track from the current archive, with a dice-roll animation (**Roll Again!** before accepting if you like). Tick **in all indexes!** to pick from every archive; **Options → Choose Random Stream Domains...** limits which archives are used.
- **Download:** **Download Selected File(s)** (Shift/Ctrl-click to select several) or **Download Every File Here** for the whole folder. Before a folder download you see the total size, and a warning if the drive may not have enough space. **Stop** cancels.
- **Default download location:** tick **Set Default Download Location** and choose a folder to skip the save prompt. Recent destinations are remembered.
- **Load Music...** plays files from your own computer. **Options → Set Default Load Music Folder...** sets where it starts.

Streamed files are deleted when the app closes.

## Favorites, Shuffle and History

- **Favorites tab:** right-click a file or folder and choose **★ Favorite**. Favorites are grouped under separators that follow the sort column (first letter, archive, year, developer, size and so on) and are saved between sessions.
- **Shuffle tab:** **Shuffle** fills this tab with random files from the current archive. **Options → Shuffle Amount** sets how many (10 to 500, or a custom number).
- **Smart Shuffle** (Options menu, on by default) makes Shuffle and Stream Random less repetitive: games played in the last week are rarely picked, while favorites and newly added entries are picked more often.
- **History tab:** every track you play, as **Recently played** or **Most played** with play counts. Double-click to play it again; if a streamed file has been cleaned up, it is downloaded again from joshw.info. **Clear History** wipes the list.

## Game info and box art

**View → Game Info Panel** adds a side panel for the selected game: box art, title, year, developer, publisher, platform and how often you've played it. Right-click → **Game info** opens it for any row.

Box art comes from [libretro-thumbnails](https://thumbnails.libretro.com/). Titles are matched against libretro's cover list for the game's system, preferring USA, then World, Europe and Japan releases, with fuzzy matching for small spelling differences. Covers are downloaded once and cached, and games without a cover aren't looked up again for 30 days.

## Keeping indexes up to date

- **Index Current** checks the open archive for new additions.
- **Index All** checks every archive, with progress and timers per archive, and finishes with a summary of the new titles.

Updates are incremental: only folders whose "last modified" time changed on the server are listed again, listings are fetched in parallel, and only new files are size-checked. An archive with nothing new is left untouched.

**What's new:** everything the last update added is shown in **green bold**, can be listed on its own with **New only**, and is favoured by Smart Shuffle.

## The players

Each format opens in its own player window and process, with its own taskbar icon. Players keep running if you close the browser, and each one can also be started on its own (e.g. `python3 gsf_player.py file.minigsf`).

Every player has:

- the **playlist** of the folder you played from, with **Prev / Next**, **Play/Pause**, **Stop**, **Random** and **Loop forever** (repeat the current track endlessly instead of fading out);
- a **seek bar** you can click or drag (hover over it to see the time);
- the track's **tags** (title, artist, game, length) where the format has them;
- **volume** up to 150%, shared by all open players;
- the browser's **theme and font size**, which update live when you change them.

| Player | Formats | Notes |
|---|---|---|
| PSF | `.psf`, `.minipsf`, `.psf2`, `.minipsf2` | Audio Overload SDK, in-process. Gapless. Seeks by rendering silently up to the target (about 2 s to reach 0:50), since the emulator can't jump. |
| GME | `.nsf`, `.nsfe`, `.spc`, `.gbs`, `.hes`, `.kss`, `.ay`, `.sap`, `.gym`, `.vgm` | Game Music Emu, in-process. Gapless. Lists each file's subtracks. |
| USF | `.usf`, `.miniusf` | lazyusf / lazyusf2. |
| GSF | `.gsf`, `.minigsf` | playgsf core. |
| 2SF | `.2sf`, `.mini2sf` | vio2sf core. |
| SID | `.sid` | sidplayfp, with subtunes. |
| MIDI / Beatnik | `.mid`, `.midi`, `.kar`, `.rmi`, `.rmf`, `.zmf`, `.xmf`, `.mxmf`, `.mthc` | NeoBAE `playbae`, with a choice of soundfont. On Linux, `.xmi` files are converted to MIDI first (needs WildMIDI). |
| vgmstream | `.ast`, `.dsp`, `.brstm`, `.rstm`, `.bfsar`, `.bfwav`, `.adp`, `.str`, `.thp`, `.vag`, `.wem`, ..., and any extension not listed here | vgmstream. |
| Arcade | `.vgz` | VGMPlay. |
| mpv | `.mp3`, `.ogg`, `.flac`, `.wav`, `.m4a`, `.opus`, `.xma`, ... | mpv. |
| mpv (tracker modules) | `.mod`, `.xm`, `.it`, `.s3m`, `.mptm`, `.mo3`, `.med`, `.stm`, `.imf`, `.dsm`, `.669`, `.mtm`, `.okt`, `.ult`, ... | mpv with libopenmpt. |
| mpv (AHX / HivelyTracker) | `.ahx`, `.hvl` | Rendered to WAV by `hvl2wav` (a second or so), then played by mpv. |

## Now Playing, tray icon and media keys

All players share a common engine (`player_core.py`) that registers each running player so it can be controlled from outside its own window:

- **View → Now Playing...** opens a small controller with box art, title, game and artist, a seek bar, playback buttons, **Show player**, and a list of all running players to switch between.
- **View → Tray Icon** puts an icon in the system tray with Play/Pause, Next, Previous, Stop, Now Playing and Quit.
- **Media keys** (Play/Pause, Next, Previous, Stop) control the player you used most recently. On Linux, players also show up in your desktop's media controls and in tools like `playerctl` (MPRIS), with track info and cover art.

Players talk to each other only over 127.0.0.1, using a random token stored in a file only your user can read.

## Exporting and length overrides

Right-click a track in a player's playlist for:

- **Export to WAV / FLAC:** renders the track with the same emulator that plays it, fade-out included, with a progress window. SID, MIDI, vgmstream and mpv formats are rendered by their command-line tools.
- **Set length / fade...:** for tracks with a missing or wrong length tag. The setting is remembered for that track and used by both playback and export. **Clear override** removes it.
- **Open folder:** opens the folder that contains the file.

## Options, View and About menus

| Options | What it does |
|---|---|
| Keep Download Window Open After Completion | Leave the progress window up when a download finishes. |
| Stream Random Button Animation | Turn the colour-cycling animation of **Stream Random!** on or off. |
| Choose Random Stream Domains... | Which archives **Stream Random!** may pick from across all indexes. |
| Smart Shuffle | See [above](#favorites-shuffle-and-history). |
| Set Default Load Music Folder... | Starting folder for **Load Music...**. |
| Download Premade Indexes | Replace your local indexes with the premade ones. |
| Install Dependencies | Install or rebuild the playback backends. |
| Shuffle Amount | How many files **Shuffle** picks. |
| Themes | Every ttkbootstrap theme. Hover over one to preview it; click to keep it. |
| Font Size | 8 to 16 pt, for the browser and all players. |
| Dependencies | Which backends are installed. |

| View | What it does |
|---|---|
| Now Playing... | Open the [Now Playing](#now-playing-tray-icon-and-media-keys) controller. |
| Game Info Panel | Show or hide the [game info](#game-info-and-box-art) panel. |
| Tray Icon | Show or hide the tray icon. |

**About** (next to View) shows the license and credits for every open-source project the app uses, with clickable links.

## Supported archives

| Maker | Archives (main file types) |
|---|---|
| Nintendo | NES (`.nsf`, `.nsfe`), SNES (`.spc`), Game Boy (`.gbs`), Game Boy Advance (`.gsf`), N64 (`.usf`), DS (`.2sf`), 3DS (`.3sf`), GameCube (`.ast`, `.dsp`, `.brstm`), Wii (`.brstm`, `.rstm`, `.bfsar`), Wii U (`.bfstm`, `.bfwav`), Switch (`.bfstm`, `.bcstm`, `.wem`, `.hca`, ...) |
| Sony | PlayStation (`.psf`), PlayStation 2 (`.psf2`), PlayStation 3, 4 and 5, PSP, Vita |
| Sega | Sega CD / Mega Drive (`.vgm`, `.vgz`, `.gym`), Saturn (`.ssf`), Dreamcast (`.dsf`) |
| Microsoft | Xbox, Xbox 360 (`.xma`, `.xmd`) |
| Others | 3DO, WonderSwan (`.wsr`), PC Engine (`.hes`), Neo Geo, FM Towns, MSX (`.kss`), C64 (`.sid`), Arcade (VGMRips `.vgz`), Chiptune (The Mod Archive: `.xm`, `.mod`, `.it`, `.s3m`, `.mptm`, `.ahx`, ...), Hoot, PC, S98, VGMusic (`.mid`), Other Assorted Systems |

Some archives contain formats that don't have a player here yet (such as `.3sf`, `.ssf` and `.dsf`). You can still browse, search and download them.

The **Chiptune** archive is a premade index of tracker modules from [The Mod Archive](https://modarchive.org). It can't be updated with Index Current / Index All, because the Mod Archive isn't a plain folder listing.

## Windows

The app runs on Windows 10/11 (64-bit), from source or as a PyInstaller build. Nothing needs compiling and no admin rights are needed.

### Running from source

Double-click **`run.bat`**. It installs Python through `winget` if it is missing, creates a local `.venv`, installs `requirements.txt` and starts the app without a console window.

### Building an .exe

```bat
python tools\fetch_windows_deps.py mpv
pyinstaller main.spec
```

This produces `dist\joshw-browser.exe`. The first command downloads mpv, which is over 100 MB and so kept out of git, so that it is bundled too.

### Playback backends

The Windows backends are bundled in `assets/libraries/windows/`. Each runs hidden, and the app's player windows provide the UI:

| Formats | Linux backend | Windows backend |
|---|---|---|
| vgmstream formats (BRSTM, DSP, WEM, ...) | vgmstream123 | `vgmstream-cli.exe` (official build) |
| VGZ (arcade) | vgmplay | `VGMPlay.exe` (official build) |
| SID | sidplayfp | `sidplayfp.exe` (official build) |
| MIDI / RMF | playbae | `playbae.exe` (official NeoBAE build) |
| NSF, SPC, GBS, VGM, ... | libgme | `libgme.dll` (cross-compiled) |
| GSF | playgsf | `gsfdec.exe` (playgsf core, built from `tools/windows_decoders`) |
| 2SF | 2sfPlay | `2sfdec.exe` (vio2sf core, built from `tools/windows_decoders`) |
| USF | lazyusf | `usfdec.exe` (lazyusf2 core, built from `tools/windows_decoders`) |
| AHX / HVL | hvl2wav | `hvl2wav.exe` (HivelyTracker replayer, built from `tools/windows_decoders`) |
| PSF / PSF2 | libaosdk.so | `aosdk.dll` (Audio Overload SDK, built from `tools/windows_decoders`) |
| MP3, OGG, FLAC, modules, ... | mpv | `mpv.exe` (downloaded on first use if not bundled) |
| 7z extraction | 7z | `7za.exe` (official build) |

**Install Dependencies** on Windows only downloads what isn't bundled: mpv, the FluidR3 MIDI soundfont, and the C64 ROMs that sidplayfp uses (saved to `%APPDATA%\sidplayfp`).

File names with non-English characters are passed to the decoders as their short (8.3) path, so they play normally.

To update the bundled backends, run `python tools/fetch_windows_deps.py` on a Linux machine with `mingw-w64`, `cmake` and `7z` installed, then commit the result. The pinned versions are listed at the top of that script.

## Linux backends and manual building

**Install Dependencies** writes a shell script and runs it in a terminal (gnome-terminal, konsole, xfce4-terminal or xterm). The script:

1. Installs system packages: git, cmake, a C/C++ compiler, libgme, sidplayfp, SDL2, libao, zlib, libsndfile, the codec libraries and xterm. The exact lists for apt, dnf, pacman and zypper are in `get_package_manager_info()` in `downloaders.py`.
2. Builds each missing decoder from source and copies it into `~/.config/joshw/`:

   | Decoder | Source | Notes |
   |---|---|---|
   | `lazyusf` | [derselbst/lazyusf](https://github.com/derselbst/lazyusf) | Patched to report its play position. |
   | `vgmstream123` | [vgmstream/vgmstream](https://github.com/vgmstream/vgmstream) | |
   | `playgsf` | [yshui/playgsf](https://github.com/yshui/playgsf) | With `patches/playgsf-start-offset.patch` for seeking. |
   | `2sfPlay` | [yshui/2sftowav](https://github.com/yshui/2sftowav) | vio2sf core with a small SDL2 player front-end. |
   | `vgmplay` | [vgmrips/vgmplay-legacy](https://github.com/vgmrips/vgmplay-legacy) | |
   | `playbae` | [zefie/NeoBAE](https://github.com/zefie/NeoBAE) | Also builds and installs SDL3 and SDL3_ttf. |
   | `gsfdec`, `2sfdec`, `usfdec` | `tools/windows_decoders/` | Small WAV-to-stdout decoders, the same ones bundled for Windows. Needed for USF seeking, 2SF length overrides and GSF/2SF/USF export. |
   | `hvl2wav` | [pete-gordon/hivelytracker](https://github.com/pete-gordon/hivelytracker) | AHX/HivelyTracker renderer, needed for `.ahx` and `.hvl` files. |

3. Downloads the FluidR3 GM soundfont for MIDI and the C64 ROMs for sidplayfp.

The PSF engine (`libaosdk.so`) is already in `assets/libraries/`, so it needs no building. It is built from the same `aosdk_wrapper.c` as the Windows DLL.

To build by hand, install the packages above and follow `InstallDependenciesWorker.run_installation()` in `downloaders.py`. The decoders in `tools/windows_decoders/` build with:

```sh
python3 tools/windows_decoders/build.py ~/.config/joshw --native gsfdec 2sfdec usfdec hvl2wav
python3 tools/windows_decoders/build.py assets/libraries --native aosdk   # rebuild libaosdk.so
```

The app looks for each backend in this order: the app's `bin/` folder or bundle, then `~/.config/joshw/`, then your `PATH`.

## Where your data is stored

Everything is kept in one folder: `~/.config/joshw/` on Linux, `Documents\joshw\` on Windows.

| File / folder | Contents |
|---|---|
| `settings.json` | Theme, font size, volume and other options |
| `indexes/` | Archive indexes |
| `favorites.json` | Your favorites |
| `history.sqlite3` | Play history and counts |
| `track_overrides.json` | Per-track length and fade overrides |
| `new_entries.json` | What the last index update added |
| `art/` | Cached box art |
| `temp_extracted/` | Streamed files (deleted on exit) |
| `players/` | Registration files of running players |

## Project layout

| File | Purpose |
|---|---|
| `main.py` | Entry point (also starts players in the PyInstaller build) |
| `browser.py`, `browser_features.py` | Main window: browsing, search, favorites, shuffle, history, game info, menus |
| `config.py`, `network.py` | Archive list, paths, directory listings and downloads |
| `index_updater.py`, `downloaders.py` | Incremental indexing, premade indexes, dependency installer, download windows |
| `library.py` | Play history, overrides, new-entry tracking, game name parsing, box art lookup |
| `player_launcher.py`, `players.py` | Choosing and starting the right player; shared player widgets and volume |
| `*_player.py` | One file per player |
| `player_core.py`, `player_ipc.py`, `media_session.py` | Shared player engine, player-to-player control, media keys and MPRIS |
| `now_playing.py` | Now Playing window and tray icon |
| `exporter.py` | WAV/FLAC export |
| `psf_ctypes.py` | Bindings for the PSF engine |
| `platform_support.py`, `windows_setup.py` | Linux/Windows differences and Windows setup |
| `tools/` | Windows backend fetcher, decoder sources and build script |
| `assets/` | Icons and bundled libraries |

## License

Distributed under the MIT License. See [`LICENSE`](LICENSE), which also lists and links every third-party project the app uses, along with each one's license. You can also read it in the app under **About**.
