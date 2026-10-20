# ubuntu-24.04's packaged toolchains for dread bench (bench/toolchains/README.md)
FROM docker.io/library/ubuntu:24.04
RUN apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        gcc-arm-none-eabi binutils-arm-none-eabi libnewlib-arm-none-eabi clang-18 lld-18 \
    && rm -rf /var/lib/apt/lists/*
# the paths the recipes name (the host's): newlib's sysroot and an unversioned clang
RUN ln -s /usr/lib/arm-none-eabi /usr/arm-none-eabi \
    && ln -sf /usr/bin/clang-18 /usr/local/bin/clang && ln -sf /usr/bin/ld.lld-18 /usr/local/bin/ld.lld
