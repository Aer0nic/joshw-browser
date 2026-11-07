import os
import shutil
import subprocess
import sys
import time
import urllib.error
from pathlib import Path
import threading
import tempfile
import urllib.request
import zipfile
import tkinter as tk
from tkinter import ttk, messagebox
import ttkbootstrap as tb
import config
from config import resource_path

class PremadeIndexProgressWindow(tb.Toplevel):
    def __init__(self, parent):
        super().__init__(parent)
        self.parent = parent
        self.title("Downloading Premade Indexes...")
        self.geometry("420x150")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        try:
            icon_file = resource_path("assets/icons/app_icon.png")
            if icon_file.exists():
                self.iconphoto(False, tk.PhotoImage(file=str(icon_file)))
        except Exception:
            pass

        self.status_var = tk.StringVar(value="Connecting to repository...")
        
        padding = tb.Frame(self, padding=16)
        padding.pack(fill="both", expand=True)

        tb.Label(padding, textvariable=self.status_var, font=("Arial", 9, "bold")).pack(anchor="w", pady=(0, 10))

        # Indeterminate progress bar for download operation
        self.pbar = tb.Progressbar(padding, mode="indeterminate", bootstyle="primary-striped")
        self.pbar.pack(fill="x", expand=True, pady=(0, 10))
        self.pbar.start(12)

        threading.Thread(target=self.download_worker, daemon=True).start()

    def download_worker(self):
        cache_dir = Path(config.get_cache_directory())
        indexes_dir = cache_dir / "indexes"
        indexes_dir.mkdir(parents=True, exist_ok=True)

        # Use GitHub's zip archive endpoint to avoid needing git or wget on Windows/Linux
        zip_url = "https://github.com/Aer0nic/joshw-browser/archive/refs/heads/main.zip"
        temp_zip = cache_dir / "joshw_repo.zip"
        extract_target = cache_dir / "joshw_extracted"

        try:
            if extract_target.exists():
                shutil.rmtree(extract_target, ignore_errors=True)

            self.update_status("Downloading index archive...")
            urllib.request.urlretrieve(zip_url, str(temp_zip))

            self.update_status("Extracting index files...")
            with zipfile.ZipFile(temp_zip, 'r') as zip_ref:
                zip_ref.extractall(extract_target)

            if temp_zip.exists():
                temp_zip.unlink()

            config_display_path = "Documents\\Joshw\\indexes" if sys.platform == "win32" else "~/.config/joshw/indexes"
            self.update_status(f"Installing index files to {config_display_path}...")

            # Locate domain_cache files inside the extracted repo structure
            found_count = 0
            for root, dirs, files in os.walk(extract_target):
                for file in files:
                    if file.startswith("domain_cache") and file.endswith(".json"):
                        src_file = Path(root) / file
                        shutil.copy2(src_file, indexes_dir / file)
                        found_count += 1

            if extract_target.exists():
                shutil.rmtree(extract_target, ignore_errors=True)

            if found_count > 0:
                self.finish_success(found_count, indexes_dir)
            else:
                error_msg = "Premade index files containing 'domain_cache' were not found in the repository archive."
                self.finish_error(error_msg, zip_url)

        except Exception as e:
            error_msg = f"An error occurred during indexing download:\n\n{e}"
            self.finish_error(error_msg, zip_url)

    def update_status(self, text):
        if self.winfo_exists():
            self.after(0, lambda: self.status_var.set(text))

    def finish_success(self, count, indexes_dir):
        if self.winfo_exists():
            self.pbar.stop()
            self.after(0, self.close_and_notify_success, count, indexes_dir)

    def close_and_notify_success(self, count, indexes_dir):
        self.destroy()
        if self.parent.winfo_exists():
            messagebox.showinfo(
                "Success",
                f"Successfully installed {count} index files into:\n{indexes_dir}"
            )

    def finish_error(self, error_msg, target_url):
        if self.winfo_exists():
            self.pbar.stop()
            self.after(0, self.close_and_show_error, error_msg, target_url)

    def close_and_show_error(self, error_msg, target_url):
        self.destroy()
        if self.parent.winfo_exists():
            messagebox.showerror("Installation Error", error_msg)


def check_and_download_sid_firmware():
    """Silently checks and downloads kernel and basic firmware files on startup if missing."""
    try:
        if sys.platform == "win32":
            sidplayfp_dir = Path(config.get_cache_directory()) / "sidplayfp"
        else:
            sidplayfp_dir = Path(os.path.expanduser("~/.local/share/sidplayfp"))
            
        sidplayfp_dir.mkdir(parents=True, exist_ok=True)

        kernal_file = sidplayfp_dir / "kernal"
        basic_file = sidplayfp_dir / "basic"

        if not kernal_file.exists():
            url_kernal = "https://www.zimmers.net/anonftp/pub/cbm/firmware/computers/c64/kernal.901227-03.bin"
            try:
                urllib.request.urlretrieve(url_kernal, str(kernal_file))
            except (urllib.error.URLError, OSError):
                pass

        if not basic_file.exists():
            url_basic = "https://www.zimmers.net/anonftp/pub/cbm/firmware/computers/c64/basic.901226-01.bin"
            try:
                urllib.request.urlretrieve(url_basic, str(basic_file))
            except (urllib.error.URLError, OSError):
                pass
    except Exception:
        pass

