#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <pthread.h>
#include "aosdk_wrapper.h"

#include "ao.h"
#include "corlett.h"

// Core PSF / PSF2 engine function declarations from AOSDK
extern int32 psf_start(const char *filename);
extern int32 psf_gen(int16 *buffer, uint32 samples);
extern int32 psf_stop(void);

extern int32 psf2_start(const char *filename);
extern int32 psf2_gen(int16 *buffer, uint32 samples);
extern int32 psf2_stop(void);

extern double psfTimeToSeconds(const char *str);

struct AOSDKContext {
    int is_open;
    int format;               // 1 = PSF1, 2 = PSF2
    int is_done;
    double sample_rate;
    int channels;
    double duration_sec;
    double fade_sec;
    int64_t total_samples_rendered;
    char last_error[256];
    corlett_t* corlett;
};

static pthread_mutex_t g_sdk_mutex = PTHREAD_MUTEX_INITIALIZER;

static void set_error(AOSDKContext* ctx, const char* msg) {
    if (ctx) {
        snprintf(ctx->last_error, sizeof(ctx->last_error), "%s", msg);
    }
}

// Callback so minipsf / minipsf2 files can load their parent _lib files
static int corlett_lib_cb(void *param, const char *filename, uint8 **buffer, uint32 *length) {
    FILE *f = ao_fopen(filename, "rb");
    if (!f) return AO_FAIL;

    fseek(f, 0, SEEK_END);
    long sz = ftell(f);
    fseek(f, 0, SEEK_SET);

    if (sz <= 0) {
        fclose(f);
        return AO_FAIL;
    }

    uint8 *buf = (uint8*)malloc(sz);
    if (!buf) {
        fclose(f);
        return AO_FAIL;
    }

    if (fread(buf, 1, sz, f) != (size_t)sz) {
        free(buf);
        fclose(f);
        return AO_FAIL;
    }

    fclose(f);
    *buffer = buf;
    *length = (uint32)sz;
    return AO_SUCCESS;
}

AOSDKContext* aosdk_create(void) {
    AOSDKContext* ctx = (AOSDKContext*)calloc(1, sizeof(AOSDKContext));
    if (!ctx) return NULL;
    ctx->sample_rate = 44100.0;
    ctx->channels = 2;
    return ctx;
}

void aosdk_destroy(AOSDKContext* ctx) {
    if (!ctx) return;
    aosdk_stop(ctx);
    free(ctx);
}

