/* Gizmo, i386 build: flash and RAM (a flat 32-bit address space); .ramfunc and .data are stored in flash and copied to RAM. */
ENTRY(_start)
MEMORY
{
    FLASH (rx)  : ORIGIN = 0x08000000, LENGTH = 0x20000
    RAM   (rwx) : ORIGIN = 0x20000000, LENGTH = 0x10000
}
SECTIONS
{
    .text : {
        KEEP(*(.text.start))
        *(.text .text.*)
        *(.rodata .rodata.*)
        . = ALIGN(4);
    } > FLASH
    .ramfunc : {
        __ramfunc_start = .;
        *(.ramfunc .ramfunc.*)
        . = ALIGN(4);
        __ramfunc_end = .;
    } > RAM AT > FLASH
    __ramfunc_load = LOADADDR(.ramfunc);
    .data : {
        __data_start = .;
        *(.data .data.*)
        . = ALIGN(4);
        __data_end = .;
    } > RAM AT > FLASH
    __data_load = LOADADDR(.data);
    .bss (NOLOAD) : {
        __bss_start = .;
        *(.bss .bss.* COMMON)
        . = ALIGN(4);
        __bss_end = .;
    } > RAM
    __data_size = __data_end - __data_start;
    __ramfunc_size = __ramfunc_end - __ramfunc_start;
    __bss_size = __bss_end - __bss_start;
    __stack_top = ORIGIN(RAM) + LENGTH(RAM);
    _estack = __stack_top;
    /DISCARD/ : { *(.comment) *(.note*) *(.eh_frame) *(.eh_frame_hdr) }
}
