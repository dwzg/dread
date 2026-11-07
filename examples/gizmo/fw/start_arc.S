/* Gizmo, ARC600 build (ARCompact, little-endian): the interrupt vector table at address 0 (the
   interrupt vector base's reset value), the reset entry, the startup's copy table, the level-1
   interrupt entry. Every interrupt line (vectors 3-31) is taken as level 1: it enters through
   ilink1 and returns with j.f [ilink1], which restores STATUS32 from STATUS32_L1 (ARC600 has no
   rtie; arc-elf32-as refuses it for -mcpu=arc600). That the lines are level 1 out of reset
   (AUX_IRQ_LEV) is the ARC600 documentation's default as far as we read it, not confirmed on a core. */
    .file   "start_arc.S"           /* else the symbol table names the preprocessor's temporary file,
                                       a new name on every build */
    .section .vectors, "ax", @progbits
__vectors:                          /* 8 bytes each: j with a long immediate (a local label: the
                                       table is the target's vectors, not a function) */
    j       @_start                 /* 0: reset */
    j       @exc_halt               /* 1: memory error */
    j       @exc_halt               /* 2: instruction error */
    .rept   29
    j       @irq_entry              /* 3-31: the interrupt lines (the interrupt controller's on all) */
    .endr

    .section .text.start, "ax", @progbits
    .global _start
    .type _start, @function
_start:
    mov     sp, @__stack_top
    mov     gp, @__SDATA_BEGIN__    /* GCC addresses small data (-G 4: .sdata, .sbss) from gp */
    mov     r3, @copy_table
    mov     r5, 0
1:  ld      r0, [r3, 0]             /* destination */
    ld      r1, [r3, 4]             /* source (0: zero the range) */
    ld      r2, [r3, 8]             /* length in bytes (a multiple of 4) */
    add     r3, r3, 12
    breq    r2, 0, 4f
    breq    r1, 0, 3f
2:  ld.ab   r4, [r1, 4]
    st.ab   r4, [r0, 4]
    sub.f   r2, r2, 4
    bgt     2b
    b       1b
3:  st.ab   r5, [r0, 4]
    sub.f   r2, r2, 4
    bgt     3b
    b       1b
4:  flag    2                       /* STATUS32.E1: level-1 interrupts on (E2 stays off, H 0) */
    bl      @main
5:  b       5b
    .size _start, . - _start

    .text
    .global exc_halt
    .type exc_halt, @function
exc_halt:                           /* a memory or instruction error: stop here (they would return
                                       through ilink2, which nothing here does) */
    b       exc_halt
    .size exc_halt, . - exc_halt

    .global irq_entry
    .type irq_entry, @function
irq_entry:                          /* the core put the return address into ilink1 and STATUS32 into
                                       STATUS32_L1, and cleared E1 (as the ARC600 documentation
                                       reads to us; not confirmed on a core) */
    sub     sp, sp, 56
    st      r0, [sp, 0]
    st      r1, [sp, 4]
    st      r2, [sp, 8]
    st      r3, [sp, 12]
    st      r4, [sp, 16]
    st      r5, [sp, 20]
    st      r6, [sp, 24]
    st      r7, [sp, 28]
    st      r8, [sp, 32]
    st      r9, [sp, 36]
    st      r10, [sp, 40]
    st      r11, [sp, 44]
    st      r12, [sp, 48]
    st      blink, [sp, 52]
    bl      @irq_dispatch           /* (nothing on its path uses a zero-overhead loop or lp_count:
                                       they are not saved; libgcc's __udivmodsi4 does, outside it) */
    ld      blink, [sp, 52]
    ld      r12, [sp, 48]
    ld      r11, [sp, 44]
    ld      r10, [sp, 40]
    ld      r9, [sp, 36]
    ld      r8, [sp, 32]
    ld      r7, [sp, 28]
    ld      r6, [sp, 24]
    ld      r5, [sp, 20]
    ld      r4, [sp, 16]
    ld      r3, [sp, 12]
    ld      r2, [sp, 8]
    ld      r1, [sp, 4]
    ld      r0, [sp, 0]
    add     sp, sp, 56
    j.f     [ilink1]
    .size irq_entry, . - irq_entry

    .section .rodata.copy_table, "a"
    .p2align 2
    .type copy_table, @object
copy_table:
    .word   __data_start, __data_load, __data_size
    .word   __ramfunc_start, __ramfunc_load, __ramfunc_size
    .word   __bss_start, 0, __bss_size
    .word   0, 0, 0
    .size copy_table, . - copy_table
