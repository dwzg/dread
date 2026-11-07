/* Gizmo, arc build (ARC600): flash at 0 with the interrupt vector table at its start, RAM at
   0x00100000 (within reach of bl's 25-bit displacement from the flash, so that calls into .ramfunc
   need no long call); .ramfunc and .data (with GCC's small data) are stored in flash and copied to
   RAM. gp points at the small data (__SDATA_BEGIN__); .sbss follows .data's small data at the start
   of .bss. */
ENTRY(_start)
MEMORY
{
    FLASH (rx)  : ORIGIN = 0x00000000, LENGTH = 0x00020000
    RAM   (rwx) : ORIGIN = 0x00100000, LENGTH = 0x00010000
}
SECTIONS
{
    .text : {
        KEEP(*(.vectors))
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
        __SDATA_BEGIN__ = .;
        *(.sdata .sdata.*)
        . = ALIGN(4);
        __data_end = .;
    } > RAM AT > FLASH
    __data_load = LOADADDR(.data);
    .bss (NOLOAD) : {
        __bss_start = .;
        *(.sbss .sbss.*)
        *(.bss .bss.* COMMON)
        . = ALIGN(4);
        __bss_end = .;
    } > RAM
    __data_size = __data_end - __data_start;
    __ramfunc_size = __ramfunc_end - __ramfunc_start;
    __bss_size = __bss_end - __bss_start;
    __stack_top = ORIGIN(RAM) + LENGTH(RAM);
    /DISCARD/ : { *(.comment) *(.note*) *(.ARC.attributes) *(.eh_frame) }
}
