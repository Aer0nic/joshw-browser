#
# Modified Makefile for Audio Overload SDK - Linux Shared Library (PSF/PSF2)
#

CC   ?= gcc
LD   = $(CC)
CFLAGS += -O3 -fPIC -Wall -DPATH_MAX=1024 -DHAS_PSXCPU=1 -DLSB_FIRST=1 -DNDEBUG
CFLAGS += -I. -Ieng_ds -Ieng_psf -Ieng_psf/peops -Ieng_psf/peops2 -Izlib
LDFLAGS = -shared -Wl,-soname,libaosdk.so -lpthread -lz -lm

TARGET = assets/libraries/libaosdk.so

# Core audio driver files + your custom wrapper
OBJS = eng_ds/ao.o eng_ds/corlett.o eng_ds/utils.o aosdk_wrapper.o

# PSF engine core + MIPS/SPU submodules
OBJS += eng_psf/eng_psf.o eng_psf/psx.o eng_psf/psx_hw.o eng_psf/eng_spu.o
OBJS += $(wildcard eng_psf/mips/*.o) $(wildcard eng_psf/spu/*.o)

# PEOPS PSF1 SPU driver
OBJS += eng_psf/peops/spu.o eng_psf/peops/dma.o eng_psf/peops/registers.o eng_psf/peops/adsr.o eng_psf/peops/reverb.o

# PSF2 engine + PEOPS2 extensions
OBJS += eng_psf/eng_psf2.o eng_psf/peops2/spu.o eng_psf/peops2/dma.o eng_psf/peops2/registers.o

# zlib files required for compressed PSF containers
OBJS += zlib/adler32.o zlib/compress.o zlib/crc32.o zlib/gzio.o zlib/uncompr.o zlib/deflate.o zlib/trees.o
OBJS += zlib/zutil.o zlib/inflate.o zlib/infback.o zlib/inftrees.o zlib/inffast.o

.PHONY: all clean patch

all: patch $(TARGET)

# Automated patch step to ensure Linux integer types and extern declarations exist in PEOPS legacy code
patch:
	@echo "==> Ensuring Linux compatibility patches for PEOPS sources..."
	@python3 -c '\
import os\
def patch_file(path, target_line, insert_str):\
    if not os.path.exists(path): return\
    with open(path, "r") as f: content = f.read()\
    if insert_str.strip() in content: return\
    new_content = content.replace(target_line, insert_str + "\n" + target_line, 1)\
    with open(path, "w") as f: f.write(new_content)\
\
patch_file("eng_psf/peops/dma.c", "#define _IN_DMA", "#include <stdint.h>\n#include \"ao.h\"\n#ifndef u32\ntypedef uint32_t u32;\ntypedef uint16_t u16;\ntypedef uint8_t u8;\n#endif\nextern uint32 psx_ram[(2*1024*1024)/4];\nextern u16 spuMem[0x40000];\nextern u32 spuAddr;");\
patch_file("eng_psf/peops/registers.c", "#include \"stdafx.h\"", "#include <stdint.h>\n#include <stdio.h>\n#include <string.h>\n#include \"ao.h\"\n#ifndef u32\ntypedef uint32_t u32;\ntypedef uint16_t u16;\ntypedef uint8_t u8;\ntypedef int32_t s32;\ntypedef int16_t s16;\ntypedef int8_t s8;\n#endif\n#define _IN_REGISTERS");\
'

$(TARGET): $(OBJS)
	@mkdir -p assets/libraries
	@echo "==> Linking shared library $(TARGET)..."
	$(LD) $(LDFLAGS) -o $@ $(OBJS)

%.o: %.c
	@$(CC) $(CFLAGS) -c $< -o $@

clean:
	rm -f $(OBJS) $(TARGET)
