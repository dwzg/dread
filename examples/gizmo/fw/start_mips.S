/* Gizmo, MIPS32 build (little-endian, mips32r2, kernel mode): the reset entry at the boot vector,
   the startup's copy table, and the general exception vector (Status.BEV stays set: 0xbfc00380,
   mips.ld), which hands every interrupt to irq_dispatch. Delay slots are written out (noreorder). */
    .set noreorder
    .set noat

    .section .text.start, "ax"
    .globl _start
    .type _start, @function
_start:
    la      $sp, __stack_top
    la      $gp, _gp
    mfc0    $t0, $12                # Status: out of the reset's error level, interrupts off
    li      $t1, ~0x7               # (ERL, EXL, IE)
    and     $t0, $t0, $t1
    mtc0    $t0, $12
    la      $a3, copy_table
1:  lw      $a0, 0($a3)             # destination
    lw      $a1, 4($a3)             # source (0: zero the range)
    lw      $a2, 8($a3)             # length in bytes
    beqz    $a2, 4f
    addiu   $a3, $a3, 12
    beqz    $a1, 3f
    nop
2:  lw      $t0, 0($a1)
    sw      $t0, 0($a0)
    addiu   $a1, $a1, 4
    addiu   $a2, $a2, -4
    bgtz    $a2, 2b
    addiu   $a0, $a0, 4
    b       1b
    nop
3:  sw      $zero, 0($a0)
    addiu   $a2, $a2, -4
    bgtz    $a2, 3b
    addiu   $a0, $a0, 4
    b       1b
    nop
4:  mfc0    $t0, $12                # Status: IM2 (hardware interrupt 0: the interrupt controller) and IE
    ori     $t0, $t0, 0x401
    mtc0    $t0, $12
    jal     main
    nop
5:  b       5b
    nop
    .size _start, . - _start

    .section .text.vector, "ax"
    .globl exc_entry
    .type exc_entry, @function
exc_entry:
    addiu   $sp, $sp, -80
    sw      $at, 0($sp)
    sw      $v0, 4($sp)
    sw      $v1, 8($sp)
    sw      $a0, 12($sp)
    sw      $a1, 16($sp)
    sw      $a2, 20($sp)
    sw      $a3, 24($sp)
    sw      $t0, 28($sp)
    sw      $t1, 32($sp)
    sw      $t2, 36($sp)
    sw      $t3, 40($sp)
    sw      $t4, 44($sp)
    sw      $t5, 48($sp)
    sw      $t6, 52($sp)
    sw      $t7, 56($sp)
    sw      $t8, 60($sp)
    sw      $t9, 64($sp)
    sw      $ra, 68($sp)
    mfhi    $k0
    sw      $k0, 72($sp)
    mflo    $k0
    jal     irq_dispatch
    sw      $k0, 76($sp)
    lw      $k0, 76($sp)
    mtlo    $k0
    lw      $k0, 72($sp)
    mthi    $k0
    lw      $at, 0($sp)
    lw      $v0, 4($sp)
    lw      $v1, 8($sp)
    lw      $a0, 12($sp)
    lw      $a1, 16($sp)
    lw      $a2, 20($sp)
    lw      $a3, 24($sp)
    lw      $t0, 28($sp)
    lw      $t1, 32($sp)
    lw      $t2, 36($sp)
    lw      $t3, 40($sp)
    lw      $t4, 44($sp)
    lw      $t5, 48($sp)
    lw      $t6, 52($sp)
    lw      $t7, 56($sp)
    lw      $t8, 60($sp)
    lw      $t9, 64($sp)
    lw      $ra, 68($sp)
    addiu   $sp, $sp, 80
    eret
    .size exc_entry, . - exc_entry

    .section .rodata.copy_table, "a"
    .p2align 2
    .type copy_table, @object
copy_table:
    .word __data_start, __data_load, __data_size
    .word __ramfunc_start, __ramfunc_load, __ramfunc_size
    .word __bss_start, 0, __bss_size
    .word 0, 0, 0
    .size copy_table, . - copy_table