def get_package_manager_info():
    import shutil
    
    if shutil.which("apt"):
        return "apt", "sudo apt update", "sudo apt install -y", [
            "git", "cmake", "libgme0", "build-essential", "sidplayfp", "xterm", "xdotool",
            "zlib1g-dev", "libao-dev", "libsdl2-dev", "pkg-config", "pulseaudio-utils",
            "libsndfile-dev", "libmp3lame-dev", "libogg-dev", "libvorbis-dev",
            "libopus-dev", "libopusfile-dev", "libflac-dev", "liblzma-dev", "libxmp-dev"
        ]
    elif shutil.which("dnf"):
        return "dnf", "sudo dnf makecache", "sudo dnf install -y", [
            "git", "cmake", "game-music-emu", "gcc-c++", "make", "sidplayfp", "xterm", "xdotool",
            "zlib-devel", "libao-devel", "SDL2-devel", "pkgconf", "pulseaudio-utils",
            "libsndfile-devel", "lame-devel", "libogg-devel", "libvorbis-devel",
            "opus-devel", "opusfile-devel", "flac-devel", "xz-devel", "libxmp-devel"
        ]
    elif shutil.which("pacman"):
        return "pacman", "sudo pacman -Sy", "sudo pacman -S --noconfirm", [
            "git", "cmake", "libgme", "base-devel", "sidplayfp", "xterm", "xdotool",
            "zlib", "libao", "sdl2", "pkgconf", "libpulse",
            "libsndfile", "lame", "libogg", "libvorbis",
            "opus", "opusfile", "flac", "xz", "libxmp"
        ]
    elif shutil.which("zypper"):
        return "zypper", "sudo zypper refresh", "sudo zypper install -y", [
            "git", "cmake", "libgme0", "gcc-c++", "make", "sidplayfp", "xterm", "xdotool",
            "zlib-devel", "libao-devel", "libSDL2-devel", "pkgconf", "pulseaudio-utils",
            "libsndfile-devel", "lame-devel", "libogg-devel", "libvorbis-devel",
            "opus-devel", "opusfile-devel", "flac-devel", "xz-devel", "libxmp-devel"
        ]
    
    # Fallback to apt
    return "apt", "sudo apt update", "sudo apt install -y", [
        "git", "cmake", "libgme0", "build-essential", "sidplayfp", "xterm", "xdotool",
        "zlib1g-dev", "libao-dev", "libsdl2-dev", "pkg-config", "pulseaudio-utils",
        "libsndfile-dev", "libmp3lame-dev", "libogg-dev", "libvorbis-dev",
        "libopus-dev", "libopusfile-dev", "libflac-dev", "liblzma-dev", "libxmp-dev"
    ]

class FirstStartInstallDialog(tb.Toplevel):
    def __init__(self, parent, prompt_text, on_accept):
        super().__init__(parent)
        self.parent = parent
        self.on_accept = on_accept
        self.title("Install Dependencies")
        self.geometry("540x420")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        frame = ttk.Frame(self, padding=16)
        frame.pack(fill="both", expand=True)

        # Pulsing Red Header
        self.prefix_label = ttk.Label(
            frame,
            text="FIRST TIME USER INFO!!",
            font=("Arial", 11, "bold"),
            foreground="red"
        )
        self.prefix_label.pack(anchor="w", pady=(0, 8))

        # Main Body Text
        body_label = ttk.Label(
            frame, text=prompt_text, font=("Arial", 9), wraplength=500
        )
        body_label.pack(anchor="w", fill="both", expand=True, pady=(0, 15))

        # Action Buttons
        btn_frame = ttk.Frame(frame)
        btn_frame.pack(fill="x", side="bottom")

        cancel_btn = ttk.Button(
            btn_frame, text="Cancel", width=12, command=self.destroy
        )
        cancel_btn.pack(side="right", padx=(6, 0))

        ok_btn = ttk.Button(
            btn_frame, text="OK", width=12, command=self.confirm
        )
        ok_btn.pack(side="right")
        ok_btn.focus_set()

    def confirm(self):
        self.destroy()
        if self.on_accept:
            self.on_accept()

def install_dependencies_prompt(parent, is_first_start=False, on_complete=None):
    pm_name, update_cmd, install_cmd, pkgs = get_package_manager_info()
    pkg_str = " ".join(pkgs)

    if sys.platform == "win32":
        msg = (
            "Automatic dependency building via apt/CMake scripts is designed for Linux environments.\n\n"
            "On Windows, please ensure external CLI binaries like vgmstream123, mpv, and 7-Zip are available in your system PATH or project directory."
        )
        if is_first_start:
            FirstStartInstallDialog(parent, msg, lambda: [on_complete() if on_complete else None])
        else:
            messagebox.showinfo("Windows Dependencies", msg)
            if on_complete:
                on_complete()
        return

    prompt_body = (
        "The Josh Archive Browser requires some dependencies to run its built-in music player properly.\n"
        "You need about 1.5 GB of free space on your main drive to allow for grabbing and building all needed files.\n"
        "These will be deleted after installation!\n\n"
        "Click OK to start installation. This will open a terminal window and ask for your Sudo password.\n"
        "Once entered, it will run:\n\n"
        f"{update_cmd}\n"
        f"{install_cmd} {pkg_str}\n\n"
        "It will then grab the source files for Lazyusf, VGMStream, PlayGSF, 2sfPlay, SDL3, SDL3_ttf, NeoBAE,\n"
        "download the soundfont, and build them into your config directory.\n\n"
        "If you agree to this, hit the OK button to start, otherwise hit Cancel."
    )

    def start_worker():
        InstallDependenciesWorker(parent, pm_name, update_cmd, install_cmd, pkgs)
        if on_complete:
            on_complete()

    if is_first_start:
        FirstStartInstallDialog(parent, prompt_body, start_worker)
    else:
        ans = messagebox.askokcancel("Install Dependencies", prompt_body)
        if ans:
            start_worker()


