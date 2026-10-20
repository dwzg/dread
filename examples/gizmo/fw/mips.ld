/* Gizmo, MIPS32 build: the boot flash in kseg1 (the reset vector at 0xbfc00000, the general
   exception vector at 0xbfc00380 while Status.BEV is set), RAM in kseg0; .ramfunc and .data are
   stored in flash and copied to RAM; small data (.sdata, .sbss) is addressed from gp. */
ENTRY(_start)
MEMORY
{
    FLASH (rx)  : ORIGIN = 0xbfc00000, LENGTH = 0x20000
    RAM   (rwx) : ORIGIN = 0x80000000, LENGTH = 0x10000
}
SECTIONS
{
    .text : {
        KEEP(*(.text.start))
        . = ORIGIN(FLASH) + 0x380;      /* the general exception vector (Status.BEV set) */
        KEEP(*(.text.vector))
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
        . = ALIGN(16);
        _gp = . + 0x7ff0;
        *(.sdata .sdata.*)
        . = ALIGN(4);
        __data_end = .;
    } > RAM AT > FLASH
    __data_load = LOADADDR(.data);
    .bss (NOLOAD) : {
        __bss_start = .;
        *(.sbss .sbss.* .scommon)
        *(.bss .bss.* COMMON)
        . = ALIGN(4);
        __bss_end = .;
    } > RAM
    __data_size = __data_end - __data_start;
    __ramfunc_size = __ramfunc_end - __ramfunc_start;
    __bss_size = __bss_end - __bss_start;
    __stack_top = ORIGIN(RAM) + LENGTH(RAM);
    _estack = __stack_top;
    /DISCARD/ : { *(.comment) *(.note*) *(.pdr) *(.gnu.attributes) *(.mdebug*) *(.MIPS.abiflags) *(.reginfo) *(.got) }
}
