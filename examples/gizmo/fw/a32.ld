/* Gizmo, a32 build: flash and RAM; .ramfunc and .data are stored in flash and copied to RAM. */
ENTRY(_start)
MEMORY
{
    FLASH (rx)  : ORIGIN = 0x00020000, LENGTH = 0x20000
    RAM   (rwx) : ORIGIN = 0x40000000, LENGTH = 0x10000
}
SECTIONS
{
    .text : {
        KEEP(*(.vectors))
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
    __irq_stack_top = __stack_top - 0x800;
    _estack = __stack_top;
    /DISCARD/ : { *(.ARM.exidx*) *(.ARM.extab*) *(.comment) *(.note*) }
}
