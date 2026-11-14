/*
 * gsfdec - decode a .gsf/.minigsf to a 16-bit WAV stream on stdout.
 *
 * Windows counterpart of playgsf (github.com/yshui/playgsf) as built by the
 * Linux installer, including patches/playgsf-start-offset.patch (-b<ms>).
 * It is playgsf's linuxmain.cpp with the SDL oscilloscope and libao output
 * removed: writeSound() writes PCM to stdout instead, and the track info
 * ("Title: ...", "Length: ...", "Fade: ...") goes to stderr in the same
 * format the app's GSF player parses. Length/fade/silence handling is the
 * unchanged VBA core.
 *
 * Usage: gsfdec [-c] [-e] [-i] [-s] [-L sec] [-t ms] [-b ms] file.minigsf > out.wav
 */
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <cstdint>
#include <csignal>
#include <string>
#include <unistd.h>
#include <fcntl.h>
#ifdef _WIN32
#include <direct.h>
#include <io.h>
#define chdir _chdir
#define dup _dup
#define dup2 _dup2
#define fileno _fileno
#endif

extern "C" {
#include "types.h"
#include "VBA/psftag.h"
#include "gsf.h"
}

extern "C" {
int defvolume = 1000;
int relvolume = 1000;
int TrackLength = 0;
int FadeLength = 0;
int IgnoreTrackLength, DefaultLength = 150000;
int playforever = 0;
int fileoutput = 0;
int TrailingSilence = 1000;
int DetectSilence = 0, silencedetected = 0, silencelength = 5;
int CliOnly = 1;
int noinfo = 0;
}
int cpupercent = 0, sndSamplesPerSec, sndNumChannels;
int sndBitsPerSample = 16;
int deflen = 120, deffade = 4;

extern unsigned short soundFinalWave[1470];
extern int soundBufferLen;

double decode_pos_ms;  // current decoding position, in milliseconds
int seek_needed;       // if != -1, skip output until decode_pos_ms reaches it
static int start_time_ms = 0;
static volatile int g_playing = 0;
static volatile int g_must_exit = 0;
static int g_header_written = 0;
static FILE *g_wav = NULL;  // the real stdout; fd 1 itself is pointed at stderr

extern "C" int LengthFromString(const char *timestring);
extern "C" int VolumeFromString(const char *volumestring);

extern "C" void end_of_track() { g_playing = 0; }

static void put_u32(unsigned char *p, uint32_t v) { p[0] = v; p[1] = v >> 8; p[2] = v >> 16; p[3] = v >> 24; }
static void put_u16(unsigned char *p, uint16_t v) { p[0] = v; p[1] = v >> 8; }

static void write_wav_header() {
    // Length depends on silence detection / trailing silence, so it's left
    // "unknown" (0xFFFFFFFF); the player takes the duration from the tags.
    unsigned char h[44];
    memcpy(h, "RIFF", 4); put_u32(h + 4, 0xFFFFFFFFu); memcpy(h + 8, "WAVE", 4);
    memcpy(h + 12, "fmt ", 4); put_u32(h + 16, 16); put_u16(h + 20, 1);
    put_u16(h + 22, (uint16_t)sndNumChannels); put_u32(h + 24, (uint32_t)sndSamplesPerSec);
    put_u32(h + 28, (uint32_t)(sndSamplesPerSec * sndNumChannels * 2));
    put_u16(h + 32, (uint16_t)(sndNumChannels * 2)); put_u16(h + 34, 16);
    memcpy(h + 36, "data", 4); put_u32(h + 40, 0xFFFFFFFFu);
    fwrite(h, 1, sizeof h, g_wav);
    g_header_written = 1;
}

extern "C" void writeSound(void) {
    int ret = soundBufferLen;
    if (!g_header_written) write_wav_header();
    if (seek_needed < 0 || decode_pos_ms >= seek_needed) {
        if (fwrite(soundFinalWave, 1, ret, g_wav) != (size_t)ret) {
            g_must_exit = 1;  // reader went away
            g_playing = 0;
        }
    }
    decode_pos_ms += (ret / (2 * sndNumChannels) * 1000) / (float)sndSamplesPerSec;
    if (seek_needed >= 0 && decode_pos_ms >= seek_needed) seek_needed = -1;
}

extern "C" void signal_handler(int) { g_playing = 0; g_must_exit = 1; }

static void info(const char *key, const char *value) { fprintf(stderr, "%s: %s\n", key, value); }

