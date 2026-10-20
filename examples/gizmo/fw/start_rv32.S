/* Gizmo, RV32 build: the reset entry, the startup's copy table, the trap entry (machine mode). */
    .option norelax

    .section .text.start, "ax"
    .globl _start
    .type _start, @function
_start:
    la      sp, __stack_top
    la      t0, trap_entry
    csrw    mtvec, t0               # direct mode: every trap enters at trap_entry
    la      a3, copy_table
1:  lw      a0, 0(a3)               # destination
    lw      a1, 4(a3)               # source (0: zero the range)
    lw      a2, 8(a3)               # length in bytes
    addi    a3, a3, 12
    beqz    a2, 4f
    beqz    a1, 3f
2:  lw      t0, 0(a1)
    sw      t0, 0(a0)
    addi    a1, a1, 4
    addi    a0, a0, 4
    addi    a2, a2, -4
    bgtz    a2, 2b
    j       1b
3:  sw      zero, 0(a0)
    addi    a0, a0, 4
    addi    a2, a2, -4
    bgtz    a2, 3b
    j       1b
4:
#ifdef GIZMO_CPP
    la      s0, __init_array_start  # the C++ part's static constructors (rv32cpp.ld keeps .init_array)
    la      s1, __init_array_end
6:  bgeu    s0, s1, 7f
    lw      t0, 0(s0)
    jalr    t0
    addi    s0, s0, 4
    j       6b
7:
#endif
    li      t0, 0x800               # mie.MEIE: the interrupt controller's line
    csrs    mie, t0
    csrsi   mstatus, 8              # mstatus.MIE
    call    main
5:  j       5b
    .size _start, . - _start

    .text
    .p2align 2                      # mtvec in direct mode needs a 4-byte aligned entry
    .globl trap_entry
    .type trap_entry, @function
trap_entry:
    addi    sp, sp, -64
    sw      ra, 60(sp)
    sw      t0, 56(sp)
    sw      t1, 52(sp)
    sw      t2, 48(sp)
    sw      a0, 44(sp)
    sw      a1, 40(sp)
    sw      a2, 36(sp)
    sw      a3, 32(sp)
    sw      a4, 28(sp)
    sw      a5, 24(sp)
    sw      a6, 20(sp)
    sw      a7, 16(sp)
    sw      t3, 12(sp)
    sw      t4, 8(sp)
    sw      t5, 4(sp)
    sw      t6, 0(sp)
    call    irq_dispatch
    lw      ra, 60(sp)
    lw      t0, 56(sp)
    lw      t1, 52(sp)
    lw      t2, 48(sp)
    lw      a0, 44(sp)
    lw      a1, 40(sp)
    lw      a2, 36(sp)
    lw      a3, 32(sp)
    lw      a4, 28(sp)
    lw      a5, 24(sp)
    lw      a6, 20(sp)
    lw      a7, 16(sp)
    lw      t3, 12(sp)
    lw      t4, 8(sp)
    lw      t5, 4(sp)
    lw      t6, 0(sp)
    addi    sp, sp, 64
    mret
    .size trap_entry, . - trap_entry

    .section .rodata.copy_table, "a"
    .p2align 2
    .type copy_table, @object
copy_table:
    .word __data_start, __data_load, __data_size
    .word __ramfunc_start, __ramfunc_load, __ramfunc_size
    .word __bss_start, 0, __bss_size
    .word 0, 0, 0
    .size copy_table, . - copy_table
