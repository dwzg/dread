/* Gizmo, MSP430 build: the reset entry, the startup's copy table, the interrupt entry, the vectors. */
    .section .text.start, "ax", @progbits
    .global _start
    .type _start, @function
_start:
    mov     #__stack_top, r1
    mov     #copy_table, r10
1:  mov     @r10+, r12              ; destination
    mov     @r10+, r13              ; source (0: zero the range)
    mov     @r10+, r14              ; length in bytes (even)
    tst     r14
    jz      4f
    tst     r13
    jz      3f
2:  mov     @r13+, r15              ; (llvm-mc has no @rN+ source with an indexed destination)
    mov     r15, 0(r12)
    incd    r12
    decd    r14
    jnz     2b
    jmp     1b
3:  clr     0(r12)
    incd    r12
    decd    r14
    jnz     3b
    jmp     1b
4:  eint
    call    #main
5:  jmp     5b
    .size _start, . - _start

    .text
    .global irq_entry
    .type irq_entry, @function
irq_entry:                          ; the core pushed pc and sr and cleared GIE
    push    r11
    push    r12
    push    r13
    push    r14
    push    r15
    call    #irq_dispatch
    pop     r15
    pop     r14
    pop     r13
    pop     r12
    pop     r11
    reti
    .size irq_entry, . - irq_entry

    .section .rodata.copy_table, "a"
    .p2align 1
    .type copy_table, @object
copy_table:
    .word   __data_start, __data_load, __data_size
    .word   __ramfunc_start, __ramfunc_load, __ramfunc_size
    .word   __bss_start, 0, __bss_size
    .word   0, 0, 0
    .size copy_table, . - copy_table

    .section .vectors, "a"          ; 0xffe0-0xfffe: every interrupt to irq_entry, reset to _start
    .word   irq_entry, irq_entry, irq_entry, irq_entry, irq_entry, irq_entry, irq_entry, irq_entry
    .word   irq_entry, irq_entry, irq_entry, irq_entry, irq_entry, irq_entry, irq_entry, _start