int main(int argc, char **argv) {
    // The VBA core printf()s diagnostics ("Failed to load", ...) to stdout,
    // which is the WAV stream here. Keep a private handle on the real stdout
    // for audio and send everything else written to fd 1 to stderr.
    fflush(stdout);
    int wav_fd = dup(fileno(stdout));
    dup2(fileno(stderr), fileno(stdout));
#ifdef _WIN32
    _setmode(wav_fd, _O_BINARY);
#endif
    g_wav = fdopen(wav_fd, "wb");
    if (!g_wav) { fprintf(stderr, "Could not open output\n"); return 1; }
    char length_str[256], fade_str[256], tmp_str[256];
    int r;
    while ((r = getopt(argc, argv, "b:chlsrieqW:L:t:")) >= 0) {
        char *e;
        switch (r) {
        case 'b': start_time_ms = (int)strtol(optarg, &e, 10); if (start_time_ms < 0) start_time_ms = 0; break;
        case 'i': IgnoreTrackLength = 1; break;
        case 's': DetectSilence = 1; break;
        case 'L': silencelength = (int)strtol(optarg, &e, 10); break;
        case 'e': playforever = 1; break;
        case 't': DefaultLength = (int)strtol(optarg, &e, 10); break;
        case 'q': noinfo = 1; break;
        case 'h':
            fprintf(stderr, "Usage: gsfdec [-b<ms>] [-e] [-i] [-s] [-L sec] [-t ms] file > out.wav\n");
            return 0;
        default: break;  // -c -l -r -W: accepted for playgsf compatibility, ignored
        }
    }
    if (optind >= argc) { fprintf(stderr, "No file specified\n"); return 1; }
    signal(SIGINT, signal_handler);

    // playgsf (built with -DLINUX) finds "_lib" files by splitting the path on
    // '/' only, and copies paths into 260-byte buffers. Load from inside the
    // track's folder with the bare file name: the libs are then found as
    // "./<lib>" no matter which separators or how long the real path is.
    const char *file = argv[optind];
    {
        const char *s1 = strrchr(file, '/'), *s2 = strrchr(file, '\\');
        const char *sep = s1 > s2 ? s1 : s2;
        if (sep) {
            std::string dir(file, (size_t)(sep - file));
            if (dir.empty() || (dir.size() == 2 && dir[1] == ':')) dir += '/';  // "/" or "C:"
            if (chdir(dir.c_str()) != 0) {
                fprintf(stderr, "Could not enter folder %s\n", dir.c_str());
                return 1;
            }
            file = sep + 1;
        }
    }
    decode_pos_ms = 0;
    seek_needed = start_time_ms > 0 ? start_time_ms : -1;
    if (!GSFRun((char *)file)) { fprintf(stderr, "Could not load %s\n", file); return 1; }
    g_playing = 1;

    char *tag = (char *)malloc(50001);
    psftag_readfromfile((void *)tag, file);
    const char *base = strrchr(file, '/');
    const char *base2 = strrchr(file, '\\');
    if (base2 > base) base = base2;
    info("Filename", base ? base + 1 : file);
    snprintf(tmp_str, sizeof tmp_str, "%d", sndNumChannels); info("Channels", tmp_str);
    snprintf(tmp_str, sizeof tmp_str, "%d", sndSamplesPerSec); info("Sample rate", tmp_str);
    static const char *keys[][2] = {{"title", "Title"}, {"artist", "Artist"}, {"game", "Game"},
                                     {"year", "Year"}, {"copyright", "Copyright"}, {"gsfby", "GSF By"},
                                     {"tagger", "Tagger"}, {"comment", "Comment"}};
    for (auto &k : keys)
        if (!psftag_getvar(tag, k[0], tmp_str, sizeof tmp_str - 1)) info(k[1], tmp_str);

    if (!psftag_getvar(tag, "fade", fade_str, sizeof fade_str - 1)) {
        FadeLength = LengthFromString(fade_str);
        snprintf(tmp_str, sizeof tmp_str, "%s (%d ms)", fade_str, FadeLength); info("Fade", tmp_str);
    }
    if (!psftag_raw_getvar(tag, "length", length_str, sizeof length_str - 1)) {
        TrackLength = LengthFromString(length_str) + FadeLength;
        snprintf(tmp_str, sizeof tmp_str, "%s (%d ms)%s", length_str, TrackLength,
                 IgnoreTrackLength ? " (ignored)" : "");
        info("Length", tmp_str);
        if (IgnoreTrackLength) TrackLength = DefaultLength;
    } else {
        TrackLength = DefaultLength;
    }
    fflush(stderr);

    while (g_playing && !g_must_exit) EmulationLoop();
    fflush(g_wav);
    free(tag);
    return 0;
}