class InstallDependenciesWorker:
    def __init__(self, parent, pm_name="apt", update_cmd="sudo apt update", install_cmd="sudo apt install -y", pkgs=None):
        self.parent = parent
        self.pm_name = pm_name
        self.update_cmd = update_cmd
        self.install_cmd = install_cmd
        self.pkgs = pkgs or []
        threading.Thread(target=self.run_installation, daemon=True).start()

    def run_installation(self):
        cache_dir = Path(config.get_cache_directory())
        cache_dir.mkdir(parents=True, exist_ok=True)
        sidplayfp_dir = Path(os.path.expanduser("~/.local/share/sidplayfp"))
        sidplayfp_dir.mkdir(parents=True, exist_ok=True)

        temp_build_dir = Path(tempfile.mkdtemp(prefix="joshw_dep_"))
        try:
            script_lines = []

            # 1. Base packages installation dynamically configured
            script_lines.append("read -p \"Skip package manager update/install and proceed to dependency builds? (y/n): \" skip_pm")
            script_lines.append("case \"$skip_pm\" in")
            script_lines.append("    [yY]*)")
            script_lines.append("        echo 'Skipping package installation...'")
            script_lines.append("        ;;")
            script_lines.append("    *)")
            script_lines.append(f"        {self.update_cmd}")
            script_lines.append(f"        {self.install_cmd} {' '.join(self.pkgs)}")
            script_lines.append("        ;;")
            script_lines.append("esac")

            # 2. Lazyusf build
            lazyusf_bin = cache_dir / "lazyusf"
            if not lazyusf_bin.exists():
                lazyusf_src_dir = temp_build_dir / "lazyusf"
                patch_script = temp_build_dir / "patch_lazyusf.py"
                
                with open(patch_script, "w", encoding="utf-8") as ps:
                    ps.write("import glob\n")
                    ps.write("import sys\n\n")
                    ps.write("target = 'printf(\"\\\\r%f\",(play_time/60000));'\n")
                    ps.write("replacement = 'printf(\"\\\\r%.7f\", play_time / 60000.0); fflush(stdout);'\n\n")
                    ps.write("found = False\n")
                    ps.write("for f in glob.glob(f'{sys.argv[1]}/**/audio.c', recursive=True):\n")
                    ps.write("    with open(f, 'r', encoding='utf-8') as file:\n")
                    ps.write("        content = file.read()\n")
                    ps.write("    if target in content:\n")
                    ps.write("        with open(f, 'w', encoding='utf-8') as file:\n")
                    ps.write("            file.write(content.replace(target, replacement))\n")
                    ps.write("        print(f'Successfully patched {f}')\n")
                    ps.write("        found = True\n\n")
                    ps.write("if not found:\n")
                    ps.write("    sys.exit('Patch failed: target text not found')\n")

                script_lines.extend([
                    f"rm -rf \"{lazyusf_src_dir}\"",
                    f"git clone https://github.com/derselbst/lazyusf.git \"{lazyusf_src_dir}\"",
                    f"python3 \"{patch_script}\" \"{lazyusf_src_dir}\"",
                    f"mkdir -p \"{lazyusf_src_dir}/build\"",
                    f"cd \"{lazyusf_src_dir}/build\" && cmake .. && make",
                    f"cp \"{lazyusf_src_dir}/build/lazyusf\" \"{cache_dir}/\"",
                    f"chmod +x \"{lazyusf_bin}\""
                ])

            # 3. VGMStream build
            vgmstream_bin = cache_dir / "vgmstream123"
            if not vgmstream_bin.exists():
                vgmstream_src = temp_build_dir / "vgmstream"
                script_lines.extend([
                    f"git clone https://github.com/vgmstream/vgmstream.git \"{vgmstream_src}\"",
                    f"cd \"{vgmstream_src}\" && bash make-build-cmake.sh",
                    f"cp \"{vgmstream_src}/build/cli/vgmstream123\" \"{cache_dir}/\"",
                    f"chmod +x \"{vgmstream_bin}\""
                ])

            # 4. PlayGSF build & patch
            playgsf_bin = cache_dir / "playgsf"
            playgsf_needs_seek_patch = not playgsf_bin.exists()
            if playgsf_bin.exists():
                try:
                    playgsf_help = subprocess.run(
                        [str(playgsf_bin), "-h"], check=True, capture_output=True, text=True, timeout=3
                    ).stdout
                    playgsf_needs_seek_patch = "-b<num>" not in playgsf_help
                except (OSError, subprocess.SubprocessError):
                    playgsf_needs_seek_patch = True

            if playgsf_needs_seek_patch:
                playgsf_patch = resource_path("patches/playgsf-start-offset.patch")
                staged_playgsf_patch = temp_build_dir / "playgsf-start-offset.patch"
                
                has_patch = playgsf_patch.exists()
                if has_patch:
                    shutil.copy2(playgsf_patch, staged_playgsf_patch)

                script_lines.extend([
                    f"cd \"{cache_dir}\"",
                    "rm -rf playgsfbuild",
                    "git clone https://github.com/yshui/playgsf.git playgsfbuild",
                    "cd playgsfbuild"
                ])

                if has_patch:
                    script_lines.append(f"git apply --unidiff-zero \"{staged_playgsf_patch}\" || echo 'Warning: PlayGSF patch application skipped or failed, continuing standard build...'")

                script_lines.extend([
                    "./configure || exit 1",
                    "make clean",
                    "make || true",
                    "g++ -fPIC -fpermissive -no-pie gsf.o VBA/GBA.o VBA/Globals.o VBA/Sound.o VBA/Util.o VBA/bios.o VBA/memgzio.o VBA/snd_interp.o VBA/unzip.o linuxmain.o VBA/psftag.o libresample/src/filterkit.o libresample/src/resample.o libresample/src/resamplesubs.o -o playgsf -lao -lSDL2 -lz -lm -lpthread || exit 1",
                    f"if [ -f playgsf ]; then cp playgsf \"{playgsf_bin}\"; elif [ -f src/playgsf ]; then cp src/playgsf \"{playgsf_bin}\"; else exit 1; fi",
                    "cd ..",
                    "rm -rf playgsfbuild",
                    f"chmod +x \"{playgsf_bin}\""
                ])

            # 5. 2sfPlay build
            twosf_play_bin = cache_dir / "2sfPlay"
            twosf_needs_seek_support = not twosf_play_bin.exists()
            if twosf_play_bin.exists():
                try:
                    twosf_help = subprocess.run([str(twosf_play_bin), "--help"], capture_output=True, text=True, timeout=3).stdout
                    twosf_needs_seek_support = "-b<ms>" not in twosf_help
                except (OSError, subprocess.SubprocessError):
                    twosf_needs_seek_support = True

            if twosf_needs_seek_support:
                twosf_workdir = temp_build_dir / "2sftowav_source"
                script_lines.extend([
                    f"git clone --recurse-submodules https://github.com/yshui/2sftowav.git \"{twosf_workdir}\"",
                    f"cd \"{twosf_workdir}/src/psflib\" && make",
                    f"cd \"{twosf_workdir}/src/vio2sf\""
                ])

                script_lines.append("echo '#include <locale.h>' > 2sftowav.c")
                script_lines.append("echo '#include <signal.h>' >> 2sftowav.c")
                script_lines.append("echo '#include <stdint.h>' >> 2sftowav.c")
                script_lines.append("echo '#include <stdio.h>' >> 2sftowav.c")
                script_lines.append("echo '#include <stdlib.h>' >> 2sftowav.c")
                script_lines.append("echo '#include <string.h>' >> 2sftowav.c")
                script_lines.append("echo '#include <SDL.h>' >> 2sftowav.c")
                script_lines.append("echo '#include \"common.h\"' >> 2sftowav.c")
                script_lines.append("echo '#include \"desmume/state.h\"' >> 2sftowav.c")
                script_lines.append("echo 'static int16_t samples[4096];' >> 2sftowav.c")
                script_lines.append("echo 'static volatile sig_atomic_t stop_requested = 0;' >> 2sftowav.c")
                script_lines.append("echo 'static void handle_sigint(int signal_number) { (void)signal_number; stop_requested = 1; }' >> 2sftowav.c")
                script_lines.append("echo 'int main(int argc, char **argv) {' >> 2sftowav.c")
                script_lines.append("echo '    setlocale(LC_ALL, \"C\");' >> 2sftowav.c")
                script_lines.append("echo '    long start_time_ms = 0;' >> 2sftowav.c")
                script_lines.append("echo '    const char *input_file = NULL;' >> 2sftowav.c")
                script_lines.append("echo '    for (int i = 1; i < argc; i++) {' >> 2sftowav.c")
                script_lines.append("echo '        if (strcmp(argv[i], \"--help\") == 0) { return 0; }' >> 2sftowav.c")
                script_lines.append("echo '        if (strncmp(argv[i], \"-b\", 2) == 0) {' >> 2sftowav.c")
                script_lines.append("echo '            char *end = NULL;' >> 2sftowav.c")
                script_lines.append("echo '            start_time_ms = strtol(argv[i] + 2, &end, 10);' >> 2sftowav.c")
                script_lines.append("echo '        } else if (input_file == NULL) {' >> 2sftowav.c")
                script_lines.append("echo '            input_file = argv[i];' >> 2sftowav.c")
                script_lines.append("echo '        }' >> 2sftowav.c")
                script_lines.append("echo '    }' >> 2sftowav.c")
                script_lines.append("echo '    if (input_file == NULL) return 1;' >> 2sftowav.c")
                script_lines.append("echo '    struct twosf_loader_state state = {0};' >> 2sftowav.c")
                script_lines.append("echo '    if (psf_load(input_file, &stdio_callbacks, 0x24, twosf_loader, &state, twosf_info, &state, 1, print_message, 0) <= 0) return 1;' >> 2sftowav.c")
                script_lines.append("echo '    NDS_state core = {0};' >> 2sftowav.c")
                script_lines.append("echo '    state_init(&core);' >> 2sftowav.c")
                script_lines.append("echo '    core.dwInterpolation = 0;' >> 2sftowav.c")
                script_lines.append("echo '    core.dwChannelMute = 0;' >> 2sftowav.c")
                script_lines.append("echo '    state_setrom(&core, state.rom, state.rom_size, 0);' >> 2sftowav.c")
                script_lines.append("echo '    state_loadstate(&core, state.state, state.state_size);' >> 2sftowav.c")
                script_lines.append("echo '    long nsamples = state.length_ms * 441 / 10 + 1;' >> 2sftowav.c")
                script_lines.append("echo '    long fade_samples = state.fade_ms * 441 / 10;' >> 2sftowav.c")
                script_lines.append("echo '    long samples_to_skip = start_time_ms * 441 / 10;' >> 2sftowav.c")
                script_lines.append("echo '    while (samples_to_skip > 0) {' >> 2sftowav.c")
                script_lines.append("echo '        long to_skip = samples_to_skip < 2048 ? samples_to_skip : 2048;' >> 2sftowav.c")
                script_lines.append("echo '        state_render(&core, samples, to_skip);' >> 2sftowav.c")
                script_lines.append("echo '        samples_to_skip -= to_skip;' >> 2sftowav.c")
                script_lines.append("echo '        nsamples -= to_skip;' >> 2sftowav.c")
                script_lines.append("echo '    }' >> 2sftowav.c")
                script_lines.append("echo '    SDL_Init(SDL_INIT_AUDIO);' >> 2sftowav.c")
                script_lines.append("echo '    SDL_AudioSpec wanted = {0};' >> 2sftowav.c")
                script_lines.append("echo '    wanted.freq = 44100;' >> 2sftowav.c")
                script_lines.append("echo '    wanted.format = AUDIO_S16SYS;' >> 2sftowav.c")
                script_lines.append("echo '    wanted.channels = 2;' >> 2sftowav.c")
                script_lines.append("echo '    wanted.samples = 2048;' >> 2sftowav.c")
                script_lines.append("echo '    SDL_AudioDeviceID device = SDL_OpenAudioDevice(NULL, 0, &wanted, NULL, 0);' >> 2sftowav.c")
                script_lines.append("echo '    signal(SIGINT, handle_sigint);' >> 2sftowav.c")
                script_lines.append("echo '    SDL_PauseAudioDevice(device, 0);' >> 2sftowav.c")
                script_lines.append("echo '    while (nsamples > 0 && !stop_requested) {' >> 2sftowav.c")
                script_lines.append("echo '        long to_render = nsamples < 2048 ? nsamples : 2048;' >> 2sftowav.c")
                script_lines.append("echo '        state_render(&core, samples, to_render);' >> 2sftowav.c")
                script_lines.append("echo '        if (fade_samples > 0 && nsamples <= fade_samples) {' >> 2sftowav.c")
                script_lines.append("echo '            for (int i = 0; i < to_render * 2; i++) {' >> 2sftowav.c")
                script_lines.append("echo '                double factor = (nsamples - i / 2) / (double)fade_samples;' >> 2sftowav.c")
                script_lines.append("echo '                samples[i] = (int16_t)(samples[i] * factor);' >> 2sftowav.c")
                script_lines.append("echo '            }' >> 2sftowav.c")
                script_lines.append("echo '        }' >> 2sftowav.c")
                script_lines.append("echo '        SDL_QueueAudio(device, samples, to_render * 2 * sizeof(int16_t));' >> 2sftowav.c")
                script_lines.append("echo '        nsamples -= to_render;' >> 2sftowav.c")
                script_lines.append("echo '        while (!stop_requested && SDL_GetQueuedAudioSize(device) > 44100 * 4 / 2) { SDL_Delay(10); }' >> 2sftowav.c")
                script_lines.append("echo '    }' >> 2sftowav.c")
                script_lines.append("echo '    SDL_CloseAudioDevice(device);' >> 2sftowav.c")
                script_lines.append("echo '    SDL_Quit();' >> 2sftowav.c")
                script_lines.append("echo '    return 0;' >> 2sftowav.c")
                script_lines.append("echo '}' >> 2sftowav.c")

                script_lines.append("echo 'CFLAGS = -g -I. $(shell pkg-config --cflags sdl2)' > Makefile")
                script_lines.append("echo 'CXXFLAGS = -g -I. -std=gnu++17' >> Makefile")
                script_lines.append("echo 'SDL_LIBS = $(shell pkg-config --libs sdl2)' >> Makefile")
                script_lines.append("echo 'OBJS = desmume/FIFO.o desmume/GPU.o desmume/MMU.o desmume/NDSSystem.o desmume/SPU.o desmume/arm_instructions.o desmume/armcpu.o desmume/barray.o desmume/bios.o desmume/cp15.o desmume/isqrt.o desmume/matrix.o desmume/mc.o desmume/resampler.o desmume/state.o desmume/thumb_instructions.o 2sftowav.o' >> Makefile")
                script_lines.append("echo 'all: libvio2sf.a 2sfplay' >> Makefile")
                script_lines.append("echo 'libvio2sf.a: $(OBJS)' >> Makefile")
                script_lines.append("echo -e '\t$(AR) rcs $@ $^' >> Makefile")
                script_lines.append("echo '%.o: %.c' >> Makefile")
                script_lines.append("echo -e '\t$(CC) -c $(CFLAGS) -O3 -o $@ $<' >> Makefile")
                script_lines.append("echo '%.o: %.cpp' >> Makefile")
                script_lines.append("echo -e '\t$(CXX) -c $(CXXFLAGS) -O3 -o $@ $<' >> Makefile")
                script_lines.append("echo '2sfplay: 2sftowav.o libvio2sf.a ../psflib/libpsflib.a' >> Makefile")
                script_lines.append("echo -e '\t$(CXX) $(CXXFLAGS) -o $@ 2sftowav.o libvio2sf.a ../psflib/libpsflib.a -lz -lm $(SDL_LIBS)' >> Makefile")

                script_lines.extend([
                    "make clean || true",
                    "make",
                    f"install -m 755 2sfplay \"{twosf_play_bin}\""
                ])

            vgmplay_bin = cache_dir / "vgmplay"
            if not vgmplay_bin.exists():
                vgmplay_src = temp_build_dir / "vgmplay-legacy"
                script_lines.extend([
                    f"git clone https://github.com/vgmrips/vgmplay-legacy.git \"{vgmplay_src}\"",
                    f"cd \"{vgmplay_src}/VGMPlay\"",
                    "make",
                    f"mv vgmplay \"{cache_dir}/\"",
                    f"chmod +x \"{vgmplay_bin}\""
                ])

            # 6. NeoBAE (playbae) and its SDL3/SDL3_ttf source build & install
            playbae_bin = cache_dir / "playbae"
            if not playbae_bin.exists():
                sdl_src = temp_build_dir / "SDL"
                sdl_ttf_src = temp_build_dir / "SDL_ttf"
                neobae_src = temp_build_dir / "NeoBAE"
                
                script_lines.extend([
                    f"git clone https://github.com/libsdl-org/SDL.git -b main --depth 1 \"{sdl_src}\"",
                    f"cmake -B \"{sdl_src}/build\" \"{sdl_src}\"",
                    f"cmake --build \"{sdl_src}/build\" --parallel $(nproc)",
                    f"sudo cmake --install \"{sdl_src}/build\"",
                    
                    f"git clone https://github.com/libsdl-org/SDL_ttf.git -b main --depth 1 \"{sdl_ttf_src}\"",
                    f"cmake -B \"{sdl_ttf_src}/build\" \"{sdl_ttf_src}\"",
                    f"cmake --build \"{sdl_ttf_src}/build\" --parallel $(nproc)",
                    f"sudo cmake --install \"{sdl_ttf_src}/build\"",
                    
                    f"git clone --recurse-submodules https://github.com/zefie/NeoBAE.git \"{neobae_src}\"",
                    f"cmake -B \"{neobae_src}/build\" \"{neobae_src}\"",
                    f"cmake --build \"{neobae_src}/build\" --parallel $(nproc)",
                    f"mv \"{neobae_src}/build/bin/playbae\" \"{playbae_bin}\"",
                    f"chmod +x \"{playbae_bin}\""
                ])

            # 7. Soundfont and Extras download
            soundfont_file = cache_dir / "FluidR3_GM.sf2"
            if not soundfont_file.exists():
                script_lines.append(f"wget -O \"{soundfont_file}\" https://github.com/pianobooster/fluid-soundfont/releases/download/v3.1/FluidR3_GM.sf2 || true")

            kernal_file = sidplayfp_dir / "kernal"
            if not kernal_file.exists():
                script_lines.append(f"wget -O \"{kernal_file}\" https://www.zimmers.net/anonftp/pub/cbm/firmware/computers/c64/kernal.901227-03.bin || true")

            basic_file = sidplayfp_dir / "basic"
            if not basic_file.exists():
                script_lines.append(f"wget -O \"{basic_file}\" https://www.zimmers.net/anonftp/pub/cbm/firmware/computers/c64/basic.901226-01.bin || true")

            # 8. Cleanup temp directory
            script_lines.append(f"rm -rf \"{temp_build_dir}\"")

            script_file = temp_build_dir / "install_deps.sh"
            with open(script_file, "w", encoding="utf-8") as f:
                f.write("#!/bin/bash\n\n")
                f.write("error_handler() {\n")
                f.write("    echo ''\n")
                f.write("    echo '===================================================' \n")
                f.write("    echo 'ERROR DETECTED! Installation failed.'\n")
                f.write("    echo 'Review the log messages above to pinpoint the issue.'\n")
                f.write("    echo '===================================================' \n")
                f.write("    read -p 'Press Enter to exit...' key\n")
                f.write("    exit 1\n")
                f.write("}\n\n")
                f.write("trap 'error_handler' ERR\n")
                f.write("set -e\n\n")
                f.write("\n".join(script_lines) + "\n\n")
                f.write("echo '----------------------------------------'\n")
                f.write("echo 'Installation complete successfully. Press Enter to close.'\n")
                f.write("read -p 'Press Enter to exit...' key\n")
            os.chmod(script_file, 0o755)

            terminals = [
                ("gnome-terminal", ["gnome-terminal", "--", "bash", "-c", str(script_file)]),
                ("konsole", ["konsole", "-e", "bash", "-c", str(script_file)]),
                ("xfce4-terminal", ["xfce4-terminal", "-e", f"bash -c '{script_file}'"]),
                ("xterm", ["xterm", "-hold", "-e", str(script_file)]),
                ("x-terminal-emulator", ["x-terminal-emulator", "-e", f"bash -c '{script_file}'"])
            ]

            launched = False
            for term_name, term_cmd in terminals:
                if shutil.which(term_name):
                    try:
                        subprocess.Popen(term_cmd)
                        launched = True
                        break
                    except Exception:
                        pass
            if not launched:
                subprocess.Popen(["x-terminal-emulator", "-e", f"bash -c '{script_file}'"])

        except Exception as e:
            if self.parent.winfo_exists():
                self.parent.after(0, lambda err=e: messagebox.showerror("Installation Error", f"An error occurred during dependency installation:\n\n{err}"))


