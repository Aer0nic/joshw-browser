#!/usr/bin/env bash
#
# build_aosdk.sh - Reproducible Linux build script for AOSDK PSF/PSF2 Shared Library
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AOSDK_DIR="${SCRIPT_DIR}/aosdk"
OUTPUT_DIR="${HOME}/.config/joshw"
OUTPUT_LIB="${OUTPUT_DIR}/libaosdk.so"
LOCAL_LIB="${SCRIPT_DIR}/libaosdk.so"

echo "=== Building AOSDK PSF/PSF2 Shared Library ==="

# Check compiler
CC="${CC:-gcc}"
if ! command -v "${CC}" &>/dev/null; then
    echo "ERROR: C compiler '${CC}' not found." >&2
    exit 1
fi
echo "Using compiler: $("${CC}" --version | head -n1)"

# Target architecture detection
ARCH="$(uname -m)"
echo "Target architecture: ${ARCH}"

EXTRA_FLAGS=""
if [ "${ARCH}" = "x86_64" ] || [ "${ARCH}" = "aarch64" ] || [ "${ARCH}" = "ppc64le" ] || [ "${ARCH}" = "s390x" ]; then
    EXTRA_FLAGS="-DLONG_IS_64BIT=1"
fi

# Dependency discovery: zlib, m, pthread
EXTRA_LIBS="-lm -lpthread"
if pkg-config --exists zlib 2>/dev/null; then
    ZLIB_CFLAGS="$(pkg-config --cflags zlib)"
    ZLIB_LIBS="$(pkg-config --libs zlib)"
    echo "Found zlib via pkg-config: ${ZLIB_LIBS}"
else
    ZLIB_CFLAGS=""
    ZLIB_LIBS="-lz"
    echo "Using default zlib flags: -lz"
fi

# Verify AOSDK source tree exists
if [ ! -d "${AOSDK_DIR}" ]; then
    echo "ERROR: AOSDK directory '${AOSDK_DIR}' not found." >&2
    exit 1
fi

CFLAGS=(
    -O3
    -fPIC
    -shared
    -Wall
    -DPATH_MAX=1024
    -DHAS_PSXCPU=1
    -DLSB_FIRST=1
    -DNOGUI
    -DNOPLAY
    ${EXTRA_FLAGS}
    -I"${AOSDK_DIR}"
    -I"${AOSDK_DIR}/eng_psf"
    ${ZLIB_CFLAGS}
)

SRCS=(
    "${AOSDK_DIR}/aosdk_wrapper.c"
    "${AOSDK_DIR}/eng_psf/psx.c"
    "${AOSDK_DIR}/eng_psf/psx_hw.c"
    "${AOSDK_DIR}/eng_psf/peops/spu.c"
    "${AOSDK_DIR}/eng_psf/eng_psf.c"
    "${AOSDK_DIR}/eng_psf/eng_psf2.c"
    "${AOSDK_DIR}/eng_psf/peops2/spu.c"
    "${AOSDK_DIR}/eng_psf/peops2/dma.c"
    "${AOSDK_DIR}/eng_psf/peops2/registers.c"
    "${AOSDK_DIR}/corlett.c"
    "${AOSDK_DIR}/utils.c"
    "${AOSDK_DIR}/ao.c"
)

# Verify all source files exist
for src in "${SRCS[@]}"; do
    if [ ! -f "${src}" ]; then
        echo "ERROR: Missing source file: ${src}" >&2
        exit 1
    fi
done

echo "Compiling ${LOCAL_LIB}..."
"${CC}" "${CFLAGS[@]}" \
    "${SRCS[@]}" \
    ${ZLIB_LIBS} ${EXTRA_LIBS} \
    -Wl,-soname,libaosdk.so \
    -o "${LOCAL_LIB}"

echo "Built ${LOCAL_LIB} successfully ($(stat -c%s "${LOCAL_LIB}") bytes)."

# Install to ~/.config/joshw/ if writable
mkdir -p "${OUTPUT_DIR}"
if cp "${LOCAL_LIB}" "${OUTPUT_LIB}" 2>/dev/null; then
    echo "Installed to ${OUTPUT_LIB}"
else
    echo "Warning: Could not copy to ${OUTPUT_LIB} (check permissions). Local copy is at ${LOCAL_LIB}"
fi

echo "=== Build Complete ==="

