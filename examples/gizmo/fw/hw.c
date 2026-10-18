/* The peripherals: polled UART, a timer that can interrupt, keys and outputs, and a function the
 * startup copies to RAM. */
#include "gizmo.h"

void uart_init(void)
{
    UART_CTRL = 1;
}

void uart_putc(char c)
{
    while (!(UART_STATUS & 1))
        ;
    UART_DATA = (u8)c;
}

void uart_puts(const char *s)
{
    while (*s)
        uart_putc(*s++);
}

int uart_getc(void)
{
    if (!(UART_STATUS & 2))
        return -1;
    return UART_DATA & 0xFF;
}

__attribute__((noinline)) void gpio_set(u32 bit, u32 on)
{
    if (on)
        GPIO_OUT |= 1u << bit;
    else
        GPIO_OUT &= ~(1u << bit);
}

u32 keys_read(void)
{
    return GPIO_IN & 0xF;
}

void timer_start(u32 ticks)
{
    TIMER_CTRL = 0;
    TIMER_LOAD = ticks;
    TIMER_STAT = 1;
    TIMER_CTRL = 1;
}

void delay(u32 ticks)
{
    timer_start(ticks);
    while (!(TIMER_STAT & 1))
        ;
    TIMER_CTRL = 0;
}

void timer_irq(void)
{
    int c;
    TIMER_STAT = 1;
    g_ticks++;
    c = uart_getc();
    if (c >= 0)
        ring_put(&g_rx, (u8)c);
}

void irq_enable_timer(void)
{
    TIMER_CTRL = 0;
    TIMER_LOAD = 1000;
    TIMER_STAT = 1;
    TIMER_CTRL = 3;
#ifdef GIZMO_M3
    NVIC_ISER0 = 1u << IRQ_TIMER;
#else
    INTC_ENABLE |= 1u << IRQ_TIMER;
#endif
}

/* CRC-32 of a buffer; runs from RAM (the startup copies it there). */
__attribute__((section(".ramfunc"), noinline)) u32 ram_crc(const u8 *p, u32 n)
{
    u32 crc = 0xFFFFFFFFu;
    int k;
    while (n--) {
        crc ^= *p++;
        for (k = 0; k < 8; k++)
            crc = (crc >> 1) ^ (0xEDB88320u & -(crc & 1));
    }
    return ~crc;
}

#ifndef GIZMO_M3
/* The ARM7-style and RV32 builds' interrupt: the entry (start_a32.S, start_rv32.S) calls this for
   every interrupt. */
void irq_dispatch(void)
{
    u32 pending = INTC_PENDING;
    if (pending & (1u << IRQ_TIMER)) {
        timer_irq();
        INTC_ACK = 1u << IRQ_TIMER;
    }
}
#endif
