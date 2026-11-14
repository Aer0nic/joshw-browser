/*
 * aosdk_wrapper.c - the small C API psf_ctypes.py binds to (aosdk_create,
 * aosdk_open, aosdk_render, ...), on top of the Audio Overload SDK 1.4.8
 * PSF/PSF2 engines as maintained in DeaDBeeF (plugins/psf/aosdk).
 *
 * Built into aosdk.dll for Windows by tools/windows_decoders/build.py; the
 * same file also builds a Linux .so with --native (for testing).
 *
 * Contract (matches the Linux libaosdk.so the app already uses):
 *   - 44.1 kHz, stereo, interleaved int16
 *   - get_duration = "length" tag (without fade), get_fade = "fade" tag
 *   - the engine applies the fade itself; is_done turns true once
 *     length + fade has been rendered (never, if the track has no length)
 *   - seek only supports going back to the start (<= 0.05 s)
 */
#include <ctype.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "ao.h"
#include "corlett.h"
#include "eng_protos.h"

#ifdef _WIN32
#define AOSDK_API __declspec(dllexport)
#else
#define AOSDK_API __attribute__((visibility("default")))
#endif

enum {
    AOSDK_OK = 0,
    AOSDK_ERROR_NULL_POINTER = -1,
    AOSDK_ERROR_FILE_NOT_FOUND = -2,
    AOSDK_ERROR_INVALID_FORMAT = -3,
    AOSDK_ERROR_ENGINE_INIT = -4,
    AOSDK_ERROR_SEEK_UNSUPPORTED = -5,
    AOSDK_ERROR_NOT_OPEN = -6,
    AOSDK_ERROR_BUFFER_TOO_SMALL = -7,
    AOSDK_ERROR_INTERNAL = -8,
};
enum { AOSDK_FORMAT_NONE = 0, AOSDK_FORMAT_PSF = 1, AOSDK_FORMAT_PSF2 = 2 };

#define RATE 44100
#define FRAME_SAMPLES 735 /* one 60 Hz frame; the PSF2 engine only works in these */

typedef struct {
    int format;
    void *engine;
    char *path;
    uint8 *filebuf;
    uint32 filesize;
    corlett_t *tags;
    double length_s, fade_s; /* < 0: not tagged */
    unsigned long long frames_rendered;
    int16 pending[FRAME_SAMPLES * 2];
    int pending_count, pending_pos;
    char error[512];
} aosdk_ctx;

/* ---- hooks the engines call ------------------------------------------- */

int ao_get_lib(char *filename, uint8 **buffer, uint64 *length) {
    FILE *f = fopen(filename, "rb");
    if (!f) return AO_FAIL;
    fseek(f, 0, SEEK_END);
    long size = ftell(f);
    fseek(f, 0, SEEK_SET);
    if (size <= 0) { fclose(f); return AO_FAIL; }
    uint8 *buf = malloc((size_t)size);
    if (!buf) { fclose(f); return AO_FAIL; }
    if (fread(buf, 1, (size_t)size, f) != (size_t)size) { free(buf); fclose(f); return AO_FAIL; }
    fclose(f);
    *buffer = buf;
    *length = (uint64)size;
    return AO_SUCCESS;
}

/* "_lib" names are relative to the directory of the file being played.
 * Accept either separator (Windows paths may mix them). */
void ao_getlibpath(const char *path, const char *libname, char *libpath, int size) {
    const char *a = strrchr(path, '\\'), *b = strrchr(path, '/');
    const char *e = a > b ? a : b;
    size_t dir = e ? (size_t)(e + 1 - path) : 0;
    if (dir >= (size_t)size) dir = 0;
    memcpy(libpath, path, dir);
    libpath[dir] = 0;
    strncat(libpath, libname, (size_t)size - dir - 1);
}

int change_pc(int foo) { (void)foo; return 0; } /* MAME stub */

/* ---- helpers ------------------------------------------------------------- */

static void set_error(aosdk_ctx *c, const char *msg) {
    if (!c) return;
    snprintf(c->error, sizeof c->error, "%s", msg);
}

/* PSF time tags: "s", "m:s", "h:m:s", seconds may have a fraction (. or ,) */
static double parse_time(const char *s) {
    if (!s) return -1;
    while (*s && isspace((unsigned char)*s)) s++;
    if (!*s) return -1;
    double total = 0, part = 0, frac_div = 0;
    int any = 0;
    for (; *s; s++) {
        if (isdigit((unsigned char)*s)) {
            any = 1;
            if (frac_div > 0) { part += (*s - '0') / frac_div; frac_div *= 10; }
            else part = part * 10 + (*s - '0');
        } else if (*s == ':') {
            total = (total + part) * 60; part = 0; frac_div = 0;
        } else if ((*s == '.' || *s == ',') && frac_div == 0) {
            frac_div = 10;
        } else if (isspace((unsigned char)*s)) {
            break;
        } else {
            return -1;
        }
    }
    return any ? total + part : -1;
}

