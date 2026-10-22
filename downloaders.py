import os
import shutil
import subprocess
import time
import urllib.error
from pathlib import Path
import threading
import tempfile
import urllib.request
import tkinter as tk
from tkinter import ttk, messagebox
import config


def check_and_download_sid_firmware():
    """Silently checks and downloads kernal and basic firmware files on startup if missing."""
    try:
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


def install_dependencies_prompt(parent):
    prompt_text = (
        "The Josh Archive Browser requires some dependencies to run its built-in music player properly, "
        "please hit OK to start installation! This will open a terminal window and ask for your Sudo password. "
        "Once entered, it will run:\n\n"
        "sudo apt update\n"
        "sudo apt install ... -y\n\n"
        "It will then grab the source files for Lazyusf, VGMStream, PlayGSF, 2sfPlay, SDL3, SDL3_ttf, NeoBAE, download the soundfont, and build them into ~/.config/joshw/.\n\n"
        "If you agree to this, hit the OK button to start, otherwise hit Cancel."
    )
    ans = messagebox.askokcancel("Install Dependencies", prompt_text)
    if ans:
        InstallDependenciesWorker(parent)


class InstallDependenciesWorker:
    def __init__(self, parent):
        self.parent = parent
        threading.Thread(target=self.run_installation, daemon=True).start()

    def run_installation(self):
        cache_dir = Path(config.get_cache_directory())
        cache_dir.mkdir(parents=True, exist_ok=True)
        sidplayfp_dir = Path(os.path.expanduser("~/.local/share/sidplayfp"))
        sidplayfp_dir.mkdir(parents=True, exist_ok=True)

        temp_build_dir = Path(tempfile.mkdtemp(prefix="joshw_dep_"))
        try:
            script_lines = []

            # 1. Base APT packages installation (including all required dev headers, dropping fluidsynth)
            apt_pkgs = [
                "git", "cmake", "libgme0", "build-essential", "sidplayfp", "xterm", "xdotool",
                "zlib1g-dev", "libao-dev", "libsdl2-dev", "pkg-config", "pulseaudio-utils",
                "libsndfile-dev", "libmp3lame-dev", "libogg-dev", "libvorbis-dev",
                "libopus-dev", "libopusfile-dev", "libflac-dev", "liblzma-dev", "libxmp-dev"
            ]
            script_lines.append("sudo apt update")
            script_lines.append(f"sudo apt install -y {' '.join(apt_pkgs)}")

            # 2. Lazyusf build
            lazyusf_bin = cache_dir / "lazyusf"
            if not lazyusf_bin.exists():
                lazyusf_src_dir = temp_build_dir / "lazyusf"
                script_lines.extend([
                    f"rm -rf \"{lazyusf_src_dir}\"",
                    f"git clone https://github.com/derselbst/lazyusf.git \"{lazyusf_src_dir}\"",
                    f"python3 -c \"import glob; "
                    f"target = r'printf(\\\"\\\\r%f\\\",(play_time/60000));'; "
                    f"replacement = 'printf(\\\"\\\\r%.7f\\\", play_time / 60000.0);\\\\nfflush(stdout);'; "
                    f"found = False; "
                    f"[open(f, 'w', encoding='utf-8').write(open(f, 'r', encoding='utf-8').read().replace(target, replacement)) or print(f'Patched {{f}}') or locals().update(found=True) "
                    f"for f in glob.glob('{lazyusf_src_dir}/**/audio.c', recursive=True) if target in open(f, 'r', encoding='utf-8').read()]; "
                    f"assert found, 'Patch failed'\"",
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
                playgsf_patch = Path(__file__).resolve().parent / "patches" / "playgsf-start-offset.patch"
                staged_playgsf_patch = temp_build_dir / "playgsf-start-offset.patch"
                if playgsf_patch.exists():
                    shutil.copy2(playgsf_patch, staged_playgsf_patch)
                    script_lines.extend([
                        f"cd \"{cache_dir}\"",
                        "git clone https://github.com/yshui/playgsf.git playgsfbuild",
                        f"cd playgsfbuild && git apply --unidiff-zero \"{staged_playgsf_patch}\" && ./configure || exit 1",
                        "make || true",
                        "g++ -fPIC -no-pie gsf.o VBA/GBA.o VBA/Globals.o VBA/Sound.o VBA/Util.o VBA/bios.o VBA/memgzio.o VBA/snd_interp.o VBA/unzip.o linuxmain.o VBA/psftag.o libresample/src/filterkit.o libresample/src/resample.o libresample/src/resamplesubs.o -lz -lao -lSDL2 -o playgsf",
                        f"mv playgsf \"{cache_dir}/\" && cd ..",
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
                    f"cd \"{twosf_workdir}/src/vio2sf\""
                ])
                # Write cst files and Makefiles inline into the build script
                script_lines.append(r"""cat > 2sftowav.c <<'EOF'
#include <locale.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <SDL.h>
#include "common.h"
#include "desmume/state.h"
static int16_t samples[4096];
static volatile sig_atomic_t stop_requested = 0;
static void handle_sigint(int signal_number) { (void)signal_number; stop_requested = 1; }
int main(int argc, char **argv) {
    setlocale(LC_ALL, "C");
    long start_time_ms = 0;
    const char *input_file = NULL;
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--help") == 0) { return 0; }
        if (strncmp(argv[i], "-b", 2) == 0) {
            char *end = NULL;
            start_time_ms = strtol(argv[i] + 2, &end, 10);
        } else if (input_file == NULL) {
            input_file = argv[i];
        }
    }
    if (input_file == NULL) return 1;
    struct twosf_loader_state state = {0};
    if (psf_load(input_file, &stdio_callbacks, 0x24, twosf_loader, &state, twosf_info, &state, 1, print_message, 0) <= 0) return 1;
    NDS_state core = {0};
    state_init(&core);
    core.dwInterpolation = 0;
    core.dwChannelMute = 0;
    state_setrom(&core, state.rom, state.rom_size, 0);
    state_loadstate(&core, state.state, state.state_size);
    long nsamples = state.length_ms * 441 / 10 + 1;
    long fade_samples = state.fade_ms * 441 / 10;
    long samples_to_skip = start_time_ms * 441 / 10;
    while (samples_to_skip > 0) {
        long to_skip = samples_to_skip < 2048 ? samples_to_skip : 2048;
        state_render(&core, samples, to_skip);
        samples_to_skip -= to_skip;
        nsamples -= to_skip;
    }
    SDL_Init(SDL_INIT_AUDIO);
    SDL_AudioSpec wanted = {0};
    wanted.freq = 44100;
    wanted.format = AUDIO_S16SYS;
    wanted.channels = 2;
    wanted.samples = 2048;
    SDL_AudioDeviceID device = SDL_OpenAudioDevice(NULL, 0, &wanted, NULL, 0);
    signal(SIGINT, handle_sigint);
    SDL_PauseAudioDevice(device, 0);
    while (nsamples > 0 && !stop_requested) {
        long to_render = nsamples < 2048 ? nsamples : 2048;
        state_render(&core, samples, to_render);
        if (fade_samples > 0 && nsamples <= fade_samples) {
            for (int i = 0; i < to_render * 2; i++) {
                double factor = (nsamples - i / 2) / (double)fade_samples;
                samples[i] = (int16_t)(samples[i] * factor);
            }
        }
        SDL_QueueAudio(device, samples, to_render * 2 * sizeof(int16_t));
        nsamples -= to_render;
        while (!stop_requested && SDL_GetQueuedAudioSize(device) > 44100 * 4 / 2) { SDL_Delay(10); }
    }
    SDL_CloseAudioDevice(device);
    SDL_Quit();
    return 0;
}
EOF""")
                script_lines.append(r"""cat > Makefile <<'EOF'
CFLAGS = -g -I. $(shell pkg-config --cflags sdl2)
CXXFLAGS = -g -I. -std=gnu++17
SDL_LIBS = $(shell pkg-config --libs sdl2)
OBJS = desmume/FIFO.o desmume/GPU.o desmume/MMU.o desmume/NDSSystem.o desmume/SPU.o desmume/arm_instructions.o desmume/armcpu.o desmume/barray.o desmume/bios.o desmume/cp15.o desmume/isqrt.o desmume/matrix.o desmume/mc.o desmume/resampler.o desmume/state.o desmume/thumb_instructions.o 2sftowav.o
all: libvio2sf.a 2sfplay
libvio2sf.a: $(OBJS)
	$(AR) rcs $@ $^
%.o: %.c
	$(CC) -c $(CFLAGS) -O3 -o $@ $<
%.o: %.cpp
	$(CXX) -c $(CXXFLAGS) -O3 -o $@ $<
2sfplay: 2sftowav.o libvio2sf.a ../psflib/libpsflib.a
	$(CXX) $(CXXFLAGS) -o $@ 2sftowav.o libvio2sf.a ../psflib/libpsflib.a -lz -lm $(SDL_LIBS)
EOF""")
                script_lines.extend([
                    "make clean || true",
                    "make",
                    f"install -m 755 2sfplay \"{twosf_play_bin}\""
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
                script_lines.append(f"wget -O \"{soundfont_file}\" https://github.com/pianobooster/fluid-soundfont/releases/download/v3.1/FluidR3_GM.sf2")

            nsf_tracks_bin = cache_dir / "nsf_tracks"
            if not nsf_tracks_bin.exists():
                script_lines.extend([
                    f"wget -O \"{nsf_tracks_bin}\" https://raw.githubusercontent.com/Aer0nic/joshw-browser/main/tools/nsf_tracks || true",
                    f"chmod +x \"{nsf_tracks_bin}\" || true"
                ])

            kernal_file = sidplayfp_dir / "kernal"
            if not kernal_file.exists():
                script_lines.append(f"wget -O \"{kernal_file}\" https://www.zimmers.net/anonftp/pub/cbm/firmware/computers/c64/kernal.901227-03.bin || true")

            basic_file = sidplayfp_dir / "basic"
            if not basic_file.exists():
                script_lines.append(f"wget -O \"{basic_file}\" https://www.zimmers.net/anonftp/pub/cbm/firmware/computers/c64/basic.901226-01.bin || true")

            # 8. Cleanup temp directory
            script_lines.append(f"rm -rf \"{temp_build_dir}\"")

            # Write the complete unified script using '&&' chaining so everything runs sequentially
            script_file = temp_build_dir / "install_deps.sh"
            with open(script_file, "w", encoding="utf-8") as f:
                f.write("#!/bin/bash\n")
                f.write("set -e\n")  # Exit immediately if any command fails
                f.write(" && \\\n".join(script_lines) + "\n")
                f.write("echo '----------------------------------------'\n")
                f.write("echo 'Installation complete. Press Enter to close.'\n")
                f.write("read\n")
            os.chmod(script_file, 0o755)

            # Launch terminal
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


def install_single_dependency(dep_name, parent):
    """Triggers an isolated build/download for a single dependency without running full system apt updates."""
    threading.Thread(target=_run_single_installer, args=(dep_name, parent), daemon=True).start()


def _run_single_installer(dep_name, parent):
    cache_dir = Path(config.get_cache_directory())
    cache_dir.mkdir(parents=True, exist_ok=True)
    temp_build_dir = Path(tempfile.mkdtemp(prefix=f"joshw_single_{dep_name}_"))

    try:
        script_lines = []
        
        if dep_name == "lazyusf":
            lazyusf_src_dir = temp_build_dir / "lazyusf"
            lazyusf_bin = cache_dir / "lazyusf"
            build_sh = temp_build_dir / "build_single.sh"
            
            with open(build_sh, "w", encoding="utf-8") as sf:
                sf.write("#!/bin/bash\n")
                sf.write(f"rm -rf \"{lazyusf_src_dir}\"\n")
                sf.write(f"git clone https://github.com/derselbst/lazyusf.git \"{lazyusf_src_dir}\"\n")
                sf.write(f"cd \"{lazyusf_src_dir}\"\n")
                
                sf.write("python3 - <<'EOF'\n")
                sf.write("import glob\n\n")
                sf.write("target = r'printf(\"\\r%f\",(play_time/60000));'\n")
                sf.write("replacement = 'printf(\"\\\\r%.7f\", play_time / 60000.0);\\nfflush(stdout);'\n\n")
                sf.write("found = False\n")
                sf.write("for f in glob.glob('**/audio.c', recursive=True):\n")
                sf.write("    with open(f, 'r', encoding='utf-8') as file:\n")
                sf.write("        content = file.read()\n\n")
                sf.write("    if target in content:\n")
                sf.write("        content = content.replace(target, replacement)\n")
                sf.write("        with open(f, 'w', encoding='utf-8') as file:\n")
                sf.write("            file.write(content)\n")
                sf.write("        print(f'Successfully patched {f}')\n")
                sf.write("        found = True\n\n")
                sf.write("if not found:\n")
                sf.write("    raise SystemExit('Patch failed: target text not found in any audio.c')\n")
                sf.write("EOF\n")

                sf.write("mkdir build && cd build\n")
                sf.write("cmake .. && make\n")
                sf.write(f"cp lazyusf \"{cache_dir}/\"\n")
                sf.write(f"chmod +x \"{lazyusf_bin}\"\n")
                sf.write("echo 'Lazyusf built successfully!'\n")
            
            os.chmod(build_sh, 0o755)
            script_lines.append(str(build_sh))

        if script_lines:
            terminal_script = temp_build_dir / "run_term.sh"
            with open(terminal_script, "w", encoding="utf-8") as tf:
                tf.write("#!/bin/bash\n" + "\n".join(script_lines) + "\nread -p 'Press Enter to close.'\n")
            os.chmod(terminal_script, 0o755)
            
            for term in ["gnome-terminal", "konsole", "xfce4-terminal", "xterm", "x-terminal-emulator"]:
                if shutil.which(term):
                    if term == "gnome-terminal":
                        subprocess.Popen([term, "--", "bash", "-c", str(terminal_script)])
                    elif term == "konsole":
                        subprocess.Popen([term, "-e", "bash", "-c", str(terminal_script)])
                    else:
                        subprocess.Popen([term, "-e", f"bash -c '{terminal_script}'"])
                    break
    except Exception as e:
        print(f"Single dependency build error: {e}")


class StepLogDialog(tk.Toplevel):
    def __init__(self, parent, title, error_message, terminal_cmd):
        super().__init__(parent)
        self.title(title)
        self.geometry("540x320")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()

        self.terminal_cmd = terminal_cmd

        ttk.Label(self, text="Premade Indexes Not Found", font=("Arial", 10, "bold"), foreground="red").pack(anchor="w", padx=12, pady=(12, 4))
        ttk.Label(self, text=error_message, font=("Arial", 9), wraplength=510, justify="left").pack(anchor="w", padx=12, pady=(0, 10))

        info_frame = ttk.Frame(self, padding=12)
        info_frame.pack(fill="both", expand=True)

        text_widget = tk.Text(info_frame, wrap="word", font=("Courier", 9), height=5)
        text_widget.pack(side="left", fill="both", expand=True)
        text_widget.insert("1.0", f"Command to be run in terminal:\n\n{self.terminal_cmd}")
        text_widget.configure(state="disabled")

        scrollbar = ttk.Scrollbar(info_frame, orient="vertical", command=text_widget.yview)
        scrollbar.pack(side="right", fill="y")
        text_widget.configure(yscrollcommand=scrollbar.set)

        btn_frame = ttk.Frame(self, padding=12)
        btn_frame.pack(fill="x")
        ttk.Button(btn_frame, text="OK", command=self.on_ok_clicked, width=12).pack(side="right")

    def on_ok_clicked(self):
        try:
            terminals = ["gnome-terminal", "konsole", "xfce4-terminal", "xterm"]
            launched = False
            for term in terminals:
                if shutil.which(term):
                    if term == "gnome-terminal":
                        subprocess.Popen([term, "--", "bash", "-c", f"{self.terminal_cmd}; echo 'Done. Press Enter to close.'; read"])
                    elif term == "konsole":
                        subprocess.Popen([term, "-e", "bash", "-c", f"{self.terminal_cmd}; echo 'Done. Press Enter to close.'; read"])
                    else:
                        subprocess.Popen([term, "-e", f"bash -c \"{self.terminal_cmd}; echo 'Done. Press Enter to close.'; read\""])
                    launched = True
                    break
            if not launched:
                subprocess.Popen(["x-terminal-emulator", "-e", f"bash -c \"{self.terminal_cmd}; echo 'Done. Press Enter to close.'; read\""])
        except Exception as e:
            messagebox.showerror("Error", f"Could not open terminal:\n\n{e}")
        
        self.destroy()


class IndexPremadeWindow:
    def __init__(self, parent, show_steps=False):
        self.parent = parent
        self.show_steps = show_steps
        threading.Thread(target=self.git_clone_worker, daemon=True).start()

    def git_clone_worker(self):
        cache_dir = Path(config.get_cache_directory())
        cache_dir.mkdir(parents=True, exist_ok=True)
        
        repo_dir = cache_dir / "joshw-browser"
        indexes_src_dir = repo_dir / "indexes"
        clone_url = "https://github.com/Aer0nic/joshw-browser.git"

        try:
            if repo_dir.exists():
                shutil.rmtree(repo_dir, ignore_errors=True)

            subprocess.run(["git", "clone", clone_url, str(repo_dir)], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

            if indexes_src_dir.exists():
                for f in indexes_src_dir.glob("domain_cache*"):
                    if f.is_file():
                        shutil.copy2(f, cache_dir / f.name)

            if repo_dir.exists():
                shutil.rmtree(repo_dir, ignore_errors=True)

            found_caches = list(cache_dir.glob("*domain_cache*"))

            if found_caches:
                if self.parent.winfo_exists():
                    self.parent.after(0, lambda: messagebox.showinfo(
                        "Success", 
                        "Premade indexes downloaded and installed successfully!"
                    ))
            else:
                terminal_cmd = f"cd {cache_dir} && git clone {clone_url} && cd joshw-browser/indexes/ && cp domain_cache* {cache_dir}"
                error_msg = "Premade index files containing 'domain_cache' were not found after cloning. Please do it manually. A terminal will now open when you click OK."
                if self.parent.winfo_exists():
                    self.parent.after(0, lambda: StepLogDialog(self.parent, "Installation Error", error_msg, terminal_cmd))

        except Exception as e:
            terminal_cmd = f"cd {cache_dir} && git clone {clone_url} && cd joshw-browser/indexes/ && cp domain_cache* {cache_dir}"
            error_msg = f"An error occurred during git clone or file copying:\n\n{e}\n\nPlease perform it manually. A terminal will now open when you click OK."
            if self.parent.winfo_exists():
                self.parent.after(0, lambda: StepLogDialog(self.parent, "Installation Error", error_msg, terminal_cmd))


class IndexAllWindow(tk.Toplevel):
    def __init__(self, parent, domains):
        super().__init__(parent)
        self.parent = parent
        self.domains = domains
        self.title("Index All Domains")
        self.geometry("580x520")
        self.minsize(520, 400)
        self.transient(parent)
        self.grab_set()

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
        main_frame = ttk.Frame(self, padding=10)
        main_frame.pack(fill="both", expand=True)

        ttk.Label(main_frame, text="Indexing all domains sequentially...", font=("Arial", 10, "bold")).pack(anchor="w", pady=(0, 8))

        container = ttk.Frame(main_frame)
        container.pack(fill="both", expand=True, pady=(0, 10))

        canvas = tk.Canvas(container, highlightthickness=0)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        scrollable_frame = ttk.Frame(canvas)

        scrollable_frame.bind(
            "<Configure>",
            lambda e: canvas.configure(scrollregion=canvas.bbox("all"))
        )
        canvas.create_window((0, 0), window=scrollable_frame, anchor="nw")
        canvas.configure(yscrollcommand=scrollbar.set)

        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        for domain_name in self.domains.keys():
            row_frame = ttk.Frame(scrollable_frame, padding=(2, 4))
            row_frame.pack(fill="x", expand=True)

            lbl = ttk.Label(row_frame, text=domain_name, font=("Arial", 9), width=24, anchor="w")
            lbl.pack(side="left", padx=(0, 6))

            pbar_container = tk.Frame(row_frame, width=160, height=18)
            pbar_container.pack_propagate(False)
            pbar_container.pack(side="left", padx=6)

            pbar = ttk.Progressbar(pbar_container, orient="horizontal", mode="determinate", maximum=100)
            pbar.pack(fill="both", expand=True)

            pct_lbl = tk.Label(pbar_container, text="0%", font=("Arial", 8, "bold"), bg="white", fg="black")
            pct_lbl.place(relx=0.5, rely=0.5, anchor="center")

            time_lbl = ttk.Label(row_frame, text="00:00:00", font=("Arial", 9), width=10, anchor="e")
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

        bottom_frame = ttk.Frame(main_frame)
        bottom_frame.pack(fill="x", pady=(4, 0))

        self.total_time_var = tk.StringVar(value="Total Elapsed: 00:00:00")
        total_time_lbl = ttk.Label(bottom_frame, textvariable=self.total_time_var, font=("Arial", 9, "bold"))
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


class DownloadProgressWindow(tk.Toplevel):
    def __init__(self, parent, title="Downloading..."):
        super().__init__(parent)
        self.title(title)
        self.geometry("460x140")
        self.resizable(False, False)
        self.transient(parent)
        self.is_cancelled = False

        self.start_time = time.time()
        self.mode = "downloading"

        self.file_label_text = tk.StringVar(value="Preparing download...")
        ttk.Label(self, textvariable=self.file_label_text, font=("Arial", 9)).pack(pady=(10, 5))
        
        pbar_container = tk.Frame(self, width=420, height=20)
        pbar_container.pack_propagate(False)
        pbar_container.pack(pady=2)

        self.pbar = ttk.Progressbar(pbar_container, orient="horizontal", mode="determinate", maximum=100)
        self.pbar.pack(fill="both", expand=True)

        self.pct_label = tk.Label(pbar_container, text="0%", font=("Arial", 8, "bold"), bg="white", fg="black")
        self.pct_label.place(relx=0.5, rely=0.5, anchor="center")

        bottom_frame = ttk.Frame(self)
        bottom_frame.pack(fill="x", padx=15, pady=(5, 10))

        self.data_info_var = tk.StringVar(value="0 / 0 bytes")
        self.data_lbl = ttk.Label(bottom_frame, textvariable=self.data_info_var, font=("Arial", 8), width=18, anchor="w")
        self.data_lbl.pack(side="left")

        self.cancel_btn = ttk.Button(bottom_frame, text="Cancel", command=self.cancel, width=10)
        self.cancel_btn.pack(side="left", expand=True)

        self.time_info_var = tk.StringVar(value="Elapsed: 00:00:00 | ETA: 00:00:00")
        self.time_lbl = ttk.Label(bottom_frame, textvariable=self.time_info_var, font=("Arial", 8), width=24, anchor="e")
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


check_and_download_sid_firmware()