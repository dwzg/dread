# ubuntu-16.04's packaged toolchains for dread bench (bench/toolchains/README.md)
FROM docker.io/library/ubuntu:16.04
RUN (apt-get update || (sed -i 's|http://[a-z.]*ubuntu.com/ubuntu|http://old-releases.ubuntu.com/ubuntu|' /etc/apt/sources.list \
        && apt-get update)) \
    && DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
        gcc-arm-none-eabi binutils-arm-none-eabi libnewlib-arm-none-eabi \
    && rm -rf /var/lib/apt/lists/*
# the path the recipes name (the host's): newlib's sysroot
RUN ln -s /usr/lib/arm-none-eabi /usr/arm-none-eabi
