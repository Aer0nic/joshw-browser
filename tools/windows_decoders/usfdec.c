/*
 * usfdec - decode a .usf/.miniusf to a 16-bit stereo 44.1 kHz WAV stream on stdout.
 *
 * Windows counterpart of lazyusf. lazyusf's x86 recompiler depends on Linux
 * signal handling and memory mapping, so this uses lazyusf2
 * (github.com/derselbst/lazyusf2, Mupen64plus-based, cached interpreter) with
 * psflib - the same pairing foobar2000/DeaDBeeF use. Based on lazyusf2's
 * test/dumpresampled.c.
 *
 * Usage: usfdec [--playback] [--forever] [-f N] [-b<ms>] file.miniusf > out.wav
 *   --playback / -f N   accepted for lazyusf compatibility (ignored)
 *   --forever           ignore the length tag and play until stopped
 *   -b<ms>              start at <ms> (rendered and discarded)
 * The app turns elapsed time into lazyusf's "minutes" progress lines.
 */
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif

#include "usf/usf.h"
#include "psflib.h"

static unsigned char *state = 0;
static unsigned int enable_compare = 0, enable_fifo_full = 0;
static long length_ms = -1, fade_ms = 0;

static void *stdio_fopen(const char *path) { return fopen(path, "rb"); }
static size_t stdio_fread(void *p, size_t size, size_t count, void *f) { return fread(p, size, count, (FILE *)f); }
static int stdio_fseek(void *f, int64_t offset, int whence) { return fseek((FILE *)f, (long)offset, whence); }
static int stdio_fclose(void *f) { return fclose((FILE *)f); }
static long stdio_ftell(void *f) { return ftell((FILE *)f); }

static psf_file_callbacks stdio_callbacks = {"\\/:", stdio_fopen, stdio_fread, stdio_fseek, stdio_fclose, stdio_ftell};

static int usf_loader(void *context, const uint8_t *exe, size_t exe_size,
                      const uint8_t *reserved, size_t reserved_size) {
    (void)context;
    if (exe && exe_size > 0) return -1;
    return usf_upload_section(state, reserved, reserved_size);
}

#define BORK_TIME 0xC0CAC01A
static unsigned long parse_time(const char *input) {
    unsigned long value = 0, multiplier = 1000, colon_count = 0;
    const char *ptr = input;
    while (*ptr && ((*ptr >= '0' && *ptr <= '9') || *ptr == ':')) { colon_count += *ptr == ':'; ++ptr; }
    if (colon_count > 2) return BORK_TIME;
    if (*ptr && *ptr != '.' && *ptr != ',') return BORK_TIME;
    if (*ptr) ++ptr;
    while (*ptr && *ptr >= '0' && *ptr <= '9') ++ptr;
    if (*ptr) return BORK_TIME;
    ptr = strrchr(input, ':');
    if (!ptr) ptr = input;
    for (;;) {
        char *end;
        if (ptr != input) ++ptr;
        if (multiplier == 1000) {
            double temp = strtod(ptr, &end);
            if (temp >= 60.0) return BORK_TIME;
            value = (unsigned long)(temp * 1000.0);
        } else {
            unsigned long temp = strtoul(ptr, &end, 10);
            if (temp >= 60 && multiplier < 3600000) return BORK_TIME;
            value += temp * multiplier;
        }
        if (ptr == input) break;
        ptr -= 2;
        while (ptr > input && *ptr != ':') --ptr;
        multiplier *= 60;
    }
    return value;
}

static int usf_info(void *context, const char *name, const char *value) {
    (void)context;
    if (!strcasecmp(name, "length")) {
        unsigned long t = parse_time(value);
        if (t != BORK_TIME) length_ms = (long)t;
    } else if (!strcasecmp(name, "fade")) {
        unsigned long t = parse_time(value);
        if (t != BORK_TIME) fade_ms = (long)t;
    } else if (!strcasecmp(name, "_enablecompare") && *value) {
        enable_compare = 1;
    } else if (!strcasecmp(name, "_enablefifofull") && *value) {
        enable_fifo_full = 1;
    }
    return 0;
}

static void print_message(void *unused, const char *message) { (void)unused; fputs(message, stderr); }

static void put_u32(unsigned char *p, uint32_t v) { p[0] = v; p[1] = v >> 8; p[2] = v >> 16; p[3] = v >> 24; }
static void put_u16(unsigned char *p, uint16_t v) { p[0] = v; p[1] = v >> 8; }

