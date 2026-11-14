#!/usr/bin/env python3
"""
Populate assets/libraries/windows/ with the Windows playback backends.

Run this on the maintainer's machine (Linux with mingw-w64 + cmake + 7z, or
Windows with 7-Zip on PATH for the download-only steps) whenever a backend
should be updated, then commit the result. The app and the PyInstaller build
only ever *read* from assets/libraries/windows/.

  python tools/fetch_windows_deps.py            # everything
  python tools/fetch_windows_deps.py vgmstream mpv   # only some tools

Each tool lands in its own folder (an .exe's DLLs must sit next to it):

  tool        source                                             license
  ----------  -------------------------------------------------  --------------
  vgmstream   github.com/vgmstream/vgmstream  (official win64)   ISC + bundled libs
  sidplayfp   github.com/libsidplayfp/sidplayfp (official ucrt64) GPL-2.0
  vgmplay     github.com/vgmrips/vgmplay-legacy (official)        GPL-2.0
  neobae      github.com/zefie/NeoBAE (official suite x86_64)     see repo
  mpv         github.com/shinchiro/mpv-winbuild-cmake (latest)    GPL-2.0+/LGPL
  7zip        github.com/ip7z/7zip  (official 7za.exe x64)        LGPL-2.1 + unRAR
  gme         github.com/libgme/game-music-emu  (cross-compiled)  LGPL-2.1
  decoders    tools/windows_decoders/ (cross-compiled, see there) GPL / per core

Sources are pinned below; bump the versions here to update.
"""
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "assets" / "libraries" / "windows"
USER_AGENT = "joshw-browser-deps-fetcher"

PINNED = {
    "vgmstream": ("vgmstream/vgmstream", "r2168", "vgmstream-win64.zip"),
    "sidplayfp": ("libsidplayfp/sidplayfp", "v3.2.2", "sidplayfp-3.2.2-ucrt64.zip"),
    "vgmplay": ("vgmrips/vgmplay-legacy", "0.40.9", "VGMPlay_040-9.7z"),
    "neobae": ("zefie/NeoBAE", "zefie-release-2026.11.05.2",
               "neobae-suite_windows_x86_64_zefie-release-2026.11.05.2.zip"),
    "7zip": ("ip7z/7zip", "26.06", "7z2606-extra.7z"),
    "gme": ("libgme/game-music-emu", "0.6.5", "libgme-0.6.5-src.tar.gz"),
}
MPV_REPO = "shinchiro/mpv-winbuild-cmake"  # rolling builds; newest x86_64 is used

# Which files of each extracted package to keep (glob patterns, relative)
KEEP = {
    "vgmstream": ["vgmstream-cli.exe", "*.dll", "COPYING*", "LICENSE*", "*.txt"],
    "sidplayfp": ["sidplayfp.exe", "*.dll", "COPYING*", "AUTHORS*", "README*"],
    "vgmplay": ["VGMPlay.exe", "VGMPlay.ini", "zlib1.dll", "*.txt"],
    "neobae": ["playbae.exe", "libneobae.dll", "*.dll", "LICENSE*", "*.txt"],
    "7zip": ["x64/7za.exe", "x64/7za.dll", "x64/7zxa.dll", "License.txt", "readme.txt"],
    "mpv": ["mpv.exe", "LICENSE*", "COPYING*"],  # d3dcompiler is video-only
}


def log(msg):
    print(f"[fetch] {msg}", flush=True)


def download(url, target):
    log(f"downloading {url}")
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=120) as resp, open(target, "wb") as out:
        shutil.copyfileobj(resp, out)
    return target


def release_asset_url(repo, tag, asset):
    return f"https://github.com/{repo}/releases/download/{tag}/{asset}"


def latest_mpv_asset():
    req = urllib.request.Request(f"https://api.github.com/repos/{MPV_REPO}/releases/latest",
                                 headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=60) as resp:
        rel = json.load(resp)
    for a in rel["assets"]:
        n = a["name"]
        if n.startswith("mpv-x86_64-") and n.endswith(".7z") and "-v3-" not in n:
            return a["browser_download_url"], rel["tag_name"]
    raise RuntimeError("no mpv-x86_64 asset in latest shinchiro release")


def find_7z():
    for name in ("7z", "7za", "7zz", "7z.exe", "7za.exe"):
        p = shutil.which(name)
        if p:
            return p
    bundled = DEST / "7zip" / "7za.exe"
    if os.name == "nt" and bundled.exists():
        return str(bundled)
    raise RuntimeError("7-Zip is needed to unpack .7z archives (install p7zip / 7-Zip)")


def extract(archive, into):
    into.mkdir(parents=True, exist_ok=True)
    name = archive.name.lower()
    if name.endswith(".zip"):
        with zipfile.ZipFile(archive) as z:
            z.extractall(into)
    elif name.endswith((".tar.gz", ".tgz")):
        with tarfile.open(archive) as t:
            t.extractall(into, filter="data")
    else:
        subprocess.run([find_7z(), "x", "-y", f"-o{into}", str(archive)], check=True,
                       stdout=subprocess.DEVNULL)


