# Toolchain images for dread bench

One Containerfile per toolchain: a distribution's packaged cross compilers, so that a recipe's build
can name `toolchain = "NAME"` and be compiled and linked with that distribution's versions instead
of the host's (`dread bench toolchain NAME` builds the image `localhost/dread-tc-NAME` with
podman). The images link the packaged files to the paths the recipes name on the host (Arch's:
`/usr/arm-none-eabi` for newlib, `riscv64-elf-gcc`), so that a recipe's flags work in any of them.
Only the compile and link steps run in the container; DREAD itself runs on the host.
