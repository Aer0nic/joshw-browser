import os
import shutil
import subprocess
import time
from pathlib import Path
import threading
import tempfile
import urllib.request
import tkinter as tk
from tkinter import ttk, messagebox
from config import get_cache_directory, USER_AGENT


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
            except Exception:
                pass

        if not basic_file.exists():
            url_basic = "https://www.zimmers.net/anonftp/pub/cbm/firmware/computers/c64/basic.901226-01.bin"
            try:
                urllib.request.urlretrieve(url_basic, str(basic_file))
            except Exception:
                pass
    except Exception:
        pass


def install_dependencies_prompt(parent):
    prompt_text = (
        "The Josh Archive Browser requires some dependencies to run its built-in music player properly, "
        "please hit OK to start installation! This will open a terminal window and ask for your Sudo password. "
        "Once entered, it will run:\n\n"
        "sudo apt update\n"
        "sudo apt install git cmake libgme0 build-essential sidplayfp xterm xdotool zlib1g-dev libao-dev libsdl2-dev pkg-config pulseaudio-utils -y\n\n"
        "It will then grab the source files for Lazyusf, VGMStream, PlayGSF, 2sfPlay, download the FluidSynth soundfont, and build them into ~/.config/joshw/.\n\n"
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
        cache_dir = Path(get_cache_directory())
        cache_dir.mkdir(parents=True, exist_ok=True)
        sidplayfp_dir = Path(os.path.expanduser("~/.local/share/sidplayfp"))
        sidplayfp_dir.mkdir(parents=True, exist_ok=True)

        script_lines = [
            "sudo apt update",
            "sudo apt install git cmake libgme0 build-essential sidplayfp xterm xdotool zlib1g-dev libao-dev libsdl2-dev libflac-dev pkg-config pulseaudio-utils -y"
        ]

        temp_build_dir = Path(tempfile.mkdtemp(prefix="joshw_dep_"))
        try:
            lazyusf_bin = cache_dir / "lazyusf"
            if not lazyusf_bin.exists():
                lazyusf_src_dir = temp_build_dir / "lazyusf"
                lazyusf_sh_path = temp_build_dir / "build_lazyusf.sh"
                
                patcher_script_path = temp_build_dir / "patch_source.py"
                
                patcher_code = r'''import pathlib

src_dir = pathlib.Path(r"___SRC_DIR___")

# 1. Patch main.c to remove file extension suffix logic
main_c = src_dir / "main.c"
if main_c.exists():
    content = main_c.read_text(encoding="utf-8")
    
    old_target = """            if(formatStr)
            {
                formatOutFileName(filename, formatStr);
            }

            if(useFlac)
            {
                strcat(filename,".flac");
            }
            else
            {
                strcat(filename,".au");
            }"""
            
    new_target = """            if(formatStr)
            {
                formatOutFileName(filename, formatStr);
            }"""
            
    if old_target in content:
        content = content.replace(old_target, new_target)
        main_c.write_text(content, encoding="utf-8")
        print("Successfully patched main.c")
    else:
        print("Warning: Could not find target block in main.c")

# 2. Patch audio.c to redirect raw PCM output to stdout, add fixed-block buffering, and print stderr meta
audio_c = src_dir / "audio.c"
if audio_c.exists():
    content = audio_c.read_text(encoding="utf-8")
    
    # Replace the file writing block safely using exact substring matching
    if "open(" in content:
        start_idx = content.find("if (!useFlac)")
        if start_idx != -1:
            brace_count = 0
            end_idx = start_idx
            found_start = False
            for i, char in enumerate(content[start_idx:]):
                if char == '{':
                    brace_count += 1
                    found_start = True
                elif char == '}':
                    brace_count -= 1
                if found_start and brace_count == 0:
                    end_idx = start_idx + i + 1
                    break
            
            if end_idx > start_idx:
                replacement = """    if (!useFlac)
    {
        fd = STDOUT_FILENO;
        #ifdef _WIN32
        _setmode(_fileno(stdout), _O_BINARY);
        #endif
        static char stdout_buffer[8192];
        setvbuf(stdout, stdout_buffer, _IOFBF, sizeof(stdout_buffer));

        int endian_flag = 1;
        fprintf(stderr, "LAZYUSF_META:%d:%d\\n", SampleRate, endian_flag);
        fflush(stderr);
    }"""
                content = content[:start_idx] + replacement + content[end_idx:]

    # Inject fixed-block PCM buffering static variables and flush logic into audio.c
    if "static unsigned char pcm_out_buf" not in content:
        buffer_globals = """
static unsigned char pcm_out_buf[8192];
static size_t pcm_out_pos = 0;
"""
        include_pos = content.find("#include \"registers.h\"")
        if include_pos != -1:
            content = content[:include_pos] + buffer_globals + "\n" + content[include_pos:]

    # Replace the direct write(fd, buf, length) block inside AddBuffer with our fixed-block queue
    old_write_block = """        // write raw pcm to au file
        unsigned int status = write(fd, buf, length);
        if (status != length)
        {
            perror("wrote wrong number of bytes\\n");
        }"""

    new_write_block = """        // Write into fixed-block buffer to smooth out erratic N64 DMA chunk sizes
        size_t offset = 0;
        while (offset < length) {
            size_t chunk = length - offset;
            if (chunk > (sizeof(pcm_out_buf) - pcm_out_pos)) {
                chunk = sizeof(pcm_out_buf) - pcm_out_pos;
            }
            memcpy(pcm_out_buf + pcm_out_pos, buf + offset, chunk);
            pcm_out_pos += chunk;
            offset += chunk;

            if (pcm_out_pos >= 4096) {
                write(fd, pcm_out_buf, 4096);
                size_t leftover = pcm_out_pos - 4096;
                if (leftover > 0) {
                    memmove(pcm_out_buf, pcm_out_buf + 4096, leftover);
                }
                pcm_out_pos = leftover;
            }
        }"""

    if old_write_block in content:
        content = content.replace(old_write_block, new_write_block)

    # Safely replace CloseSound to flush buffer contents before closing/exiting
    old_close_sound = """void CloseSound(void)
{
    if(!useFlac && fd != STDOUT_FILENO)
    {
        close(fd);
    }"""

    new_close_sound = """void CloseSound(void)
{
    if (!useFlac && pcm_out_pos > 0) {
        write(fd, pcm_out_buf, pcm_out_pos);
        pcm_out_pos = 0;
    }

    if(!useFlac && fd != STDOUT_FILENO)
    {
        close(fd);
    }"""

    if old_close_sound in content:
        content = content.replace(old_close_sound, new_close_sound)

    audio_c.write_text(content, encoding="utf-8")
    print("Successfully patched audio.c with fixed-block PCM smoothing")
'''

                # Safely replace the placeholder with the actual source path string
                patcher_code = patcher_code.replace("___SRC_DIR___", str(lazyusf_src_dir))

                with open(patcher_script_path, "w", encoding="utf-8") as pf:
                    pf.write(patcher_code)

                # 2. Write the runner shell script
                with open(lazyusf_sh_path, "w", encoding="utf-8") as sf:
                    sf.write("#!/bin/bash\n")
                    sf.write(f"rm -rf \"{lazyusf_src_dir}\"\n")
                    sf.write(f"git clone https://github.com/derselbst/lazyusf.git \"{lazyusf_src_dir}\"\n")
                    sf.write(f"python3 \"{patcher_script_path}\"\n")
                    sf.write(f"cd \"{lazyusf_src_dir}\"\n")
                    sf.write("mkdir build && cd build\n")
                    sf.write("cmake .. && make\n")
                    sf.write(f"find . -name 'lazyusf' -type f -exec cp {{}} \"{cache_dir}\" \\;\n")
                    sf.write(f"chmod +x \"{lazyusf_bin}\"\n")
                
                os.chmod(lazyusf_sh_path, 0o755)
                script_lines.append(str(lazyusf_sh_path))

            vgmstream_bin = cache_dir / "vgmstream123"
            if not vgmstream_bin.exists():
                script_lines.append(f"git clone https://github.com/vgmstream/vgmstream.git {temp_build_dir / 'vgmstream'}")
                script_lines.append(f"cd {temp_build_dir / 'vgmstream'} && bash make-build-cmake.sh")
                script_lines.append(f"cp {temp_build_dir / 'vgmstream' / 'build' / 'cli' / 'vgmstream123'} {cache_dir}/")
                script_lines.append(f"chmod +x {vgmstream_bin}")

            playgsf_bin = cache_dir / "playgsf"
            playgsf_needs_seek_patch = not playgsf_bin.exists()
            if playgsf_bin.exists():
                try:
                    playgsf_help = subprocess.run(
                        [str(playgsf_bin), "-h"],
                        check=True,
                        capture_output=True,
                        text=True,
                        timeout=3
                    ).stdout
                    playgsf_needs_seek_patch = "-b<num>" not in playgsf_help
                except (OSError, subprocess.SubprocessError):
                    playgsf_needs_seek_patch = True

            if playgsf_needs_seek_patch:
                playgsf_patch = Path(__file__).resolve().parent / "patches" / "playgsf-start-offset.patch"
                staged_playgsf_patch = temp_build_dir / "playgsf-start-offset.patch"
                shutil.copy2(playgsf_patch, staged_playgsf_patch)
                script_lines.append(f"cd {cache_dir}")
                script_lines.append("git clone https://github.com/yshui/playgsf.git playgsfbuild")
                script_lines.append(f'cd playgsfbuild && git apply --unidiff-zero "{staged_playgsf_patch}" && ./configure || exit 1')
                script_lines.append("make || true")
                script_lines.append("g++ -fPIC -no-pie "
    "gsf.o VBA/GBA.o VBA/Globals.o VBA/Sound.o VBA/Util.o "
    "VBA/bios.o VBA/memgzio.o VBA/snd_interp.o VBA/unzip.o "
    "linuxmain.o VBA/psftag.o "
    "libresample/src/filterkit.o libresample/src/resample.o "
    "libresample/src/resamplesubs.o "
    "-lz -lao -lSDL2 -o playgsf")

                script_lines.append(f"mv playgsf {cache_dir}/ && cd ..")
                script_lines.append(f"rm -rf playgsfbuild")
                script_lines.append(f"chmod +x {playgsf_bin}")

            twosf_play_bin = cache_dir / "2sfPlay"
            twosf_needs_seek_support = not twosf_play_bin.exists()
            if twosf_play_bin.exists():
                try:
                    twosf_help = subprocess.run(
                        [str(twosf_play_bin), "--help"],
                        capture_output=True,
                        text=True,
                        timeout=3
                    ).stdout
                    twosf_needs_seek_support = "-b<ms>" not in twosf_help
                except (OSError, subprocess.SubprocessError):
                    twosf_needs_seek_support = True

            if twosf_needs_seek_support:
                twosf_workdir = temp_build_dir / "2sftowav_source"
                script_lines.append(f"git clone --recurse-submodules https://github.com/yshui/2sftowav.git {twosf_workdir}")
                script_lines.append(f"cd {twosf_workdir / 'src' / 'vio2sf'}")
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

static void handle_sigint(int signal_number)
{
	(void)signal_number;
	stop_requested = 1;
}

int main(int argc, char **argv)
{
	setlocale(LC_ALL, "C");
    long start_time_ms = 0;
    const char *input_file = NULL;

    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--help") == 0) {
            printf("Usage: %s [-b<ms>] <input.mini2sf>\n", argv[0]);
            printf("  -b<ms>  Start playback at the specified millisecond\n");
            return 0;
        }
        if (strncmp(argv[i], "-b", 2) == 0) {
            char *end = NULL;
            start_time_ms = strtol(argv[i] + 2, &end, 10);
            if (end == argv[i] + 2 || *end != '\0' || start_time_ms < 0) {
                fprintf(stderr, "Invalid start time: %s\n", argv[i]);
                return 1;
            }
        } else if (input_file == NULL) {
            input_file = argv[i];
        } else {
            fprintf(stderr, "Usage: %s [-b<ms>] <input.mini2sf>\n", argv[0]);
            return 1;
        }
    }

    if (input_file == NULL) {
        fprintf(stderr, "Usage: %s [-b<ms>] <input.mini2sf>\n", argv[0]);
		return 1;
	}

	struct twosf_loader_state state = {0};

    if (psf_load(input_file, &stdio_callbacks, 0x24, twosf_loader, &state,
	             twosf_info, &state, 1, print_message, 0) <= 0) {
        fprintf(stderr, "Failed to load 2sf: %s\n", input_file);
		return 1;
	}

	if (state.length_ms <= 0) {
		fprintf(stderr,
		        "Input file has an invalid or missing length tag.\n");
		return 1;
	}

	NDS_state core = {0};

	if (state_init(&core)) {
		fprintf(stderr, "Failed to init NDS state\n");
		return 1;
	}

	core.dwInterpolation = 0;
	core.dwChannelMute = 0;

	if (!state.arm7_clockdown_level)
		state.arm7_clockdown_level = state.clockdown;
	if (!state.arm9_clockdown_level)
		state.arm9_clockdown_level = state.clockdown;

	core.initial_frames = state.initial_frames;
	core.sync_type = state.sync_type;
	core.arm7_clockdown_level = state.arm7_clockdown_level;
	core.arm9_clockdown_level = state.arm9_clockdown_level;

	if (state.rom)
		state_setrom(&core, state.rom, state.rom_size, 0);

	state_loadstate(&core, state.state, state.state_size);

	long nsamples = state.length_ms * 441 / 10 + 1;
	long fade_samples = state.fade_ms * 441 / 10;
	long total = nsamples;
    long samples_to_skip = start_time_ms * 441 / 10;
    if (samples_to_skip > nsamples)
        samples_to_skip = nsamples;
    while (samples_to_skip > 0) {
        long to_skip = samples_to_skip < 2048 ? samples_to_skip : 2048;
        state_render(&core, samples, to_skip);
        samples_to_skip -= to_skip;
        nsamples -= to_skip;
    }

	if (SDL_Init(SDL_INIT_AUDIO) < 0) {
		fprintf(stderr, "SDL audio init failed: %s\n", SDL_GetError());
		return 1;
	}

	SDL_AudioSpec wanted = {0};
	wanted.freq = 44100;
	wanted.format = AUDIO_S16SYS;
	wanted.channels = 2;
	wanted.samples = 2048;

	SDL_AudioDeviceID device =
	    SDL_OpenAudioDevice(NULL, 0, &wanted, NULL, 0);

	if (!device) {
		fprintf(stderr, "Could not open audio device: %s\n", SDL_GetError());
		SDL_Quit();
		return 1;
	}

	signal(SIGINT, handle_sigint);
	SDL_PauseAudioDevice(device, 0);

	int runs = 0;

	while (nsamples > 0 && !stop_requested) {
		long to_render = nsamples < 2048 ? nsamples : 2048;

		state_render(&core, samples, to_render);

		if (fade_samples > 0 && nsamples <= fade_samples) {
			for (int i = 0; i < to_render * 2; i++) {
				double factor =
				    (nsamples - i / 2) / (double)fade_samples;
				if (factor < 0.0)
					factor = 0.0;
				samples[i] = (int16_t)(samples[i] * factor);
			}
		}

		Uint32 bytes = (Uint32)(to_render * 2 * sizeof(int16_t));

		if (SDL_QueueAudio(device, samples, bytes) < 0) {
			fprintf(stderr, "\nAudio queue failed: %s\n",
			        SDL_GetError());
			break;
		}

		nsamples -= to_render;

		/* Keep no more than about half a second queued. */
		while (!stop_requested &&
		       SDL_GetQueuedAudioSize(device) > 44100 * 4 / 2) {
			SDL_Delay(10);
		}

		if (++runs == 10) {
			fprintf(stderr, "\rRemaining: %lds / %lds",
			        nsamples / 44100, (total + 450) / 44100);
			fflush(stderr);
			runs = 0;
		}
	}

	while (!stop_requested && SDL_GetQueuedAudioSize(device) > 0)
		SDL_Delay(50);

	SDL_ClearQueuedAudio(device);
	SDL_CloseAudioDevice(device);
	SDL_Quit();

	fprintf(stderr, "\n");
	return 0;
}
EOF""")
                script_lines.append(r"""cat > Makefile <<'EOF'
