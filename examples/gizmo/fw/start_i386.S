/* Gizmo, i386 build: the reset entry, the startup's copy table and the interrupt entry (flat 32-bit
   protected mode). The startup loads a flat GDT of its own (iret reloads cs from it), copies .data and
   .ramfunc and zeroes .bss, fills the IDT's gate for the interrupt controller's line (vector 0x20,
   where a PC's remapped PIC starts) and enables interrupts before main, as the RV32 build does. */
    .section .text.start, "ax"
    .globl _start
    .type _start, @function
_start:
    lgdt    gdt_desc
    ljmp    $0x08, $0f              /* cs: the flat code segment */
0:  movl    $0x10, %eax             /* the flat data segment */
    movl    %eax, %ds
    movl    %eax, %es
    movl    %eax, %fs
    movl    %eax, %gs
    movl    %eax, %ss
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
4:  movl    $irq_entry, %eax        /* the interrupt gate of vector 0x20 (.bss, zeroed above) */
    movl    $idt + 8 * 0x20, %edx
    movw    %ax, 0(%edx)            /* offset 15:0 */
    movw    $0x08, 2(%edx)          /* cs */
    movw    $0x8e00, 4(%edx)        /* present, ring 0, 32-bit interrupt gate (clears IF) */
    shrl    $16, %eax
    movw    %ax, 6(%edx)            /* offset 31:16 */
    lidt    idt_desc
    sti
    call    main
5:  hlt
    jmp     5b
    .size _start, . - _start

/* The interrupt controller's line: every register saved, irq_dispatch (hw.c) asks the controller. */
    .text
    .globl irq_entry
    .type irq_entry, @function
irq_entry:
    pushal
    cld
    call    irq_dispatch
    popal
    iret
    .size irq_entry, . - irq_entry

    .section .rodata.gdt, "a"
    .p2align 3
    .type gdt, @object
gdt:                                /* (the accessed bits set: loading a segment writes none into flash) */
    .quad   0
    .quad   0x00cf9b000000ffff      /* 0x08: code, base 0, 4 GiB, 32-bit */
    .quad   0x00cf93000000ffff      /* 0x10: data, base 0, 4 GiB */
    .size gdt, . - gdt
    .type gdt_desc, @object
gdt_desc:
    .word   gdt_desc - gdt - 1
    .long   gdt
    .size gdt_desc, . - gdt_desc
    .type idt_desc, @object
idt_desc:
    .word   8 * 0x21 - 1
    .long   idt
    .size idt_desc, . - idt_desc

    .section .bss.idt, "aw", @nobits
    .p2align 3
    .type idt, @object
idt:
    .space  8 * 0x21
    .size idt, . - idt

    .section .rodata.copy_table, "a"
    .p2align 2
    .type copy_table, @object
copy_table:
    .long __data_start, __data_load, __data_size
    .long __ramfunc_start, __ramfunc_load, __ramfunc_size
    .long __bss_start, 0, __bss_size
    .long 0, 0, 0
    .size copy_table, . - copy_table
