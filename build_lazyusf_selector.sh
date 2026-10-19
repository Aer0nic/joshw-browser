#!/bin/bash
set -e

TARGET_DIR="$HOME/github/joshw-browser"
mkdir -p "$TARGET_DIR"

echo "=================================================="
echo "         LazyUSF Builder Selector                 "
echo "=================================================="
echo "1) Build Original Unpatched LazyUSF (Outputs -> ~/github/joshw-browser/lazyusf)"
echo "2) Build Modified LazyUSF (File Output + Audio Buffering Patches) (Outputs -> ~/github/joshw-browser/lazyusfedit)"
echo "=================================================="
read -p "Select an option [1 or 2]: " choice

TEMP_DIR=$(mktemp -d -t lazyusf_build_XXXXXX)
cd "$TEMP_DIR"

echo "Cloning original repository..."
git clone https://github.com/derselbst/lazyusf.git
cd lazyusf

if [ "$choice" = "2" ]; then
    echo "Applying audio buffer patches while keeping standard file output..."

    # Patch audio.c to keep file output (no stdout redirect), but include write_all and buffering logic
    cat << 'EOF' > patch_audio_file_buffering.py
import pathlib
audio_c = pathlib.Path("audio.c")
if audio_c.exists():
    content = audio_c.read_text(encoding="utf-8")
    
    # Inject write_all and buffer queue globals into audio.c
    if "static unsigned char pcm_out_buf" not in content:
        buffer_globals = """
#include <errno.h>

static unsigned char pcm_out_buf[8192];
static size_t pcm_out_pos = 0;
static long long cumulative_frames = 0;

static int write_all(int fd, const void *data, size_t bytes)
{
    const unsigned char *p = data;
    while (bytes > 0) {
        ssize_t n = write(fd, p, bytes);
        if (n < 0) {
            if (errno == EINTR)
                continue;
            fprintf(stderr, "LAZYUSF_ERROR: write failed, errno=%d\\n", errno);
            return -1;
        }
        if (n == 0) {
            fprintf(stderr, "LAZYUSF_ERROR: write returned 0\\n");
            return -1;
        }
        p += (size_t)n;
        bytes -= (size_t)n;
    }
    return 0;
}
"""
        include_pos = content.find("#include \"registers.h\"")
        if include_pos != -1:
            content = content[:include_pos] + buffer_globals + "\n" + content[include_pos:]

    # Replace the direct write block inside AddBuffer to use write_all buffering
    old_write_block = """        // write raw pcm to au file
        unsigned int status = write(fd, buf, length);
        if (status != length)
        {
            perror("wrote wrong number of bytes\\n");
        }"""

    new_write_block = """        size_t frame_count = length / 4;
        cumulative_frames += frame_count;

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
                if (write_all(fd, pcm_out_buf, 4096) < 0) {
                    fprintf(stderr, "LAZYUSF_ERROR: Buffer flush write_all failed at cumulative frames %lld\\n", cumulative_frames);
                }
                size_t leftover = pcm_out_pos - 4096;
                if (leftover > 0) {
                    memmove(pcm_out_buf, pcm_out_buf + 4096, leftover);
                }
                pcm_out_pos = leftover;
            }
        }"""

    if old_write_block in content:
        content = content.replace(old_write_block, new_write_block)

    # Safely replace CloseSound to flush remaining buffer contents via write_all before closing
    old_close_sound = """void CloseSound(void)
{
    if(!useFlac && fd != STDOUT_FILENO)
    {
        close(fd);
    }"""

    new_close_sound = """void CloseSound(void)
{
    if (!useFlac && pcm_out_pos > 0) {
        if (write_all(fd, pcm_out_buf, pcm_out_pos) < 0) {
            fprintf(stderr, "LAZYUSF_ERROR: Final CloseSound flush write_all failed\\n");
        }
        pcm_out_pos = 0;
    }

    if(!useFlac && fd != STDOUT_FILENO)
    {
        close(fd);
    }"""

    if old_close_sound in content:
        content = content.replace(old_close_sound, new_close_sound)

    audio_c.write_text(content, encoding="utf-8")
    print("Successfully patched audio.c with file-output buffering & write_all.")
EOF
    python3 patch_audio_file_buffering.py
fi

echo "Configuring and building with CMake..."
mkdir build
cd build
cmake ..
make

echo "Copying binary to destination..."
if [ "$choice" = "2" ]; then
    find . -name "lazyusf" -type f -exec cp {} "$TARGET_DIR/lazyusfedit" \;
    chmod +x "$TARGET_DIR/lazyusfedit"
    echo "=================================================="
    echo "Success! Modified lazyusfedit built and placed at:"
    echo "$TARGET_DIR/lazyusfedit"
    echo "(This version creates normal .au files on disk"
    echo " using the buffer-smoothing/write_all patch logic)"
    echo "=================================================="
else
    find . -name "lazyusf" -type f -exec cp {} "$TARGET_DIR/lazyusf" \;
    chmod +x "$TARGET_DIR/lazyusf"
    echo "=================================================="
    echo "Success! Original unpatched lazyusf placed at:"
    echo "$TARGET_DIR/lazyusf"
    echo "=================================================="
fi

cd /
rm -rf "$TEMP_DIR"