static void close_track(aosdk_ctx *c) {
    if (c->engine) {
        if (c->format == AOSDK_FORMAT_PSF) psf_stop(c->engine);
        else if (c->format == AOSDK_FORMAT_PSF2) psf2_stop(c->engine);
    }
    c->engine = NULL;
    free(c->filebuf); c->filebuf = NULL; c->filesize = 0;
    free(c->tags); c->tags = NULL;
    free(c->path); c->path = NULL;
    c->format = AOSDK_FORMAT_NONE;
    c->frames_rendered = 0;
    c->pending_count = c->pending_pos = 0;
    c->length_s = c->fade_s = -1;
}

static int start_engine(aosdk_ctx *c) {
    if (c->format == AOSDK_FORMAT_PSF) c->engine = psf_start(c->path, c->filebuf, c->filesize);
    else c->engine = psf2_start(c->path, c->filebuf, c->filesize);
    c->frames_rendered = 0;
    c->pending_count = c->pending_pos = 0;
    return c->engine != NULL;
}

/* ---- public API ---------------------------------------------------------- */

AOSDK_API void *aosdk_create(void) {
    aosdk_ctx *c = calloc(1, sizeof *c);
    if (c) c->length_s = c->fade_s = -1;
    return c;
}

AOSDK_API void aosdk_destroy(void *h) {
    aosdk_ctx *c = h;
    if (!c) return;
    close_track(c);
    free(c);
}

AOSDK_API int aosdk_open(void *h, const char *path) {
    aosdk_ctx *c = h;
    if (!c || !path) return AOSDK_ERROR_NULL_POINTER;
    close_track(c);
    c->error[0] = 0;

    uint8 *buf; uint64 size;
    if (ao_get_lib((char *)path, &buf, &size) != AO_SUCCESS) {
        set_error(c, "Could not read file");
        return AOSDK_ERROR_FILE_NOT_FOUND;
    }
    if (size < 16) {
        free(buf);
        set_error(c, "File too small to be a valid PSF/PSF2 container");
        return AOSDK_ERROR_INVALID_FORMAT;
    }
    if (buf[0] != 'P' || buf[1] != 'S' || buf[2] != 'F' || (buf[3] != 0x01 && buf[3] != 0x02)) {
        free(buf);
        set_error(c, "Invalid signature: file is not PSF or PSF2");
        return AOSDK_ERROR_INVALID_FORMAT;
    }
    c->format = buf[3] == 0x01 ? AOSDK_FORMAT_PSF : AOSDK_FORMAT_PSF2;
    c->filebuf = buf;
    c->filesize = (uint32)size;
    c->path = strdup(path);

    /* Tags: decode our own copy (the engines keep theirs private) */
    uint8 *prog = NULL; uint64 prog_size = 0;
    if (corlett_decode(c->filebuf, c->filesize, &prog, &prog_size, &c->tags) == AO_SUCCESS) {
        free(prog);
        c->length_s = parse_time(c->tags->inf_length);
        c->fade_s = parse_time(c->tags->inf_fade);
        if (c->length_s <= 0) c->length_s = -1;
    } else {
        c->tags = NULL;
    }

    if (!start_engine(c)) {
        snprintf(c->error, sizeof c->error,
                 "AOSDK %s engine rejected file (missing or unreadable _lib file?)",
                 c->format == AOSDK_FORMAT_PSF ? "PSF" : "PSF2");
        close_track(c);
        return AOSDK_ERROR_ENGINE_INIT;
    }
    return AOSDK_OK;
}

static unsigned long long end_frame(aosdk_ctx *c) {
    if (c->length_s <= 0) return 0; /* untagged: play until stopped */
    double fade = c->fade_s > 0 ? c->fade_s : 0;
    return (unsigned long long)((c->length_s + fade) * RATE + 0.5);
}

AOSDK_API int aosdk_render(void *h, int16 *out, int frames) {
    aosdk_ctx *c = h;
    if (!c || !out) return AOSDK_ERROR_NULL_POINTER;
    if (!c->engine) { set_error(c, "No track open"); return AOSDK_ERROR_NOT_OPEN; }
    if (frames <= 0) return 0;

    unsigned long long end = end_frame(c);
    if (end && c->frames_rendered >= end) return 0;
    if (end && c->frames_rendered + (unsigned long long)frames > end)
        frames = (int)(end - c->frames_rendered);

    int done = 0;
    while (done < frames) {
        if (c->pending_pos >= c->pending_count) {
            if (c->format == AOSDK_FORMAT_PSF) psf_gen(c->engine, c->pending, FRAME_SAMPLES);
            else psf2_gen(c->engine, c->pending, FRAME_SAMPLES);
            c->pending_count = FRAME_SAMPLES;
            c->pending_pos = 0;
        }
        int n = c->pending_count - c->pending_pos;
        if (n > frames - done) n = frames - done;
        memcpy(out + done * 2, c->pending + c->pending_pos * 2, (size_t)n * 4);
        c->pending_pos += n;
        done += n;
    }
    c->frames_rendered += (unsigned long long)done;
    return done;
}

