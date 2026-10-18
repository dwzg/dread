/* Gizmo, Cortex-M3 build: the vector table and the reset handler. */
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
    copy_words(__data_start, __data_load, __data_end);
    copy_words(__ramfunc_start, __ramfunc_load, __ramfunc_end);
    zero_words(__bss_start, __bss_end);
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
