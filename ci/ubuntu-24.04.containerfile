# bin/ci-container's image: Ubuntu 24.04 with what CI's runner has (.github/workflows/test.yml): the
# cross compilers and LLVM the workflow installs, and the runner's GCC with its 32-bit libraries (the
# x86 tests compile with gcc -m32), GCC 13 where the host has a newer one
FROM docker.io/library/ubuntu:24.04
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        gcc-arm-none-eabi binutils-arm-none-eabi libnewlib-arm-none-eabi gcc-riscv64-unknown-elf \
        binutils-riscv64-unknown-elf clang lld llvm gcc-multilib python3 python3-venv python3-pip \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*
