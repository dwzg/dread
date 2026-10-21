# Toolchain images for dread bench

One Containerfile per toolchain: a distribution's packaged cross compilers, so that a recipe's build
can name `toolchain = "NAME"` and be compiled and linked with that distribution's versions instead
of the host's (`dread bench toolchain NAME` builds the image `localhost/dread-tc-NAME:DIGEST` with
podman, DIGEST the start of the file's SHA-1). The images link the packaged files to the paths the
recipes name on the host (Arch's: `/usr/arm-none-eabi` for newlib, `riscv64-elf-gcc` for the
distributions' `riscv64-unknown-elf-gcc`), so that a recipe's flags work in any of them. Only the
compile and link steps run in the container; DREAD itself runs on the host.

| Image | ARM GCC (binutils) | RISC-V GCC (binutils, C library) | clang |
|---|---|---|---|
| ubuntu-16.04 | 4.9.3 (2.26) | none | none |
| debian-9 | 5.4.1 (2.28) | none | none |
| ubuntu-18.04 | 6.3.1 (2.27) | none | none |
| debian-10 | 7.3.1 (2.31.1) | none | none |
| debian-11 | 8.3.1 (2.35.2) | none | none |
| ubuntu-20.04 | 9.2 (2.34) | none (no C library is packaged for it) | 12.0 |
| ubuntu-22.04 | 10.3 (2.38) | 10.2 (2.35.1, picolibc 1.7.4) | 14.0 |
| debian-12 | 12.2 (2.40) | 12.2 (2.40, picolibc 1.8) | 16.0 |
| ubuntu-24.04 | 13.2 (2.42) | 13.2 (2.42, picolibc 1.8.6) | 18.1 |
| debian-13 | 14.2 (2.44) | 14.2 (2.44, picolibc 1.8.10) | 19.1 |

The five oldest images hold ARM GCC and newlib only: the GCC versions of ARM's GNU Arm Embedded
toolchains (4.9 to 8, 2015 to 2019) that vendor SDKs shipped and much firmware in the field was built
with. Debian 9 and 10 are read from archive.debian.org (their releases are archived), Debian 11 and
the Ubuntu images fall back to the archive mirrors when the regular ones no longer serve them.

The distributions package newlib for ARM only, so the RISC-V builds link picolibc
(`--specs=picolibc.specs`). Two flags differ by version: GCC 10 knows no `_zicsr` in `-march` (its
ISA spec has the CSR instructions in the base), and GCC 12 knows it but then finds no multilib for
`rv32imac_zicsr` and links the 64-bit library, so it gets `-march=rv32imac -misa-spec=2.2`.

The digest in the image's name keeps a changed file from replacing an image a run still uses (a
worktree that adds a package builds an image of its own beside the one a measurement from the
checkout uses). `dread bench toolchain NAME... --prune` removes the images of a toolchain's older
files.
