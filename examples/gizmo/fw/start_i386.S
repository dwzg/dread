/* Gizmo, i386 build: the reset entry and the startup's copy table (flat 32-bit protected mode, the
   segments as the loader left them; no interrupts yet: the timer's line is not wired on this build). */
    .section .text.start, "ax"
    .globl _start
    .type _start, @function
_start:
    movl    $__stack_top, %esp
    movl    $copy_table, %ebx
1:  movl    0(%ebx), %edi           /* destination */
    movl    4(%ebx), %esi           /* source (0: zero the range) */
    movl    8(%ebx), %ecx           /* length in bytes */
    addl    $12, %ebx
    testl   %ecx, %ecx
    jz      4f
    testl   %esi, %esi
    jz      3f
2:  movl    (%esi), %eax
    movl    %eax, (%edi)
    addl    $4, %esi
    addl    $4, %edi
    subl    $4, %ecx
    jg      2b
    jmp     1b
3:  movl    $0, (%edi)
    addl    $4, %edi
    subl    $4, %ecx
    jg      3b
    jmp     1b
4:  call    main
5:  hlt
    jmp     5b
    .size _start, . - _start

    .section .rodata.copy_table, "a"
    .p2align 2
    .type copy_table, @object
copy_table:
    .long __data_start, __data_load, __data_size
    .long __ramfunc_start, __ramfunc_load, __ramfunc_size
    .long __bss_start, 0, __bss_size
    .long 0, 0, 0
    .size copy_table, . - copy_table