class IndexAllWindow(tb.Toplevel):
    def __init__(self, parent, domains):
        super().__init__(parent)
        self.parent = parent
        self.domains = domains
        self.title("Index All Domains")
        self.geometry("580x520")
        self.minsize(520, 400)
        self.transient(parent)
        self.grab_set()

        try:
            icon_file = resource_path("assets/icons/app_icon.png")
            if icon_file.exists():
                self.iconphoto(False, tk.PhotoImage(file=str(icon_file)))
        except Exception:
            pass

        self.domain_rows = {}
        self.total_start_time = time.time()
        self.is_running = True

        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self.build_ui()

        threading.Thread(target=self.index_all_worker, daemon=True).start()
        self.update_timers()

    def format_hms(self, seconds):
        s = int(seconds)
        hours = s // 3600
        minutes = (s % 3600) // 60
        secs = s % 60
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    def build_ui(self):
        main_frame = tb.Frame(self, padding=10)
        main_frame.pack(fill="both", expand=True)

        tb.Label(main_frame, text="Indexing all domains sequentially...", font=("Arial", 10, "bold")).pack(anchor="w", pady=(0, 8))

        container = tb.Frame(main_frame)
        container.pack(fill="both", expand=True, pady=(0, 10))

        canvas = tb.Canvas(container, highlightthickness=0)
        scrollbar = tb.Scrollbar(container, orient="vertical", command=canvas.yview)
        scrollable_frame = tb.Frame(canvas)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        for domain_name in self.domains.keys():
            row_frame = tb.Frame(scrollable_frame, padding=(2, 4))
            row_frame.pack(fill="x", expand=True)

            lbl = tb.Label(row_frame, text=domain_name, font=("Arial", 9), width=24, anchor="w")
            lbl.pack(side="left", padx=(0, 6))

            pbar_container = tb.Frame(row_frame, width=160, height=18)
            pbar_container.pack_propagate(False)
            pbar_container.pack(side="left", padx=6)

            pbar = tb.Progressbar(pbar_container, orient="horizontal", mode="determinate", maximum=100)
            pbar.pack(fill="both", expand=True)

            pct_lbl = tb.Label(pbar_container, text="0%", font=("Arial", 8, "bold"), bg="white", fg="black")
            pct_lbl.place(relx=0.5, rely=0.5, anchor="center")

            time_lbl = tb.Label(row_frame, text="00:00:00", font=("Arial", 9), width=10, anchor="e")
            time_lbl.pack(side="right", padx=(6, 0))

            self.domain_rows[domain_name] = {
                "label": lbl,
                "progress": pbar,
                "pct_label": pct_lbl,
                "time_label": time_lbl,
                "start_time": None,
                "elapsed": 0.0,
                "completed": False
            }

        bottom_frame = tb.Frame(main_frame)
        bottom_frame.pack(fill="x", pady=(4, 0))

        self.total_time_var = tk.StringVar(value="Total Elapsed: 00:00:00")
        total_time_lbl = tb.Label(bottom_frame, textvariable=self.total_time_var, font=("Arial", 9, "bold"))
        total_time_lbl.pack(side="right")

    def update_timers(self):
        if not self.is_running or not self.winfo_exists():
            return

        total_elapsed = time.time() - self.total_start_time
        self.total_time_var.set(f"Total Elapsed: {self.format_hms(total_elapsed)}")

        for data in self.domain_rows.values():
            if data["start_time"] is not None and not data["completed"]:
                elapsed = time.time() - data["start_time"]
                data["time_label"].configure(text=self.format_hms(elapsed))

        self.after(500, self.update_timers)

    def index_all_worker(self):
        for domain_name, root_url in self.domains.items():
            if not self.is_running or not self.winfo_exists():
                break

            row_data = self.domain_rows[domain_name]
            row_data["start_time"] = time.time()

            def progress_cb(scanned, discovered, total_files):
                if not self.winfo_exists():
                    return
                val = min(99.0, (scanned / max(1, discovered)) * 100)
                row_data["progress"]["value"] = val
                pct_str = f"{int(val)}%"
                row_data["pct_label"].configure(text=pct_str)

            try:
                all_entries = self.parent.crawl_domain_remote(
                    domain_name, root_url, report_progress=True, progress_callback=progress_cb, incremental=True
                )
                if all_entries is not None and self.is_running and self.winfo_exists():
                    self.parent.domain_full_entries = all_entries
                    self.parent.save_cache(domain_name)
            except Exception:
                pass

            if self.winfo_exists():
                elapsed = time.time() - row_data["start_time"]
                row_data["elapsed"] = elapsed
                row_data["completed"] = True
                row_data["progress"]["value"] = 100
                row_data["pct_label"].configure(text="100%")
                row_data["time_label"].configure(text=self.format_hms(elapsed))
                row_data["label"].configure(text=f"✓ {domain_name}", foreground="green")

        if self.is_running and self.winfo_exists():
            self.parent.after(0, self.finish_indexing)

    def finish_indexing(self):
        self.is_running = False
        try:
            messagebox.showinfo("Success", "All domains have been successfully indexed sequentially!")
        except Exception:
            pass
        self.destroy()

    def on_close(self):
        self.is_running = False
        self.destroy()


