# debian-12's packaged toolchains for dread bench (bench/toolchains/README.md)
FROM docker.io/library/debian:12
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        gcc-arm-none-eabi binutils-arm-none-eabi libnewlib-arm-none-eabi clang-16 lld-16 llvm-16 \
        gcc-riscv64-unknown-elf binutils-riscv64-unknown-elf picolibc-riscv64-unknown-elf \
        gcc-i686-linux-gnu binutils-i686-linux-gnu libc6-dev-i386-cross \
    && rm -rf /var/lib/apt/lists/*
# the paths the recipes name (the host's): newlib's sysroot, an unversioned clang, and RISC-V's
# tools by the host's prefix (riscv64-elf-; picolibc: the distributions package no newlib for RISC-V)
RUN ln -s /usr/lib/arm-none-eabi /usr/arm-none-eabi \
    && ln -sf /usr/bin/clang-16 /usr/local/bin/clang && ln -sf /usr/bin/ld.lld-16 /usr/local/bin/ld.lld \
    && for t in /usr/bin/riscv64-unknown-elf-*; do ln -s "$t" "/usr/local/bin/riscv64-elf-${t#/usr/bin/riscv64-unknown-elf-}"; done