int aosdk_open(AOSDKContext* ctx, const char* filepath) {
    if (!ctx || !filepath || strlen(filepath) == 0) {
        return AOSDK_ERROR_NULL_POINTER;
    }

    pthread_mutex_lock(&g_sdk_mutex);

    if (ctx->is_open) {
        pthread_mutex_unlock(&g_sdk_mutex);
        aosdk_stop(ctx);
        pthread_mutex_lock(&g_sdk_mutex);
    }

    FILE* f = ao_fopen(filepath, "rb");
    if (!f) {
        set_error(ctx, "File not found");
        pthread_mutex_unlock(&g_sdk_mutex);
        return AOSDK_ERROR_FILE_NOT_FOUND;
    }

    fseek(f, 0, SEEK_END);
    long fsize = ftell(f);
    fseek(f, 0, SEEK_SET);

    if (fsize <= 0) {
        fclose(f);
        set_error(ctx, "File is empty or invalid");
        pthread_mutex_unlock(&g_sdk_mutex);
        return AOSDK_ERROR_INVALID_FORMAT;
    }

    uint8* file_buf = (uint8*)malloc(fsize);
    if (!file_buf) {
        fclose(f);
        set_error(ctx, "Memory allocation error");
        pthread_mutex_unlock(&g_sdk_mutex);
        return AOSDK_ERROR_INTERNAL;
    }

    if (fread(file_buf, 1, fsize, f) != (size_t)fsize) {
        free(file_buf);
        fclose(f);
        set_error(ctx, "File read error");
        pthread_mutex_unlock(&g_sdk_mutex);
        return AOSDK_ERROR_INTERNAL;
    }
    fclose(f);

    ctx->corlett = (corlett_t*)calloc(1, sizeof(corlett_t));
    if (!ctx->corlett) {
        free(file_buf);
        set_error(ctx, "Memory allocation error");
        pthread_mutex_unlock(&g_sdk_mutex);
        return AOSDK_ERROR_INTERNAL;
    }

    if (corlett_decode(file_buf, (uint32)fsize, ctx->corlett, (corlett_lib_callback_t*)corlett_lib_cb) != AO_SUCCESS) {
        free(file_buf);
        free(ctx->corlett);
        ctx->corlett = NULL;
        set_error(ctx, "Could not decode Corlett PSF container");
        pthread_mutex_unlock(&g_sdk_mutex);
        return AOSDK_ERROR_INVALID_FORMAT;
    }
    free(file_buf);

    int format = AOSDK_FORMAT_PSF;
    const char* lib_tag = corlett_tag_lookup(ctx->corlett, "_lib");
    if ((lib_tag && strstr(lib_tag, ".psf2")) || strstr(filepath, ".psf2") || strstr(filepath, ".minipsf2")) {
        format = AOSDK_FORMAT_PSF2;
    }

    // Pass the filepath string directly to psf_start / psf2_start
    int32 start_res = AO_FAIL;
    if (format == AOSDK_FORMAT_PSF2) {
        start_res = psf2_start(filepath);
    } else {
        start_res = psf_start(filepath);
    }

    if (start_res != AO_SUCCESS) {
        if (ctx->corlett) {
            corlett_free(ctx->corlett);
            ctx->corlett = NULL;
        }
        set_error(ctx, "AOSDK engine initialization failed");
        pthread_mutex_unlock(&g_sdk_mutex);
        return AOSDK_ERROR_ENGINE_INIT;
    }

    ctx->format = format;
    ctx->is_open = 1;
    ctx->is_done = 0;
    ctx->total_samples_rendered = 0;

    const char* len_str = corlett_tag_lookup(ctx->corlett, "length");
    const char* fade_str = corlett_tag_lookup(ctx->corlett, "fade");

    ctx->duration_sec = len_str ? psfTimeToSeconds(len_str) : 0.0;
    ctx->fade_sec = fade_str ? psfTimeToSeconds(fade_str) : 0.0;

    pthread_mutex_unlock(&g_sdk_mutex);
    return AOSDK_OK;
}

int aosdk_render(AOSDKContext* ctx, int16_t* buffer, int num_samples) {
    if (!ctx || !buffer) return AOSDK_ERROR_NULL_POINTER;
    if (!ctx->is_open) return AOSDK_ERROR_NOT_OPEN;
    if (ctx->is_done) return 0;

    pthread_mutex_lock(&g_sdk_mutex);

    int32 samples_rendered = 0;
    if (ctx->format == AOSDK_FORMAT_PSF2) {
        samples_rendered = psf2_gen((int16*)buffer, (uint32)num_samples);
    } else {
        samples_rendered = psf_gen((int16*)buffer, (uint32)num_samples);
    }

    if (samples_rendered <= 0) {
        ctx->is_done = 1;
        pthread_mutex_unlock(&g_sdk_mutex);
        return 0;
    }

    ctx->total_samples_rendered += samples_rendered;

    if (ctx->duration_sec > 0.0) {
        double max_time = ctx->duration_sec + ctx->fade_sec;
        double current_time = (double)ctx->total_samples_rendered / ctx->sample_rate;
        if (current_time >= max_time) {
            ctx->is_done = 1;
        }
    }

    pthread_mutex_unlock(&g_sdk_mutex);
    return samples_rendered;
}

