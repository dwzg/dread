/* Gizmo's functions written again by hand, the way a reconstruction writes them: the behaviour of the
 * original machine code, from other source. `dread recon` links them over the original image; each
 * target directory has a src/rewritten.c that includes this file after its addresses.h. */
#include <dread.h>

struct ring {
    u8 buf[32];
    u32 head;
    u32 tail;
};

int clamp(int x, int lo, int hi)
{
    return x < lo ? lo : x > hi ? hi : x;
}

ALWAYS_INLINE u32 rotl1(u32 s)
{
    return s << 1 | s >> 31;
}

/* Unrolled: larger than the original, so the overlay has to move it. */
u32 checksum(const u8 *p, u32 n)
{
    u32 s = 0;
    while (n >= 4) {
        s = rotl1(s) ^ p[0];
        s = rotl1(s) ^ p[1];
        s = rotl1(s) ^ p[2];
        s = rotl1(s) ^ p[3];
        p += 4;
        n -= 4;
    }
    while (n--)
        s = rotl1(s) ^ *p++;
    return s;
}

ALWAYS_INLINE int digit(char c, int hex)
{
    if (c >= '0' && c <= '9')
        return c - '0';
    if (hex && c >= 'a' && c <= 'f')
        return c - 'a' + 10;
    if (hex && c >= 'A' && c <= 'F')
        return c - 'A' + 10;
    return -1;
}

int parse_uint(const char *s, u32 *out)
{
    int hex = s[0] == '0' && (s[1] == 'x' || s[1] == 'X');
    int i = hex ? 2 : 0, d;
    u32 v = 0;
    while ((d = digit(s[i], hex)) >= 0) {
        v = v * (hex ? 16 : 10) + d;
        i++;
    }
    if (i == (hex ? 2 : 0))
        return -1;
    *out = v;
    return i;
}

const char *mode_name(u32 mode)
{
    if (mode == 0)
        return STR_OFF;
    if (mode == 1)
        return STR_IDLE;
    if (mode == 2)
        return STR_RUN;
    if (mode == 3)
        return STR_TEST;
    if (mode == 7)
        return STR_SERVICE;
    return STR_UNKNOWN;
}

u32 sum6(u32 a, u32 b, u32 c, u32 d, u32 e, u32 f)
{
    return a + (b << 1) + c * 3 + (d << 2) + e * 5 + f * 6;
}

u64 mul64(u32 a, u32 b)
{
    return (u64)a * b;
}

#ifndef HAVE_UDIV
u32 original_udiv(u32 a, u32 b) ORIGINAL(__udivsi3);
#endif

u32 udiv_var(u32 a, u32 b)
{
    if (b == 0)
        return 0xFFFFFFFFu;
#ifdef HAVE_UDIV
    return a / b;
#else
    return original_udiv(a, b);
#endif
}

void fmt_hex(char *out, u32 v, int digits)
{
    /* The digits from the original's table, one a turn from the last: the way its machine code works,
     * so that each build leaves the registers as its original does (the cases compare what a void
     * function leaves in the result register: GCC's i386 build its counter, clang's the count) and
     * keeps v where it does (in a register: a C that shifted a copy of v had GCC -Os shift it in v's
     * stack slot, in the caller's frame). */
    int i;
    for (i = digits - 1; i >= 0; i--) {
        out[i] = HEX_DIGITS[v & 15];
        v >>= 4;
    }
    out[digits] = 0;
}

void ring_put(struct ring *r, u8 c)
{
    u32 head = r->head, next = (head + 1) % 32;
    if (next == r->tail)
        return;
    r->buf[head] = c;
    r->head = next;
}

int ring_get(struct ring *r)
{
    u32 tail = r->tail;
    int c;
    if (tail == r->head)
        return -1;
    c = r->buf[tail];
    r->tail = (tail + 1) % 32;
    return c;
}

#ifdef INLINED_COMMANDS
/* The rv32 build's compiler (clang) turned the table of handlers into branches and inlined the
   handlers, clamp included: the same calls and stores as that code, without a table. */
void gpio_set(u32 pin, u32 on);

int dispatch(u32 cmd, u32 arg)
{
    int level;
    switch (cmd) {
    case 0:
        gpio_set(1, arg);
        return 0;
    case 1:
        gpio_set(2, arg);
        return 0;
    case 2:
        if (arg > 7)
            return -1;
        REG32(G_CONFIG + 8) = arg;          /* g_config.mode */
        return 0;
    case 3:
        level = (int)arg < 0 ? 0 : (int)arg > 9 ? 9 : (int)arg;
        REG32(G_CONFIG + 4) = level;        /* g_config.volume */
        return level;
    }
    return -1;
}
#else
typedef int (*command)(u32);

int dispatch(u32 cmd, u32 arg)
{
    if (cmd > 3)
        return -1;
    return ((const command *)COMMANDS)[cmd](arg);
}
#endif

#ifndef HWREG
#define HWREG REG32                 /* the peripherals' registers (16 bits wide on gizmo-msp430) */
#endif

void uart_putc(char c)
{
    while ((HWREG(UART_BASE + 4) & 1) == 0)
        ;
    HWREG(UART_BASE) = (u8)c;
}

int strcmp(const char *a, const char *b)
{
    const u8 *x = (const u8 *)a, *y = (const u8 *)b;
    while (*x == *y && *x) {
        x++;
        y++;
    }
    return *x - *y;
}