CFLAGS = -g -I. $(shell pkg-config --cflags sdl2)
CXXFLAGS = -g -I. -std=gnu++17

SDL_LIBS = $(shell pkg-config --libs sdl2)

OBJS = desmume/FIFO.o desmume/GPU.o desmume/MMU.o desmume/NDSSystem.o desmume/SPU.o desmume/arm_instructions.o desmume/armcpu.o desmume/barray.o desmume/bios.o desmume/cp15.o desmume/isqrt.o desmume/matrix.o desmume/mc.o desmume/resampler.o desmume/state.o desmume/thumb_instructions.o 2sftowav.o

OPTS = -O3

all: libvio2sf.a 2sfplay

../psflib/libpsflib.a:
	$(MAKE) -C ../psflib

libvio2sf.a: $(OBJS)
	$(AR) rcs $@ $^

trim2sflib: trim2sflib.o libvio2sf.a ../psflib/libpsflib.a common.h
	$(CXX) -o $@ $^ -lz

%.o: %.c
	$(CC) -c $(CFLAGS) $(OPTS) -o $@ $<

%.o: %.cpp
	$(CXX) -c $(CXXFLAGS) $(OPTS) -o $@ $<

clean:
	rm -f $(OBJS) libvio2sf.a trim2sflib.o trim2sflib 2sfplay
	$(MAKE) -C ../psflib clean

