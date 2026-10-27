/* Gizmo 1000: a small firmware written for DREAD's tests. Not a real device.
 *
 * Built four times from the same sources: for an ARM7-style core in ARM state (GIZMO_A32, image
 * at 0x20000, RAM at 0x40000000, peripherals at 0x80000000, an interrupt controller of its own),
 * for a Cortex-M3 in Thumb-2 (GIZMO_M3, flash at 0x08000000, RAM at 0x20000000, peripherals at
 * 0x40000000, the NVIC), for an RV32IMC core in machine mode (GIZMO_RV32, flash at 0x20000000,
 * RAM at 0x80000000, peripherals at 0x10000000, the A32 build's interrupt controller at the machine
 * external interrupt), for an MSP430 (GIZMO_MSP430, 16-bit int: flash at 0x8000, the vectors at
 * 0xffe0, RAM at 0x0200, peripherals at 0x0100 with 16-bit registers, the A32 build's interrupt
 * controller on every vector but reset), and for a little-endian MIPS32 core in kernel mode
 * (GIZMO_MIPS: the boot flash at 0xbfc00000 in kseg1, RAM at 0x80000000 in kseg0, peripherals at
 * 0xbf800000, the A32 build's interrupt controller at hardware interrupt 0), and for a 32-bit x86
 * core in flat protected mode (GIZMO_I386: flash at 0x08000000, RAM at 0x20000000, the RV32 build's
 * peripherals, memory-mapped; their controller at the IDT's vector 0x20). The same registers on
 * all of them. (The M4 and the C++ builds are the M3's and the RV32's with more.)
 */
#ifndef GIZMO_H
#define GIZMO_H

typedef unsigned char u8;
typedef unsigned short u16;
#ifdef GIZMO_MSP430                 /* int is 16 bits there */
typedef unsigned long u32;
typedef signed long s32;
#else
typedef unsigned int u32;
typedef signed int s32;
#endif
typedef unsigned long long u64;
#ifdef GIZMO_MSP430
typedef unsigned short hwreg;       /* the peripherals' registers are 16 bits wide there */
#else
typedef u32 hwreg;
#endif

#ifdef GIZMO_M3
#define UART_BASE  0x40011000u
#define TIMER_BASE 0x40000400u
#define GPIO_BASE  0x40010800u
#define NVIC_ISER0 (*(volatile u32 *)0xE000E100u)
#else
#if defined(GIZMO_RV32) || defined(GIZMO_I386)
#define UART_BASE  0x10000000u
#define TIMER_BASE 0x10001000u
#define INTC_BASE  0x10002000u
#define GPIO_BASE  0x10003000u
#elif defined(GIZMO_MIPS)
#define UART_BASE  0xbf800000u
#define TIMER_BASE 0xbf801000u
#define INTC_BASE  0xbf802000u
#define GPIO_BASE  0xbf803000u
#elif defined(GIZMO_MSP430)
#define UART_BASE  0x0100u
#define TIMER_BASE 0x0110u
#define INTC_BASE  0x0120u
#define GPIO_BASE  0x0130u
#else
#define UART_BASE  0x80000000u
#define TIMER_BASE 0x80001000u
#define INTC_BASE  0x80002000u
#define GPIO_BASE  0x80003000u
#endif
#define INTC_PENDING (*(volatile hwreg *)(INTC_BASE + 0x0))
#define INTC_ENABLE  (*(volatile hwreg *)(INTC_BASE + 0x4))
#define INTC_ACK     (*(volatile hwreg *)(INTC_BASE + 0x8))
#endif

#define REG(a) (*(volatile hwreg *)(a))
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
int read_line(char *buf, u32 max);
void timer_start(u32 ticks);
void delay(u32 ticks);
void timer_irq(void);
void irq_enable_timer(void);
#ifdef GIZMO_MIPS
/* ram_crc runs in RAM (kseg0): jal reaches only the 256 MiB region of the flash (kseg1), and ld.lld
   writes the call into the wrong region without a word; a long call loads the address */
__attribute__((long_call))
#endif
u32 ram_crc(const u8 *p, u32 n);

#ifdef GIZMO_M4
/* dsp.c: the Cortex-M4 build's signal path (floats, the DSP extension) */
float lp_filter(float state, float sample, float alpha);
float average(const float *v, int n);
int to_millivolts(float volts);
s32 mix_sat(s32 a, s32 b);
s32 dot_pairs(s32 a, s32 b, s32 acc);
s32 clamp12(s32 x);
u32 level8(s32 x);
int signal_step(u32 volume);
#endif
void irq_dispatch(void);

/* main.c */
int main(void);
void apply_config(const char *text);
void print_status(void);
void handle_key(int c);
#ifdef GIZMO_CPP
u32 shapes_demo(u32 x);                 /* shapes.cpp, the m3cpp build only */
#endif

#endif
