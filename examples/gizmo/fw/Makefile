# Builds the two gizmo images from the same sources and refreshes the two target directories
# (../a32 and ../m3): the ELF (ground truth for the tests), the raw image and the tables made from
# the ELF by `dread import-elf`. The built files are committed, so that the tests do not depend on
# the compiler version; run `make targets` after changing a source and commit what changes.
CROSS ?= arm-none-eabi-
CC := $(CROSS)gcc
DREAD := ../../../bin/dread
LDFLAGS := -Wl,--no-warn-rwx-segments
COMMON := -Os -g -ffile-prefix-map=$(CURDIR)=. -ffreestanding -fno-builtin -nostdlib -ffunction-sections -fno-common -Wall -Wextra
A32 := -march=armv4t -marm -mabi=aapcs -mfloat-abi=soft
M3 := -mcpu=cortex-m3 -mthumb -mfloat-abi=soft -DGIZMO_M3
SRC := lib.c util.c hw.c main.c

.PHONY: all targets clean
all: build/a32/gizmo.elf build/m3/gizmo.elf

build/a32/gizmo.elf: start_a32.S $(SRC) a32.ld gizmo.h Makefile
	mkdir -p $(dir $@)
	$(CC) $(A32) $(COMMON) -T a32.ld $(LDFLAGS) -Wl,-Map=$(@:.elf=.map) -o $@ start_a32.S $(SRC) -lgcc

build/m3/gizmo.elf: start_m3.c $(SRC) m3.ld gizmo.h Makefile
	mkdir -p $(dir $@)
	$(CC) $(M3) $(COMMON) -T m3.ld $(LDFLAGS) -Wl,-Map=$(@:.elf=.map) -o $@ start_m3.c $(SRC) -lgcc

targets: all
	for v in a32 m3; do \
	    mkdir -p ../$$v/original && cp build/$$v/gizmo.elf ../$$v/original/gizmo.elf && \
	    $(DREAD) import-elf build/$$v/gizmo.elf --target ../$$v --payload ../$$v/original/gizmo.bin && \
	    mkdir -p ../$$v/src && python3 addresses.py build/$$v/gizmo.elf ../$$v/src/addresses.h $$v || exit 1; \
	done

clean:
	rm -rf build
