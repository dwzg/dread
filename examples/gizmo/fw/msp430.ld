/* Gizmo, msp430 build: flash with the vectors at its top, RAM; .ramfunc and .data are stored in flash
   and copied to RAM. */
ENTRY(_start)
MEMORY
{
    RAM     (rwx) : ORIGIN = 0x0200, LENGTH = 0x0800
    FLASH   (rx)  : ORIGIN = 0x8000, LENGTH = 0x7fe0
    VECTORS (r)   : ORIGIN = 0xffe0, LENGTH = 0x0020
}
SECTIONS
{
    .text : {
        KEEP(*(.text.start))
        *(.text .text.*)
        *(.rodata .rodata.*)
        . = ALIGN(2);
    } > FLASH
    .ramfunc : {
        __ramfunc_start = .;
        *(.ramfunc .ramfunc.*)
        . = ALIGN(2);
        __ramfunc_end = .;
    } > RAM AT > FLASH
    __ramfunc_load = LOADADDR(.ramfunc);
    .data : {
        __data_start = .;
        *(.data .data.*)
        . = ALIGN(2);
        __data_end = .;
    } > RAM AT > FLASH
    __data_load = LOADADDR(.data);
    .bss (NOLOAD) : {
        __bss_start = .;
        *(.bss .bss.* COMMON)
        . = ALIGN(2);
        __bss_end = .;
    } > RAM
    .vectors : { KEEP(*(.vectors)) } > VECTORS
    __data_size = __data_end - __data_start;
    __ramfunc_size = __ramfunc_end - __ramfunc_start;
    __bss_size = __bss_end - __bss_start;
    __stack_top = ORIGIN(RAM) + LENGTH(RAM);
    /DISCARD/ : { *(.comment) *(.note*) *(.MSP430.attributes) *(.eh_frame) }
}