def collect(extracted, tool, patterns):
    """Copy matching files (searched anywhere in the archive) into DEST/tool."""
    out = DEST / tool
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    copied = []
    for pattern in patterns:
        if "/" in pattern:
            matches = list(extracted.rglob(pattern.split("/")[-1]))
            matches = [m for m in matches if m.parent.name == pattern.split("/")[0]]
        else:
            matches = list(extracted.rglob(pattern))
        for m in matches:
            if m.is_file() and not (out / m.name).exists():
                shutil.copy2(m, out / m.name)
                copied.append(m.name)
    if not copied:
        raise RuntimeError(f"{tool}: nothing matched {patterns}")
    log(f"{tool}: {', '.join(sorted(copied))}")
    return out


def fetch_prebuilt(tool, work):
    repo, tag, asset = PINNED[tool]
    archive = download(release_asset_url(repo, tag, asset), work / asset)
    extract(archive, work / f"x_{tool}")
    out = collect(work / f"x_{tool}", tool, KEEP[tool])
    (out / "SOURCE.txt").write_text(f"https://github.com/{repo}/releases/tag/{tag}\n{asset}\n")


def fetch_mpv(work):
    url, tag = latest_mpv_asset()
    archive = download(url, work / url.rsplit("/", 1)[1])
    extract(archive, work / "x_mpv")
    out = collect(work / "x_mpv", "mpv", KEEP["mpv"])
    (out / "SOURCE.txt").write_text(f"https://github.com/{MPV_REPO}/releases/tag/{tag}\n{url}\n")


def mingw_toolchain(work):
    tc = work / "mingw.cmake"
    tc.write_text(
        "set(CMAKE_SYSTEM_NAME Windows)\n"
        "set(CMAKE_C_COMPILER x86_64-w64-mingw32-gcc)\n"
        "set(CMAKE_CXX_COMPILER x86_64-w64-mingw32-g++)\n"
        "set(CMAKE_RC_COMPILER x86_64-w64-mingw32-windres)\n"
        "set(CMAKE_FIND_ROOT_PATH_MODE_PROGRAM NEVER)\n"
    )
    return tc


def build_gme(work):
    if not shutil.which("x86_64-w64-mingw32-g++"):
        raise RuntimeError("libgme: needs the mingw-w64 cross compiler (x86_64-w64-mingw32-g++)")
    repo, tag, asset = PINNED["gme"]
    archive = download(release_asset_url(repo, tag, asset), work / asset)
    extract(archive, work / "x_gme")
    src = next((work / "x_gme").glob("libgme-*"))
    build = work / "gme_build"
    subprocess.run([
        "cmake", "-S", str(src), "-B", str(build),
        f"-DCMAKE_TOOLCHAIN_FILE={mingw_toolchain(work)}", "-DCMAKE_BUILD_TYPE=Release",
        "-DGME_BUILD_SHARED=ON", "-DGME_BUILD_STATIC=OFF", "-DGME_ZLIB=OFF",
        "-DGME_BUILD_TESTING=OFF", "-DGME_BUILD_EXAMPLES=OFF", "-DGME_ENABLE_UBSAN=OFF",
        # self-contained DLL: no libgcc/libstdc++/winpthread DLLs needed at runtime
        "-DCMAKE_SHARED_LINKER_FLAGS=-static-libgcc -static-libstdc++ "
        "-Wl,-Bstatic -lwinpthread -Wl,-Bdynamic",
    ], check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["cmake", "--build", str(build), "-j", str(os.cpu_count() or 4)],
                   check=True, stdout=subprocess.DEVNULL)
    out = DEST / "gme"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    shutil.copy2(next(build.rglob("libgme.dll")), out / "libgme.dll")
    for lic in ("license.txt", "LICENSE", "COPYING", "license.gpl.txt"):
        if (src / lic).exists():
            shutil.copy2(src / lic, out / lic)
    (out / "SOURCE.txt").write_text(f"Cross-compiled with mingw-w64 from {release_asset_url(repo, tag, asset)}\n")
    log("gme: libgme.dll")


def build_decoders(work):
    script = ROOT / "tools" / "windows_decoders" / "build.py"
    if not script.exists():
        log("decoders: tools/windows_decoders/build.py not present, skipping")
        return
    subprocess.run([sys.executable, str(script), str(DEST / "decoders")], check=True)


STEPS = {
    "vgmstream": lambda w: fetch_prebuilt("vgmstream", w),
    "sidplayfp": lambda w: fetch_prebuilt("sidplayfp", w),
    "vgmplay": lambda w: fetch_prebuilt("vgmplay", w),
    "neobae": lambda w: fetch_prebuilt("neobae", w),
    "7zip": lambda w: fetch_prebuilt("7zip", w),
    "mpv": fetch_mpv,
    "gme": build_gme,
    "decoders": build_decoders,
}


def main(argv):
    wanted = argv or list(STEPS)
    unknown = [w for w in wanted if w not in STEPS]
    if unknown:
        sys.exit(f"unknown tool(s): {unknown}; choose from {list(STEPS)}")
    DEST.mkdir(parents=True, exist_ok=True)
    failures = []
    with tempfile.TemporaryDirectory(prefix="joshw_windeps_") as tmp:
        for name in wanted:
            work = Path(tmp) / name
            work.mkdir()
            try:
                STEPS[name](work)
            except Exception as e:
                failures.append(name)
                log(f"{name}: FAILED - {e}")
    if failures:
        sys.exit(f"failed: {failures}")
    log(f"done -> {DEST}")


if __name__ == "__main__":
    main(sys.argv[1:])