AOSDK_API int aosdk_stop(void *h) {
    aosdk_ctx *c = h;
    if (!c) return AOSDK_ERROR_NULL_POINTER;
    close_track(c);
    return AOSDK_OK;
}

AOSDK_API int aosdk_reset(void *h) {
    aosdk_ctx *c = h;
    if (!c) return AOSDK_ERROR_NULL_POINTER;
    if (!c->engine) { set_error(c, "No track open"); return AOSDK_ERROR_NOT_OPEN; }
    /* Restart from scratch: the engines' own RESTART command leaks/keeps state */
    if (c->format == AOSDK_FORMAT_PSF) psf_stop(c->engine);
    else psf2_stop(c->engine);
    c->engine = NULL;
    if (!start_engine(c)) {
        set_error(c, "Engine failed to restart");
        return AOSDK_ERROR_ENGINE_INIT;
    }
    return AOSDK_OK;
}

AOSDK_API int aosdk_seek(void *h, double seconds) {
    aosdk_ctx *c = h;
    if (!c) return AOSDK_ERROR_NULL_POINTER;
    if (seconds <= 0.05) return aosdk_reset(h);
    set_error(c, "Arbitrary seeking is unsupported by the PSF/PSF2 emulation engine "
                 "(state is non-reversible). Use stop/restart instead.");
    return AOSDK_ERROR_SEEK_UNSUPPORTED;
}

AOSDK_API int aosdk_is_open(void *h) { aosdk_ctx *c = h; return c && c->engine ? 1 : 0; }

AOSDK_API int aosdk_is_done(void *h) {
    aosdk_ctx *c = h;
    if (!c || !c->engine) return 1;
    unsigned long long end = end_frame(c);
    return end && c->frames_rendered >= end;
}

AOSDK_API int aosdk_get_format(void *h) { aosdk_ctx *c = h; return c ? c->format : AOSDK_FORMAT_NONE; }
AOSDK_API int aosdk_get_sample_rate(void *h) { (void)h; return RATE; }
AOSDK_API int aosdk_get_channels(void *h) { (void)h; return 2; }

AOSDK_API int aosdk_get_duration(void *h, double *out) {
    aosdk_ctx *c = h;
    if (!c || !out) return AOSDK_ERROR_NULL_POINTER;
    if (!c->engine) return AOSDK_ERROR_NOT_OPEN;
    if (c->length_s <= 0) { *out = 0; return AOSDK_ERROR_INVALID_FORMAT; }
    *out = c->length_s;
    return AOSDK_OK;
}

AOSDK_API int aosdk_get_fade(void *h, double *out) {
    aosdk_ctx *c = h;
    if (!c || !out) return AOSDK_ERROR_NULL_POINTER;
    if (!c->engine) return AOSDK_ERROR_NOT_OPEN;
    *out = c->fade_s > 0 ? c->fade_s : 0;
    return AOSDK_OK;
}

AOSDK_API double aosdk_get_position(void *h) {
    aosdk_ctx *c = h;
    return c ? (double)c->frames_rendered / RATE : 0.0;
}

AOSDK_API const char *aosdk_get_last_error(void *h) {
    aosdk_ctx *c = h;
    return c ? c->error : "null context";
}

AOSDK_API int aosdk_get_tag(void *h, const char *name, char *out, size_t size) {
    aosdk_ctx *c = h;
    if (!c || !name || !out || size == 0) return AOSDK_ERROR_NULL_POINTER;
    out[0] = 0;
    if (!c->tags) return AOSDK_ERROR_NOT_OPEN;
    corlett_t *t = c->tags;
    const char *v = NULL;
    if (!strcasecmp(name, "title")) v = t->inf_title;
    else if (!strcasecmp(name, "artist")) v = t->inf_artist;
    else if (!strcasecmp(name, "game")) v = t->inf_game;
    else if (!strcasecmp(name, "year")) v = t->inf_year;
    else if (!strcasecmp(name, "copyright")) v = t->inf_copy;
    else if (!strcasecmp(name, "length")) v = t->inf_length;
    else if (!strcasecmp(name, "fade")) v = t->inf_fade;
    else if (!strcasecmp(name, "_refresh")) v = t->inf_refresh;
    else if (!strcasecmp(name, "_lib")) v = t->lib;
    else if (!strncasecmp(name, "_lib", 4) && name[4] >= '2' && name[4] <= '9' && !name[5])
        v = t->libaux[name[4] - '2'];
    else {
        for (int i = 0; i < MAX_UNKNOWN_TAGS; i++) {
            if (t->tag_name[i][0] && !strcasecmp(t->tag_name[i], name)) { v = t->tag_data[i]; break; }
        }
    }
    if (!v || !v[0] || !strcmp(v, "n/a")) return AOSDK_ERROR_INVALID_FORMAT; /* corlett's "missing" */
    snprintf(out, size, "%s", v);
    return AOSDK_OK;
}
