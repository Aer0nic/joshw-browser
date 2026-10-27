#ifndef AOSDK_WRAPPER_H
#define AOSDK_WRAPPER_H

#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

#define AOSDK_OK                       0
#define AOSDK_ERROR_NULL_POINTER      -1
#define AOSDK_ERROR_FILE_NOT_FOUND    -2
#define AOSDK_ERROR_INVALID_FORMAT    -3
#define AOSDK_ERROR_ENGINE_INIT       -4
#define AOSDK_ERROR_SEEK_UNSUPPORTED  -5
#define AOSDK_ERROR_NOT_OPEN          -6
#define AOSDK_ERROR_BUFFER_TOO_SMALL  -7
#define AOSDK_ERROR_INTERNAL          -8

#define AOSDK_FORMAT_NONE  0
#define AOSDK_FORMAT_PSF   1
#define AOSDK_FORMAT_PSF2  2

typedef struct AOSDKContext AOSDKContext;

AOSDKContext* aosdk_create(void);
void          aosdk_destroy(AOSDKContext* ctx);

int           aosdk_open(AOSDKContext* ctx, const char* filepath);
int           aosdk_render(AOSDKContext* ctx, int16_t* buffer, int num_samples);
int           aosdk_stop(AOSDKContext* ctx);
int           aosdk_reset(AOSDKContext* ctx);
int           aosdk_seek(AOSDKContext* ctx, double seconds);

int           aosdk_is_open(AOSDKContext* ctx);
int           aosdk_is_done(AOSDKContext* ctx);
int           aosdk_get_format(AOSDKContext* ctx);
int           aosdk_get_sample_rate(AOSDKContext* ctx);
int           aosdk_get_channels(AOSDKContext* ctx);

int           aosdk_get_duration(AOSDKContext* ctx, double* out_duration);
int           aosdk_get_fade(AOSDKContext* ctx, double* out_fade);
double        aosdk_get_position(AOSDKContext* ctx);
const char*   aosdk_get_last_error(AOSDKContext* ctx);
int           aosdk_get_tag(AOSDKContext* ctx, const char* tag_name, char* out_buf, size_t buf_size);

#ifdef __cplusplus
}
#endif

#endif /* AOSDK_WRAPPER_H */