int aosdk_stop(AOSDKContext* ctx) {
    if (!ctx) return AOSDK_ERROR_NULL_POINTER;

    pthread_mutex_lock(&g_sdk_mutex);
    if (ctx->is_open) {
        if (ctx->format == AOSDK_FORMAT_PSF2) {
            psf2_stop();
        } else {
            psf_stop();
        }
        if (ctx->corlett) {
            corlett_free(ctx->corlett);
            ctx->corlett = NULL;
        }
        ctx->is_open = 0;
        ctx->is_done = 1;
    }
    pthread_mutex_unlock(&g_sdk_mutex);
    return AOSDK_OK;
}

int aosdk_reset(AOSDKContext* ctx) {
    if (!ctx || !ctx->is_open) return AOSDK_ERROR_NOT_OPEN;

    pthread_mutex_lock(&g_sdk_mutex);
    if (ctx->format == AOSDK_FORMAT_PSF2) {
        psf2_stop();
        psf2_start(NULL);
    } else {
        psf_stop();
        psf_start(NULL);
    }
    ctx->total_samples_rendered = 0;
    ctx->is_done = 0;
    pthread_mutex_unlock(&g_sdk_mutex);
    return AOSDK_OK;
}

int aosdk_seek(AOSDKContext* ctx, double seconds) {
    if (!ctx) return AOSDK_ERROR_NULL_POINTER;
    if (seconds <= 0.05) {
        return aosdk_reset(ctx);
    }
    set_error(ctx, "Arbitrary seeking is unsupported by the PSF/PSF2 emulation engine (state is non-reversible). Use stop/restart instead.");
    return AOSDK_ERROR_SEEK_UNSUPPORTED;
}

int aosdk_is_open(AOSDKContext* ctx) {
    return (ctx && ctx->is_open) ? 1 : 0;
}

int aosdk_is_done(AOSDKContext* ctx) {
    return (!ctx || ctx->is_done) ? 1 : 0;
}

int aosdk_get_format(AOSDKContext* ctx) {
    return ctx ? ctx->format : AOSDK_FORMAT_NONE;
}

int aosdk_get_sample_rate(AOSDKContext* ctx) {
    return ctx ? (int)ctx->sample_rate : 44100;
}

int aosdk_get_channels(AOSDKContext* ctx) {
    return ctx ? ctx->channels : 2;
}

int aosdk_get_duration(AOSDKContext* ctx, double* out_duration) {
    if (!ctx || !out_duration) return AOSDK_ERROR_NULL_POINTER;
    *out_duration = ctx->duration_sec;
    return AOSDK_OK;
}

int aosdk_get_fade(AOSDKContext* ctx, double* out_fade) {
    if (!ctx || !out_fade) return AOSDK_ERROR_NULL_POINTER;
    *out_fade = ctx->fade_sec;
    return AOSDK_OK;
}

double aosdk_get_position(AOSDKContext* ctx) {
    if (!ctx || !ctx->is_open) return 0.0;
    return (double)ctx->total_samples_rendered / ctx->sample_rate;
}

const char* aosdk_get_last_error(AOSDKContext* ctx) {
    if (!ctx) return "Null context";
    return ctx->last_error;
}

int aosdk_get_tag(AOSDKContext* ctx, const char* tag_name, char* out_buf, size_t buf_size) {
    if (!ctx || !tag_name || !out_buf || buf_size == 0) return AOSDK_ERROR_NULL_POINTER;
    if (!ctx->corlett) return AOSDK_ERROR_NOT_OPEN;

    pthread_mutex_lock(&g_sdk_mutex);
    const char* val = corlett_tag_lookup(ctx->corlett, tag_name);
    if (val) {
        snprintf(out_buf, buf_size, "%s", val);
        pthread_mutex_unlock(&g_sdk_mutex);
        return AOSDK_OK;
    }
    pthread_mutex_unlock(&g_sdk_mutex);
    return AOSDK_ERROR_INTERNAL;
}
