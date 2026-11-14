/*
 * 2sfdec - decode a .2sf/.mini2sf to a 16-bit stereo 44.1 kHz WAV stream on stdout.
 *
 * Windows counterpart of the 2sfPlay binary the Linux installer generates
 * (same emulation core: vio2sf + psflib from github.com/yshui/2sftowav, same
 * length/fade handling and -b<ms> start offset). Instead of playing through
 * SDL it writes WAV to stdout; the app plays it with sounddevice
 * (platform_support.PcmPipeProcess), which also handles volume and pause.
 *
 * Usage: 2sfdec [-b<start_ms>] file.mini2sf > out.wav
 * The WAV header's data size is exact (remaining length incl. fade), so the
 * reader knows the total duration up front.
 */
#include <locale.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif
#include "common.h"
#include "desmume/state.h"

static int16_t samples[4096];

static void put_u32(unsigned char *p, uint32_t v) { p[0] = v; p[1] = v >> 8; p[2] = v >> 16; p[3] = v >> 24; }
static void put_u16(unsigned char *p, uint16_t v) { p[0] = v; p[1] = v >> 8; }

static void write_wav_header(FILE *out, uint32_t frames) {
    unsigned char h[44];
    uint32_t data_bytes = frames * 4;
    memcpy(h, "RIFF", 4); put_u32(h + 4, 36 + data_bytes); memcpy(h + 8, "WAVE", 4);
    memcpy(h + 12, "fmt ", 4); put_u32(h + 16, 16); put_u16(h + 20, 1); put_u16(h + 22, 2);
    put_u32(h + 24, 44100); put_u32(h + 28, 44100 * 4); put_u16(h + 32, 4); put_u16(h + 34, 16);
    memcpy(h + 36, "data", 4); put_u32(h + 40, data_bytes);
    fwrite(h, 1, sizeof h, out);
}

int main(int argc, char **argv) {
    setlocale(LC_ALL, "C");
#ifdef _WIN32
    _setmode(_fileno(stdout), _O_BINARY);
#endif
    long start_time_ms = 0;
    const char *input_file = NULL;
    for (int i = 1; i < argc; i++) {
        if (strcmp(argv[i], "--help") == 0) {
            fprintf(stderr, "Usage: %s [-b<ms>] file.2sf > out.wav\n", argv[0]);
            return 0;
        }
        if (strncmp(argv[i], "-b", 2) == 0) {
            start_time_ms = strtol(argv[i] + 2, NULL, 10);
        } else if (input_file == NULL) {
            input_file = argv[i];
        }
    }
    if (input_file == NULL) return 1;

    struct twosf_loader_state state = {0};
    if (psf_load(input_file, &stdio_callbacks, 0x24, twosf_loader, &state,
                 twosf_info, &state, 1, print_message, 0) <= 0) {
        fprintf(stderr, "Could not load %s\n", input_file);
        return 1;
    }

    NDS_state core = {0};
    state_init(&core);
    core.dwInterpolation = 0;
    core.dwChannelMute = 0;
    state_setrom(&core, state.rom, state.rom_size, 0);
    state_loadstate(&core, state.state, state.state_size);

    long nsamples = state.length_ms * 441 / 10 + 1;
    long fade_samples = state.fade_ms * 441 / 10;
    long total = nsamples;
    long to_skip_total = start_time_ms * 441 / 10;
    if (to_skip_total > nsamples) to_skip_total = nsamples;

    /* Header first so the reader knows the duration before the (slow) seek */
    write_wav_header(stdout, (uint32_t)(nsamples - to_skip_total));
    fflush(stdout);

    long samples_to_skip = to_skip_total;
    while (samples_to_skip > 0) {
        long n = samples_to_skip < 2048 ? samples_to_skip : 2048;
        state_render(&core, samples, n);
        samples_to_skip -= n;
        nsamples -= n;
    }

    while (nsamples > 0) {
        long n = nsamples < 2048 ? nsamples : 2048;
        state_render(&core, samples, n);
        if (fade_samples > 0 && nsamples <= fade_samples) {
            for (int i = 0; i < n * 2; i++) {
                double factor = (nsamples - i / 2) / (double)fade_samples;
                samples[i] = (int16_t)(samples[i] * factor);
            }
        }
        if (fwrite(samples, sizeof(int16_t) * 2, n, stdout) != (size_t)n) break; /* reader gone */
        nsamples -= n;
    }
    fflush(stdout);
    (void)total;
    state_deinit(&core);
    return 0;
}
