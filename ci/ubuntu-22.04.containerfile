# bin/ci-container's image: Ubuntu 22.04 as CI's runner of that release (.github/workflows/test.yml):
# the cross compilers and LLVM the workflow installs (GCC 10 for ARM, clang 14), the runner's GCC 11 with
# its 32-bit libraries (the x86 tests compile with gcc -m32), and Python 3.11 (the release's python3 is
# 3.10, older than DREAD takes; CI sets 3.11 up)
FROM docker.io/library/ubuntu:22.04
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        gcc-arm-none-eabi binutils-arm-none-eabi libnewlib-arm-none-eabi gcc-riscv64-unknown-elf \
        binutils-riscv64-unknown-elf clang lld llvm gcc-multilib python3.11 python3.11-venv \
        ca-certificates \
    && rm -rf /var/lib/apt/lists/*
ENV DREAD_CI_PYTHON=python3.11
