# debian-13's packaged toolchains for dread bench (bench/toolchains/README.md)
FROM docker.io/library/debian:13
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        gcc-arm-none-eabi binutils-arm-none-eabi libnewlib-arm-none-eabi clang-19 lld-19 llvm-19 \
        gcc-riscv64-unknown-elf binutils-riscv64-unknown-elf picolibc-riscv64-unknown-elf \
    && rm -rf /var/lib/apt/lists/*
# the paths the recipes name (the host's): newlib's sysroot, an unversioned clang, and RISC-V's
# tools by the host's prefix (riscv64-elf-; picolibc: the distributions package no newlib for RISC-V)
RUN ln -s /usr/lib/arm-none-eabi /usr/arm-none-eabi \
    && ln -sf /usr/bin/clang-19 /usr/local/bin/clang && ln -sf /usr/bin/ld.lld-19 /usr/local/bin/ld.lld \
    && for t in /usr/bin/riscv64-unknown-elf-*; do ln -s "$t" "/usr/local/bin/riscv64-elf-${t#/usr/bin/riscv64-unknown-elf-}"; done