class DownloadProgressWindow(tb.Toplevel):
    def __init__(self, parent, title="Downloading..."):
        super().__init__(parent)
        self.title(title)
        self.geometry("500x140")
        self.resizable(False, False)
        self.transient(parent)
        self.is_cancelled = False

        try:
            icon_file = resource_path("assets/icons/app_icon.png")
            if icon_file.exists():
                self.iconphoto(False, tk.PhotoImage(file=str(icon_file)))
        except Exception:
            pass

        self.start_time = time.time()
        self.mode = "downloading"

        self.file_label_text = tk.StringVar(value="Preparing download...")
        tb.Label(self, textvariable=self.file_label_text, font=("Arial", 9)).pack(pady=(10, 5))
        
        pbar_container = tk.Frame(self, width=420, height=20)
        pbar_container.pack_propagate(False)
        pbar_container.pack(pady=2)

        self.pbar = tb.Progressbar(pbar_container, orient="horizontal", mode="determinate", maximum=100)
        self.pbar.pack(fill="both", expand=True)

        self.pct_label = tk.Label(pbar_container, text="0%", font=("Arial", 8, "bold"), bg="white", fg="black")
        self.pct_label.place(relx=0.5, rely=0.5, anchor="center")

        bottom_frame = tb.Frame(self)
        bottom_frame.pack(fill="x", padx=15, pady=(5, 10))

        self.data_info_var = tk.StringVar(value="0 / 0 bytes")
        self.data_lbl = tb.Label(bottom_frame, textvariable=self.data_info_var, font=("Arial", 8), width=18, anchor="w")
        self.data_lbl.pack(side="left")

        self.cancel_btn = tb.Button(bottom_frame, text="Cancel", command=self.cancel, width=10)
        self.cancel_btn.pack(side="left", expand=True)

        self.time_info_var = tk.StringVar(value="Elapsed: 00:00:00 | ETA: 00:00:00")
        self.time_lbl = tb.Label(bottom_frame, textvariable=self.time_info_var, font=("Arial", 8), width=24, anchor="e")
        self.time_lbl.pack(side="right")

    def format_size_1000(self, bytes_val):
        if bytes_val is None:
            return "0 bytes"
        if self.mode == "extracting":
            return f"{bytes_val} items"
        if bytes_val < 1000:
            return f"{bytes_val} B"
        elif bytes_val < 1000 * 1000:
            return f"{bytes_val / 1000.0:.1f} KB"
        elif bytes_val < 1000 * 1000 * 1000:
            return f"{bytes_val / (1000.0 * 1000.0):.1f} MB"
        else:
            return f"{bytes_val / (1000.0 * 1000.0 * 1000.0):.1f} GB"

    def format_hms(self, seconds):
        if seconds is None or seconds < 0 or seconds > 360000:
            return "00:00:00"
        s = int(seconds)
        hours = s // 3600
        minutes = (s % 3600) // 60
        secs = s % 60
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"

    def update_progress(self, filename, downloaded, total, c_idx, t_idx, speed, eta):
        if self.mode != "downloading":
            return
        d_str = self.format_size_1000(downloaded)
        t_str = self.format_size_1000(total) if total > 0 else "Unknown"
        self.file_label_text.set(f"Downloading: {filename}")
        self.data_info_var.set(f"{d_str} / {t_str}")
        
        elapsed = time.time() - self.start_time
        calculated_eta = 0
        if total > 0 and downloaded > 0:
            pct = downloaded / total
            if pct > 0:
                total_est_time = elapsed / pct
                calculated_eta = max(0.0, total_est_time - elapsed)

        self.time_info_var.set(f"Elap: {self.format_hms(elapsed)} | ETA: {self.format_hms(calculated_eta)}")

        if total > 0:
            pct_val = int((downloaded / total) * 100)
            self.pbar["value"] = pct_val
            self.pct_label.config(text=f"{pct_val}%")
        else:
            self.pbar["value"] = 0
            self.pct_label.config(text="0%")

    def start_extraction_mode(self):
        self.mode = "extracting"
        self.title("Extracting...")
        self.start_time = time.time()
        self.file_label_text.set("Extracting archive...")
        self.pbar["value"] = 0
        self.pct_label.config(text="0%")

    def update_extraction_progress(self, filename, current, total):
        if self.mode != "extracting":
            return
        self.file_label_text.set(f"Extracting: {filename}")
        self.data_info_var.set(f"{current} / {total} items")

        elapsed = time.time() - self.start_time
        calculated_eta = 0
        if total > 0 and current > 0:
            pct = current / total
            if pct > 0:
                total_est_time = elapsed / pct
                calculated_eta = max(0.0, total_est_time - elapsed)

        self.time_info_var.set(f"Elap: {self.format_hms(elapsed)} | ETA: {self.format_hms(calculated_eta)}")

        if total > 0:
            pct_val = int((current / total) * 100)
            self.pbar["value"] = pct_val
            self.pct_label.config(text=f"{pct_val}%")

    def cancel(self):
        self.is_cancelled = True
        self.destroy()