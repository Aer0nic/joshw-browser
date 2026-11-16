#!/usr/bin/env python3
"""
Build the small format decoders used on Windows (they write WAV to stdout;
the app plays it via platform_support.PcmPipeProcess).

  python tools/windows_decoders/build.py <output_dir> [--native] [name ...]

--native builds Linux binaries with the host compiler instead (for testing
the decoders on a Linux machine). Default target: Windows x64 via mingw-w64.

Decoders:
  2sfdec.exe  Nintendo DS (.2sf/.mini2sf)  core: vio2sf + psflib
              source: github.com/yshui/2sftowav (pinned commit below)
  gsfdec.exe  Game Boy Advance (.gsf/.minigsf)  core: playgsf (VBA)
              source: github.com/yshui/playgsf (pinned commit below)
  usfdec.exe  Nintendo 64 (.usf/.miniusf)  core: lazyusf2 (mupen64plus) + psflib
              source: github.com/derselbst/lazyusf2 (pinned commit below)
  aosdk.dll   PlayStation 1/2 (.psf/.minipsf/.psf2) - loaded by psf_ctypes.py
              core: Audio Overload SDK 1.4.8 PSF/PSF2 engines (PEOpS SPU)
              source: github.com/DeaDBeeF-Player/deadbeef plugins/psf/aosdk
              (pinned commit below) + tools/windows_decoders/aosdk_wrapper.c
  hvl2wav.exe AHX / HivelyTracker (.ahx/.hvl) -> WAV file  (BSD-3-Clause)
              source: github.com/pete-gordon/hivelytracker hvl2wav/ (pinned commit below)
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
TWOSF_REPO = "https://github.com/yshui/2sftowav.git"
TWOSF_COMMIT = "9e3124f"
GSF_REPO = "https://github.com/yshui/playgsf.git"
GSF_COMMIT = "ad37147"
USF_REPO = "https://github.com/derselbst/lazyusf2.git"
USF_COMMIT = "00d19c9"
PSFLIB_REPO = "https://github.com/kode54/psflib.git"
PSFLIB_COMMIT = "6cb3515"  # same psflib revision 2sftowav uses
AOSDK_REPO = "https://github.com/DeaDBeeF-Player/deadbeef.git"
AOSDK_COMMIT = "47de75b641e92f800166a2a5fb0d1a78bd86a502"
HVL_REPO = "https://github.com/pete-gordon/hivelytracker.git"
HVL_COMMIT = "f393ca7c6416f00bcb574b334a7e8b57dcb19eb2"
# PSF + PSF2 engines only (the Saturn/Dreamcast/QSound engines aren't used);
# adsr/dma/registers/reverb are #included by spu.c / spu2.c
AOSDK_SOURCES = """corlett.c eng_psf/eng_psf.c eng_psf/eng_psf2.c eng_psf/psx.c
eng_psf/psx_hw.c eng_psf/peops/spu.c eng_psf/peops2/spu2.c eng_psf/peops2/dma2.c
eng_psf/peops2/registers2.c""".split()

# lazyusf2 Makefile OBJS, minus the x86 recompiler (interpreter build: portable)
USF_SOURCES = """ai/ai_controller api/callbacks debugger/dbg_decoder main/main main/rom
main/savestates main/util memory/memory pi/cart_rom pi/pi_controller r4300/cached_interp
r4300/cp0 r4300/cp1 r4300/exception r4300/interupt r4300/mi_controller r4300/pure_interp
r4300/r4300 r4300/r4300_core r4300/recomp r4300/reset r4300/tlb r4300/empty_dynarec
rdp/rdp_core ri/rdram ri/rdram_detection_hack ri/ri_controller rsp/rsp_core rsp_hle/alist
rsp_hle/alist_audio rsp_hle/alist_naudio rsp_hle/alist_nead rsp_hle/audio rsp_hle/cicx105
rsp_hle/hle rsp_hle/hvqm rsp_hle/jpeg rsp_hle/memory rsp_hle/mp3 rsp_hle/musyx
rsp_hle/plugin rsp_hle/re2 rsp_lle/rsp si/cic si/game_controller si/n64_cic_nus_6105
si/pif si/si_controller usf/usf usf/barray usf/resampler vi/vi_controller""".split()


def run(cmd, cwd=None):
    print("[build]", " ".join(str(c) for c in cmd) if len(cmd) < 8 else f"{cmd[0]} ... ({len(cmd)} args)")
    subprocess.run([str(c) for c in cmd], cwd=cwd, check=True)


def compilers(native):
    if native:
        return "gcc", "g++", []
    for tool in ("x86_64-w64-mingw32-gcc", "x86_64-w64-mingw32-g++"):
        if not shutil.which(tool):
            sys.exit(f"{tool} not found: install mingw-w64 (or use --native)")
    # fully static: no libgcc/libstdc++/winpthread/zlib DLLs needed next to the exe
    return "x86_64-w64-mingw32-gcc", "x86_64-w64-mingw32-g++", ["-static", "-s"]


def build_2sfdec(work, out_dir, native):
    cc, cxx, link_flags = compilers(native)
    src = work / "2sftowav"
    run(["git", "clone", "--recurse-submodules", TWOSF_REPO, src])
    run(["git", "checkout", TWOSF_COMMIT], cwd=src)
    run(["git", "submodule", "update", "--init", "--recursive"], cwd=src)

    vio = src / "src" / "vio2sf"
    psflib = src / "src" / "psflib"
    objs = []
    cflags = ["-O2", "-I", vio, "-I", psflib, "-I", src / "src"]
    for c in sorted((vio / "desmume").glob("*.c")) + [psflib / "psflib.c", psflib / "psf2fs.c"]:
        o = work / (c.stem + "_" + c.parent.name + ".o")
        run([cc, "-c", *cflags, "-o", o, c])
        objs.append(o)
    for cpp in sorted((vio / "desmume").glob("*.cpp")):
        o = work / (cpp.stem + "_cpp.o")
        run([cxx, "-c", "-std=gnu++17", *cflags, "-o", o, cpp])
        objs.append(o)
    main_o = work / "2sfdec.o"
    run([cc, "-c", *cflags, "-o", main_o, HERE / "2sfdec.c"])

    exe = out_dir / ("2sfdec" if native else "2sfdec.exe")
    run([cxx, "-o", exe, main_o, *objs, "-lz", "-lm", *link_flags])
    return exe


def build_gsfdec(work, out_dir, native):
    cc, cxx, link_flags = compilers(native)
    src = work / "playgsf"
    run(["git", "clone", GSF_REPO, src])
    run(["git", "checkout", GSF_COMMIT], cwd=src)
    # Same flags as playgsf's Makefile (the VBA core expects -DLINUX)
    # -DC_CORE: portable C instead of 32-bit-address x86 inline asm (breaks on x64 Windows)
    flags = ["-DLINUX", "-DC_CORE", "-I", src, "-I", src / "VBA", "-I", src / "libresample" / "include",
             '-DVERSION_STR="0.07"', '-DHA_VERSION_STR="0.11"', "-O2", "-fpermissive",
             "-w"]
    sources = [src / "gsf.cpp"] + [src / "VBA" / f for f in (
        "GBA.cpp", "Globals.cpp", "Sound.cpp", "Util.cpp", "bios.cpp", "memgzio.c",
        "snd_interp.cpp", "unzip.cpp", "psftag.c")] + [
        src / "libresample" / "src" / f for f in ("filterkit.c", "resample.c", "resamplesubs.c")]
    objs = []
    for i, f in enumerate(sources):
        o = work / f"gsf_{i}_{f.stem}.o"
        compiler = cxx if f.suffix == ".cpp" else cc
        extra = ["-std=gnu++14"] if f.suffix == ".cpp" else []
        run([compiler, "-c", *flags, *extra, "-o", o, f])
        objs.append(o)
    main_o = work / "gsfdec.o"
    run([cxx, "-c", *flags, "-std=gnu++14", "-o", main_o, HERE / "gsfdec.cpp"])
    exe = out_dir / ("gsfdec" if native else "gsfdec.exe")
    # VBA's inline asm uses absolute addresses (playgsf links with -no-pie too)
    pie = ["-no-pie"] if native else []
    run([cxx, "-o", exe, main_o, *objs, "-lz", "-lm", *pie, *link_flags])
    return exe


def build_usfdec(work, out_dir, native):
    cc, cxx, link_flags = compilers(native)
    src = work / "lazyusf2"
    run(["git", "clone", USF_REPO, src])
    run(["git", "checkout", USF_COMMIT], cwd=src)
    psflib = work / "psflib"
    run(["git", "clone", PSFLIB_REPO, psflib])
    run(["git", "checkout", PSFLIB_COMMIT], cwd=psflib)

    flags = ["-O2", "-I", src, "-I", psflib, "-DARCH_MIN_SSE2", "-w"]
    objs = []
    for rel in USF_SOURCES:
        c = src / (rel + ".c")
        o = work / (rel.replace("/", "_") + ".o")
        run([cc, "-c", *flags, "-o", o, c])
        objs.append(o)
    for c in (psflib / "psflib.c", psflib / "psf2fs.c"):
        o = work / ("psflib_" + c.stem + ".o")
        run([cc, "-c", *flags, "-o", o, c])
        objs.append(o)
    main_o = work / "usfdec.o"
    run([cc, "-c", *flags, "-o", main_o, HERE / "usfdec.c"])
    exe = out_dir / ("usfdec" if native else "usfdec.exe")
    run([cc, "-o", exe, main_o, *objs, "-lz", "-lm", *link_flags])
    return exe


def build_aosdk(work, out_dir, native):
    cc, _cxx, link_flags = compilers(native)
    src = work / "deadbeef"
    # Only plugins/psf is needed out of the whole DeaDBeeF tree
    run(["git", "clone", "--filter=blob:none", "--no-checkout", AOSDK_REPO, src])
    run(["git", "sparse-checkout", "set", "--no-cone", "plugins/psf/"], cwd=src)
    run(["git", "checkout", AOSDK_COMMIT], cwd=src)
    ao = src / "plugins" / "psf" / "aosdk"
    flags = ["-O2", "-DHAS_PSXCPU=1", "-I", ao, "-I", ao / "eng_psf", "-w",
             "-fno-strict-aliasing"]
    if native:
        flags.append("-fPIC")
    objs = []
    for rel in AOSDK_SOURCES:
        o = work / ("ao_" + rel.replace("/", "_").replace(".c", ".o"))
        run([cc, "-c", *flags, "-o", o, ao / rel])
        objs.append(o)
    main_o = work / "aosdk_wrapper.o"
    run([cc, "-c", *flags, "-o", main_o, HERE / "aosdk_wrapper.c"])
    if native:
        lib = out_dir / "libaosdk.so"
        run([cc, "-shared", "-o", lib, main_o, *objs, "-lz", "-lm"])
    else:
        lib = out_dir / "aosdk.dll"
        # zlib/libgcc linked in statically: the DLL only needs system DLLs
        run([cc, "-shared", "-o", lib, main_o, *objs, "-Wl,-Bstatic", "-lz",
             "-static-libgcc", "-s", "-Wl,--exclude-libs,ALL"])
    return lib


def build_hvl2wav(work, out_dir, native):
    cc, _cxx, link_flags = compilers(native)
    src = work / "hivelytracker"
    # Only hvl2wav/ (converter + replayer) is needed, not the tracker itself
    run(["git", "clone", "--filter=blob:none", "--no-checkout", HVL_REPO, src])
    run(["git", "sparse-checkout", "set", "--no-cone", "hvl2wav/"], cwd=src)
    run(["git", "checkout", HVL_COMMIT], cwd=src)
    hv = src / "hvl2wav"
    # _BIG_ENDIAN_ only switches the WAV header writer to byte-by-byte output,
    # which is correct on every platform (upstream's Linux makefile sets it too)
    flags = ["-O2", "-w", "-D_BIG_ENDIAN_", "-I", hv]
    objs = []
    for name in ("hvl2wav.c", "replay.c"):
        o = work / name.replace(".c", ".o")
        run([cc, "-c", *flags, "-o", o, hv / name])
        objs.append(o)
    exe = out_dir / ("hvl2wav" if native else "hvl2wav.exe")
    run([cc, "-o", exe, *objs, "-lm", *link_flags])
    return exe


BUILDERS = {"2sfdec": "build_2sfdec", "gsfdec": "build_gsfdec", "usfdec": "build_usfdec",
            "aosdk": "build_aosdk", "hvl2wav": "build_hvl2wav"}


def main(argv):
    if not argv:
        sys.exit(__doc__)
    out_dir = Path(argv[0]).resolve()
    native = "--native" in argv
    only = [a for a in argv[1:] if not a.startswith("--")]  # e.g. "aosdk gsfdec"
    builders = [globals()[BUILDERS[n]] for n in (only or BUILDERS)]
    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="joshw_decoders_") as tmp:
        for builder in builders:
            work = Path(tmp) / builder.__name__
            work.mkdir()
            print(f"[build] built {builder(work, out_dir, native)}")
    if native:
        return  # local test/Linux builds: no SOURCE.txt next to the user's binaries
    (out_dir / "SOURCE.txt").write_text(
        f"2sfdec: {TWOSF_REPO} @ {TWOSF_COMMIT} + tools/windows_decoders/2sfdec.c\n"
        f"gsfdec: {GSF_REPO} @ {GSF_COMMIT} + tools/windows_decoders/gsfdec.cpp\n"
        f"usfdec: {USF_REPO} @ {USF_COMMIT} + {PSFLIB_REPO} @ {PSFLIB_COMMIT} + tools/windows_decoders/usfdec.c\n"
        f"aosdk.dll: {AOSDK_REPO} @ {AOSDK_COMMIT} (plugins/psf/aosdk, AOSDK 1.4.8: BSD-3-Clause;"
        f" PEOpS SPU: GPL-2.0+) + tools/windows_decoders/aosdk_wrapper.c\n"
        f"hvl2wav.exe: {HVL_REPO} @ {HVL_COMMIT} (hvl2wav/, BSD-3-Clause)\n"
    )


if __name__ == "__main__":
    main(sys.argv[1:])