static void write_wav_header(uint32_t frames /* 0xFFFFFFFF = unknown */) {
    unsigned char h[44];
    uint32_t data_bytes = frames == 0xFFFFFFFFu ? 0xFFFFFFFFu : frames * 4;
    memcpy(h, "RIFF", 4); put_u32(h + 4, data_bytes == 0xFFFFFFFFu ? data_bytes : 36 + data_bytes);
    memcpy(h + 8, "WAVE", 4); memcpy(h + 12, "fmt ", 4); put_u32(h + 16, 16); put_u16(h + 20, 1);
    put_u16(h + 22, 2); put_u32(h + 24, 44100); put_u32(h + 28, 44100 * 4); put_u16(h + 32, 4);
    put_u16(h + 34, 16); memcpy(h + 36, "data", 4); put_u32(h + 40, data_bytes);
    fwrite(h, 1, sizeof h, stdout);
    fflush(stdout);
}

int main(int argc, char **argv) {
#ifdef _WIN32
    _setmode(_fileno(stdout), _O_BINARY);
#endif
    const char *file = NULL;
    int forever = 0;
    long start_ms = 0;
    for (int i = 1; i < argc; i++) {
        if (!strcmp(argv[i], "--forever")) forever = 1;
        else if (!strcmp(argv[i], "--playback")) { /* lazyusf compat */ }
        else if (!strcmp(argv[i], "-f") && i + 1 < argc) i++; /* fade type: compat */
        else if (!strncmp(argv[i], "-b", 2)) start_ms = strtol(argv[i] + 2, NULL, 10);
        else if (!strcmp(argv[i], "-h") || !strcmp(argv[i], "--help")) {
            fprintf(stderr, "Usage: usfdec [--forever] [-b<ms>] file.miniusf > out.wav\n");
            return 0;
        } else file = argv[i];
    }
    if (!file) { fprintf(stderr, "No file specified\n"); return 1; }

    state = (unsigned char *)malloc(usf_get_state_size());
    usf_clear(state);
    if (psf_load(file, &stdio_callbacks, 0x21, usf_loader, 0, usf_info, 0, 1, print_message, 0) <= 0) {
        fprintf(stderr, "Could not load %s\n", file);
        return 1;
    }
    usf_set_compare(state, enable_compare);
    usf_set_fifo_full(state, enable_fifo_full);
    usf_set_hle_audio(state, 1);

    if (length_ms < 0) length_ms = 180000;  /* untagged: lazyusf's 3 minute default */
    if (forever) fade_ms = 0;
    long body = (long)((int64_t)length_ms * 441 / 10);
    long fade_total = (long)((int64_t)fade_ms * 441 / 10);
    long skip = (long)((int64_t)start_ms * 441 / 10);
    if (!forever && skip > body + fade_total) skip = body + fade_total;
    write_wav_header(forever ? 0xFFFFFFFFu : (uint32_t)(body + fade_total - skip));

    int16_t buf[2048];
    long pos = 0;  /* frames rendered so far (incl. skipped) */
    for (;;) {
        long todo = 1024;
        if (!forever) {
            long left = body + fade_total - pos;
            if (left <= 0) break;
            if (todo > left) todo = left;
        }
        if (skip > pos && todo > skip - pos) todo = skip - pos;  /* land exactly on the seek point */
        const char *err = usf_render_resampled(state, buf, (size_t)todo, 44100);
        if (err) { fprintf(stderr, "Render error: %s\n", err); break; }
        if (!forever && pos + todo > body && fade_total > 0) {
            for (long i = 0; i < todo; i++) {
                long into_fade = pos + i - body;
                if (into_fade < 0) continue;
                long remaining = fade_total - into_fade;
                buf[i * 2] = (int16_t)((int64_t)buf[i * 2] * remaining / fade_total);
                buf[i * 2 + 1] = (int16_t)((int64_t)buf[i * 2 + 1] * remaining / fade_total);
            }
        }
        if (pos >= skip) {
            if (fwrite(buf, 4, (size_t)todo, stdout) != (size_t)todo) break;  /* reader gone */
        }
        pos += todo;
    }
    fflush(stdout);
    usf_shutdown(state);
    free(state);
    return 0;
}
