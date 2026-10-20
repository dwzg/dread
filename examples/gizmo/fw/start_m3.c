/* Gizmo, Cortex-M3 and Cortex-M4 builds: the vector table and the reset handler (on the M4, which
   enables the FPU first). */
#include "gizmo.h"

extern u32 _estack[];
extern u32 __data_start[], __data_end[], __data_load[];
extern u32 __ramfunc_start[], __ramfunc_end[], __ramfunc_load[];
extern u32 __bss_start[], __bss_end[];

void Reset_Handler(void);
void Default_Handler(void);
void TIMER_IRQHandler(void);

__attribute__((noinline)) static void copy_words(u32 *dst, const u32 *src, u32 *end)
{
    while (dst < end)
        *dst++ = *src++;
}

__attribute__((noinline)) static void zero_words(u32 *dst, u32 *end)
{
    while (dst < end)
        *dst++ = 0;
}

void Reset_Handler(void)
{
#ifdef GIZMO_M4
    *(volatile u32 *)0xE000ED88u |= 0xFu << 20;        /* CPACR: CP10 and CP11, the FPU, full access */
    __asm volatile ("dsb\n\tisb" ::: "memory");
#endif
    copy_words(__data_start, __data_load, __data_end);
    copy_words(__ramfunc_start, __ramfunc_load, __ramfunc_end);
    zero_words(__bss_start, __bss_end);
#ifdef GIZMO_CPP
    {   /* the C++ part's static constructors (m3cpp.ld keeps .init_array) */
        extern void (*__init_array_start[])(void), (*__init_array_end[])(void);
        for (void (**f)(void) = __init_array_start; f < __init_array_end; f++)
            (*f)();
    }
#endif
    main();
    for (;;)
        ;
}

void Default_Handler(void)
{
    for (;;)
        ;
}

void TIMER_IRQHandler(void)
{
    timer_irq();
}

__attribute__((section(".vectors"), used)) void (*const vectors[])(void) = {
    (void (*)(void))_estack,
    Reset_Handler,
    Default_Handler,            /* NMI */
    Default_Handler,            /* HardFault */
    Default_Handler,            /* MemManage */
    Default_Handler,            /* BusFault */
    Default_Handler,            /* UsageFault */
    0, 0, 0, 0,
    Default_Handler,            /* SVCall */
    Default_Handler,            /* DebugMon */
    0,
    Default_Handler,            /* PendSV */
    Default_Handler,            /* SysTick */
    TIMER_IRQHandler,           /* IRQ 0: the timer */
};
