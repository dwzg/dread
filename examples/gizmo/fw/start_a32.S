/* Gizmo, ARM7-style build: exception table, stacks, the startup's copy table, the IRQ entry. */
    .syntax unified
    .arm

    .section .vectors, "ax"
    .global _start
_start:
    b   reset
    b   hang                    @ undefined instruction
    b   hang                    @ software interrupt
    b   hang                    @ prefetch abort
    b   hang                    @ data abort
    .word copy_table            @ (unused vector): where the startup's copy table is
    b   irq_entry
    b   hang                    @ fast interrupt

    .text
    .type reset, %function
reset:
    msr cpsr_c, #0xD2           @ IRQ mode, interrupts masked
    ldr sp, =__irq_stack_top
    msr cpsr_c, #0xD3           @ supervisor mode, interrupts masked
    ldr sp, =__stack_top
    ldr r4, =copy_table
1:  ldmia r4!, {r0, r1, r2}     @ destination, source (0: zero the range), length in bytes
    cmp r2, #0
    beq 4f
    cmp r1, #0
    beq 3f
2:  ldr r3, [r1], #4
    str r3, [r0], #4
    subs r2, r2, #4
    bhi 2b
    b   1b
3:  mov r3, #0
5:  str r3, [r0], #4
    subs r2, r2, #4
    bhi 5b
    b   1b
4:  msr cpsr_c, #0x53           @ supervisor mode, IRQ enabled
    bl  main
    .size reset, . - reset

    .type hang, %function
hang:
    b   hang
    .size hang, . - hang

    .type irq_entry, %function
irq_entry:
    sub lr, lr, #4
    stmfd sp!, {r0-r3, r12, lr}
    bl  irq_dispatch
    ldmfd sp!, {r0-r3, r12, pc}^
    .size irq_entry, . - irq_entry

    .section .rodata.copy_table, "a"
    .align 2
    .type copy_table, %object
copy_table:
    .word __data_start, __data_load, __data_size
    .word __ramfunc_start, __ramfunc_load, __ramfunc_size
    .word __bss_start, 0, __bss_size
    .word 0, 0, 0
    .size copy_table, . - copy_table