2sfplay: 2sftowav.o libvio2sf.a ../psflib/libpsflib.a
	$(CXX) $(CXXFLAGS) -o $@ 2sftowav.o libvio2sf.a ../psflib/libpsflib.a -lz -lm $(SDL_LIBS)
EOF""")
                script_lines.append("make clean")
                script_lines.append("make")
                script_lines.append(f"install -m 755 2sfplay \"{cache_dir / '2sfPlay'}\"")

            soundfont_file = cache_dir / "FluidR3_GM.sf2"
            if not soundfont_file.exists():
                script_lines.append(f"wget -O {soundfont_file} https://github.com/pianobooster/fluid-soundfont/releases/download/v3.1/FluidR3_GM.sf2")

            nsf_tracks_bin = cache_dir / "nsf_tracks"
            if not nsf_tracks_bin.exists():
                script_lines.append(f"wget -O {nsf_tracks_bin} https://raw.githubusercontent.com/Aer0nic/joshw-browser/main/tools/nsf_tracks || echo 'nsf_tracks fetch optional'")
                script_lines.append(f"chmod +x {nsf_tracks_bin} || true")

            kernal_file = sidplayfp_dir / "kernal"
            if not kernal_file.exists():
                script_lines.append(f"wget -O {kernal_file} https://www.zimmers.net/anonftp/pub/cbm/firmware/computers/c64/kernal.901227-03.bin")

            basic_file = sidplayfp_dir / "basic"
            if not basic_file.exists():
                script_lines.append(f"wget -O {basic_file} https://www.zimmers.net/anonftp/pub/cbm/firmware/computers/c64/basic.901226-01.bin")

            script_file = temp_build_dir / "install_deps.sh"
            with open(script_file, "w", encoding="utf-8") as f:
                f.write("#!/bin/bash\n")
                f.write("\n".join(script_lines) + "\n")
                f.write("echo '----------------------------------------'\n")
                f.write("echo 'Installation complete. Press Enter to close.'\n")
                f.write("read\n")
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
        cache_dir = Path(get_cache_directory())
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
        self.mode = "downloading" # downloading or extracting

        self.file_label_text = tk.StringVar(value="Preparing download...")
        ttk.Label(self, textvariable=self.file_label_text, font=("Arial", 9)).pack(pady=(10, 5))
        
        pbar_container = tk.Frame(self, width=420, height=20)
        pbar_container.pack_propagate(False)
        pbar_container.pack(pady=2)

        self.pbar = ttk.Progressbar(pbar_container, orient="horizontal", mode="determinate", maximum=100)
        self.pbar.pack(fill="both", expand=True)

        self.pct_label = tk.Label(pbar_container, text="0%", font=("Arial", 8, "bold"), bg="white", fg="black")
        self.pct_label.place(relx=0.5, rely=0.5, anchor="center")

        # Bottom controls container layout: [Data info label] [Cancel button] [Elapsed / ETA label]
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