# Builds the gizmo images from the same sources and refreshes their target directories (../a32,
# ../m3, ../m4, ../rv32, ../msp430): the ELF (ground truth for the tests), the raw image, the tables
# made from the ELF by `dread import-elf` and the startup list in each dread.toml. a32, m3 and m4
# (hard float on FPv4-SP, with dsp.c) are built with arm-none-eabi-gcc, m3cpp too with shapes.cpp (g++);
# rv32cpp is rv32 with shapes.cpp (clang++), rv32 with clang and ld.lld, msp430 with TI's MSP430 GCC (msp430-elf-,
# release 8.2.0.52: the 16-bit instruction set, libgcc's multiply and divide helpers). The clang
# build of the MSP430 (build/msp430clang, its own helpers in msp430_rt.c) is not a target yet. The built files are committed, so that the tests do not depend on
# the compiler version; run `make targets` after changing a source and commit what changes.
CROSS ?= arm-none-eabi-
CC := $(CROSS)gcc
CLANG ?= clang
DREAD := ../../../bin/dread
LDFLAGS := -Wl,--no-warn-rwx-segments
COMMON := -Os -g -ffile-prefix-map=$(CURDIR)=. -ffreestanding -fno-builtin -nostdlib -ffunction-sections -fno-common -Wall -Wextra
A32 := -march=armv4t -marm -mabi=aapcs -mfloat-abi=soft
M3 := -mcpu=cortex-m3 -mthumb -mfloat-abi=soft -DGIZMO_M3
M4 := -mcpu=cortex-m4 -mthumb -mfloat-abi=hard -mfpu=fpv4-sp-d16 -DGIZMO_M3 -DGIZMO_M4
RV32 := --target=riscv32-unknown-elf -march=rv32imc -mabi=ilp32 -fuse-ld=lld -DGIZMO_RV32
MIPS := --target=mipsel-unknown-elf -march=mips32r2 -mno-abicalls -fno-pic -DGIZMO_MIPS
MIPS_OBJ := $(patsubst %,build/mips/%.o,start_mips lib util hw main)
MSP430_CROSS ?= msp430-elf-
MSP430 := -mcpu=msp430 -mhwmult=none -DGIZMO_MSP430
MSP430CLANG := --target=msp430 -DGIZMO_MSP430
MSP430CLANG_OBJ := $(patsubst %,build/msp430clang/%.o,start_msp430 lib util hw main msp430_rt)
SRC := lib.c util.c hw.c main.c

.PHONY: all targets clean
all: build/a32/gizmo.elf build/m3/gizmo.elf build/m4/gizmo.elf build/m3cpp/gizmo.elf build/rv32/gizmo.elf \
     build/rv32cpp/gizmo.elf build/mips/gizmo.elf build/msp430/gizmo.elf

build/a32/gizmo.elf: start_a32.S $(SRC) a32.ld gizmo.h Makefile
	mkdir -p $(dir $@)
	$(CC) $(A32) $(COMMON) -T a32.ld $(LDFLAGS) -Wl,-Map=$(@:.elf=.map) -o $@ start_a32.S $(SRC) -lgcc

build/m3/gizmo.elf: start_m3.c $(SRC) m3.ld gizmo.h Makefile
	mkdir -p $(dir $@)
	$(CC) $(M3) $(COMMON) -T m3.ld $(LDFLAGS) -Wl,-Map=$(@:.elf=.map) -o $@ start_m3.c $(SRC) -lgcc

build/m4/gizmo.elf: start_m3.c $(SRC) dsp.c m3.ld gizmo.h Makefile
	mkdir -p $(dir $@)
	$(CC) $(M4) $(COMMON) -T m3.ld $(LDFLAGS) -Wl,-Map=$(@:.elf=.map) -o $@ start_m3.c $(SRC) dsp.c -lgcc

# The C++ build: the Cortex-M3 build plus shapes.cpp (arm-none-eabi-g++, no exceptions, no RTTI, no
# library), its static constructors run by the startup
CXX := $(CROSS)g++
CPPFLAGS_CXX := -fno-exceptions -fno-rtti -fno-threadsafe-statics -fno-use-cxa-atexit
build/m3cpp/shapes.o: shapes.cpp gizmo.h Makefile
	mkdir -p $(dir $@)
	$(CXX) $(M3) -DGIZMO_CPP $(COMMON) $(CPPFLAGS_CXX) -c -o $@ shapes.cpp

