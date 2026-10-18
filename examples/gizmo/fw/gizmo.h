/* Gizmo 1000: a small firmware written for DREAD's tests. Not a real device.
 *
 * Built twice from the same sources: for an ARM7-style core in ARM state (GIZMO_A32, image at
 * 0x20000, RAM at 0x40000000, peripherals at 0x80000000, an interrupt controller of its own) and
 * for a Cortex-M3 in Thumb-2 (GIZMO_M3, flash at 0x08000000, RAM at 0x20000000, peripherals at
 * 0x40000000, the NVIC). The peripherals have the same registers on both.
 */
#ifndef GIZMO_H
#define GIZMO_H

typedef unsigned char u8;
typedef unsigned short u16;
typedef unsigned int u32;
typedef signed int s32;
typedef unsigned long long u64;

#ifdef GIZMO_M3
#define UART_BASE  0x40011000u
#define TIMER_BASE 0x40000400u
#define GPIO_BASE  0x40010800u
#define NVIC_ISER0 (*(volatile u32 *)0xE000E100u)
#else
#define UART_BASE  0x80000000u
#define TIMER_BASE 0x80001000u
#define INTC_BASE  0x80002000u
#define GPIO_BASE  0x80003000u
#define INTC_PENDING (*(volatile u32 *)(INTC_BASE + 0x0))
#define INTC_ENABLE  (*(volatile u32 *)(INTC_BASE + 0x4))
#define INTC_ACK     (*(volatile u32 *)(INTC_BASE + 0x8))
#endif

#define REG(a) (*(volatile u32 *)(a))
#define UART_DATA   REG(UART_BASE + 0x0)
#define UART_STATUS REG(UART_BASE + 0x4)    /* bit 0: can send, bit 1: a byte arrived */
#define UART_CTRL   REG(UART_BASE + 0x8)    /* bit 0: on */
#define TIMER_COUNT REG(TIMER_BASE + 0x0)
#define TIMER_LOAD  REG(TIMER_BASE + 0x4)
#define TIMER_CTRL  REG(TIMER_BASE + 0x8)   /* bit 0: run, bit 1: interrupt when it expires */
#define TIMER_STAT  REG(TIMER_BASE + 0xC)   /* bit 0: expired; writing 1 clears it */
#define GPIO_IN     REG(GPIO_BASE + 0x0)    /* bits 0-3: keys */
#define GPIO_OUT    REG(GPIO_BASE + 0x4)    /* bit 1: LED, bit 2: beeper */

#define IRQ_TIMER 0

struct config {
    u32 baud;
    u32 volume;
    u32 mode;
    char name[16];
};

struct ring {
    u8 buf[32];
    u32 head;
    u32 tail;
};

extern struct config g_config;
extern volatile u32 g_ticks;
extern struct ring g_rx;

/* lib.c */
void *memcpy(void *dst, const void *src, u32 n);
void *memset(void *dst, int c, u32 n);
int memcmp(const void *a, const void *b, u32 n);
u32 strlen(const char *s);
int strcmp(const char *a, const char *b);
char *strncpy(char *dst, const char *src, u32 n);

/* util.c */
u32 checksum(const u8 *p, u32 n);
int clamp(int x, int lo, int hi);
int parse_uint(const char *s, u32 *out);
int parse_kv(const char *line, char *key, u32 keymax, u32 *value);
const char *mode_name(u32 mode);
u32 sum6(u32 a, u32 b, u32 c, u32 d, u32 e, u32 f);
u64 mul64(u32 a, u32 b);
u32 udiv_var(u32 a, u32 b);
int fmt_dec(char *out, u32 v);
void fmt_hex(char *out, u32 v, int digits);
void ring_put(struct ring *r, u8 c);
int ring_get(struct ring *r);
int dispatch(u32 cmd, u32 arg);
int cmd_led(u32 on);
int cmd_beep(u32 on);
int cmd_mode(u32 mode);
int cmd_volume(u32 level);
void led_on(void);
void led_off(void);
void beep_on(void);
void beep_off(void);

/* hw.c */
void uart_init(void);
void uart_putc(char c);
void uart_puts(const char *s);
int uart_getc(void);
void gpio_set(u32 bit, u32 on);
u32 keys_read(void);
u32 script_run(const u8 *p, u32 n);
void timer_start(u32 ticks);
void delay(u32 ticks);
void timer_irq(void);
void irq_enable_timer(void);
u32 ram_crc(const u8 *p, u32 n);
void irq_dispatch(void);

/* main.c */
int main(void);
void apply_config(const char *text);
void print_status(void);
void handle_key(int c);

#endif
