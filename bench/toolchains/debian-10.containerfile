# debian-10's packaged toolchains for dread bench (bench/toolchains/README.md); buster is archived
FROM docker.io/library/debian:10
RUN printf 'deb http://archive.debian.org/debian buster main\n' > /etc/apt/sources.list \
    && apt-get -o Acquire::Check-Valid-Until=false update \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        gcc-arm-none-eabi binutils-arm-none-eabi libnewlib-arm-none-eabi \
    && rm -rf /var/lib/apt/lists/*
# the path the recipes name (the host's): newlib's sysroot
RUN ln -s /usr/lib/arm-none-eabi /usr/arm-none-eabi