build/m3cpp/gizmo.elf: start_m3.c $(SRC) build/m3cpp/shapes.o m3cpp.ld gizmo.h Makefile
	mkdir -p $(dir $@)
	$(CC) $(M3) -DGIZMO_CPP $(COMMON) -T m3cpp.ld $(LDFLAGS) -Wl,-Map=$(@:.elf=.map) -o $@ start_m3.c $(SRC) \
	    build/m3cpp/shapes.o -lgcc

# The C++ build for RISC-V: the rv32 build plus shapes.cpp (clang++)
CLANGXX ?= clang++
build/rv32cpp/shapes.o: shapes.cpp gizmo.h Makefile
	mkdir -p $(dir $@)
	$(CLANGXX) $(filter-out -fuse-ld=lld,$(RV32)) -DGIZMO_CPP $(COMMON) $(CPPFLAGS_CXX) -c -o $@ shapes.cpp

build/rv32cpp/gizmo.elf: start_rv32.S $(SRC) build/rv32cpp/shapes.o rv32cpp.ld gizmo.h Makefile
	mkdir -p $(dir $@)
	$(CLANG) $(RV32) -DGIZMO_CPP $(COMMON) -T rv32cpp.ld -Wl,-Map=$(@:.elf=.map) -o $@ start_rv32.S $(SRC) \
	    build/rv32cpp/shapes.o

build/rv32/gizmo.elf: start_rv32.S $(SRC) rv32.ld gizmo.h Makefile
	mkdir -p $(dir $@)
	$(CLANG) $(RV32) $(COMMON) -T rv32.ld -Wl,-Map=$(@:.elf=.map) -o $@ start_rv32.S $(SRC)

# The MIPS build (clang has no bare-metal driver for MIPS and would link with the host's gcc: compile
# with clang, link with ld.lld)
build/mips/%.o: %.c gizmo.h Makefile
	mkdir -p $(dir $@)
	$(CLANG) $(MIPS) $(filter-out -nostdlib,$(COMMON)) -c -o $@ $<

build/mips/start_mips.o: start_mips.S Makefile
	mkdir -p $(dir $@)
	$(CLANG) $(MIPS) $(filter-out -nostdlib,$(COMMON)) -c -o $@ $<

build/mips/gizmo.elf: $(MIPS_OBJ) mips.ld
	ld.lld -T mips.ld -Map=$(@:.elf=.map) -o $@ $(MIPS_OBJ)

build/msp430/gizmo.elf: start_msp430.S $(SRC) msp430.ld gizmo.h Makefile
	mkdir -p $(dir $@)
	$(MSP430_CROSS)gcc $(MSP430) $(COMMON) -T msp430.ld -Wl,-Map=$(@:.elf=.map) -o $@ start_msp430.S $(SRC) \
	    -lgcc -lmul_none -lgcc

# The clang build (clang's MSP430 driver links with GNU's msp430-elf-ld only: compile with clang,
# link with ld.lld)
build/msp430clang/%.o: %.c gizmo.h Makefile
	mkdir -p $(dir $@)
	$(CLANG) $(MSP430CLANG) $(filter-out -nostdlib,$(COMMON)) -c -o $@ $<

build/msp430clang/start_msp430.o: start_msp430.S Makefile
	mkdir -p $(dir $@)
	$(CLANG) $(MSP430CLANG) -g -c -o $@ $<

build/msp430clang/gizmo.elf: $(MSP430CLANG_OBJ) msp430.ld
	ld.lld -m msp430elf -T msp430.ld -Map=$(@:.elf=.map) -o $@ $(MSP430CLANG_OBJ)

targets: all
	for v in a32 m3 m4 m3cpp rv32 rv32cpp mips msp430; do \
	    mkdir -p ../$$v/original && cp build/$$v/gizmo.elf ../$$v/original/gizmo.elf && \
	    $(DREAD) import-elf build/$$v/gizmo.elf --target ../$$v --payload ../$$v/original/gizmo.bin --write-startup && \
	    mkdir -p ../$$v/src && python3 addresses.py build/$$v/gizmo.elf ../$$v/src/addresses.h $$v || exit 1; \
	done

clean:
	rm -rf build
