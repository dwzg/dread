# Builds the three gizmo images from the same sources and refreshes the three target directories
# (../a32, ../m3 and ../rv32): the ELF (ground truth for the tests), the raw image, the tables made
# from the ELF by `dread import-elf` and the startup list in each dread.toml. a32 and m3 are built
# with arm-none-eabi-gcc, rv32 with clang and ld.lld. The built files are committed, so that the tests do not depend on
# the compiler version; run `make targets` after changing a source and commit what changes.
CROSS ?= arm-none-eabi-
CC := $(CROSS)gcc
CLANG ?= clang
DREAD := ../../../bin/dread
LDFLAGS := -Wl,--no-warn-rwx-segments
COMMON := -Os -g -ffile-prefix-map=$(CURDIR)=. -ffreestanding -fno-builtin -nostdlib -ffunction-sections -fno-common -Wall -Wextra
A32 := -march=armv4t -marm -mabi=aapcs -mfloat-abi=soft
M3 := -mcpu=cortex-m3 -mthumb -mfloat-abi=soft -DGIZMO_M3
RV32 := --target=riscv32-unknown-elf -march=rv32imc -mabi=ilp32 -fuse-ld=lld -DGIZMO_RV32
SRC := lib.c util.c hw.c main.c

.PHONY: all targets clean
all: build/a32/gizmo.elf build/m3/gizmo.elf build/rv32/gizmo.elf

build/a32/gizmo.elf: start_a32.S $(SRC) a32.ld gizmo.h Makefile
	mkdir -p $(dir $@)
	$(CC) $(A32) $(COMMON) -T a32.ld $(LDFLAGS) -Wl,-Map=$(@:.elf=.map) -o $@ start_a32.S $(SRC) -lgcc

build/m3/gizmo.elf: start_m3.c $(SRC) m3.ld gizmo.h Makefile
	mkdir -p $(dir $@)
	$(CC) $(M3) $(COMMON) -T m3.ld $(LDFLAGS) -Wl,-Map=$(@:.elf=.map) -o $@ start_m3.c $(SRC) -lgcc

build/rv32/gizmo.elf: start_rv32.S $(SRC) rv32.ld gizmo.h Makefile
	mkdir -p $(dir $@)
	$(CLANG) $(RV32) $(COMMON) -T rv32.ld -Wl,-Map=$(@:.elf=.map) -o $@ start_rv32.S $(SRC)

targets: all
	for v in a32 m3 rv32; do \
	    mkdir -p ../$$v/original && cp build/$$v/gizmo.elf ../$$v/original/gizmo.elf && \
	    $(DREAD) import-elf build/$$v/gizmo.elf --target ../$$v --payload ../$$v/original/gizmo.bin --write-startup && \
	    mkdir -p ../$$v/src && python3 addresses.py build/$$v/gizmo.elf ../$$v/src/addresses.h $$v || exit 1; \
	done

clean:
	rm -rf build
