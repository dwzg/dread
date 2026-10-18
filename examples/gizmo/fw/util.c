/* Helpers with the shapes the tools have to handle: loops, boundaries, a text parser, a switch,
 * a switch made into a jump table, stack arguments, a 64-bit result, a division by a variable, a
 * table of function pointers and a family of functions that differ only in their constants. */
#include "gizmo.h"

u32 checksum(const u8 *p, u32 n)
{
    u32 s = 0;
    while (n--)
        s = ((s << 1) | (s >> 31)) ^ *p++;
    return s;
}

int clamp(int x, int lo, int hi)
{
    if (x < lo)
        return lo;
    if (x > hi)
        return hi;
    return x;
}

/* Reads a decimal or 0x-prefixed hexadecimal number. Returns the characters read, or -1 when
 * there is no digit. Overflow wraps, as it would in many old firmwares. */
int parse_uint(const char *s, u32 *out)
{
    u32 v = 0;
    int n = 0;
    if (s[0] == '0' && (s[1] == 'x' || s[1] == 'X')) {
        int digits = 0;
        n = 2;
        for (;; n++, digits++) {
            char c = s[n];
            u32 d;
            if (c >= '0' && c <= '9')
                d = c - '0';
            else if (c >= 'a' && c <= 'f')
                d = c - 'a' + 10;
            else if (c >= 'A' && c <= 'F')
                d = c - 'A' + 10;
            else
                break;
            v = v * 16 + d;
        }
        if (!digits)
            return -1;
    } else {
        while (s[n] >= '0' && s[n] <= '9')
            v = v * 10 + (s[n++] - '0');
        if (!n)
            return -1;
    }
    *out = v;
    return n;
}

/* "key = value": the key (letters and '_', at most keymax - 1 of them) and the number.
 * Returns 0, or -1 without a key, -2 without '=', -3 without a number. */
int parse_kv(const char *line, char *key, u32 keymax, u32 *value)
{
    u32 k = 0;
    while (*line == ' ' || *line == '\t')
        line++;
    while ((*line >= 'a' && *line <= 'z') || (*line >= 'A' && *line <= 'Z') || *line == '_') {
        if (k + 1 < keymax)
            key[k++] = *line;
        line++;
    }
    key[k] = 0;
    if (!k)
        return -1;
    while (*line == ' ' || *line == '\t')
        line++;
    if (*line++ != '=')
        return -2;
    while (*line == ' ' || *line == '\t')
        line++;
    if (parse_uint(line, value) < 0)
        return -3;
    return 0;
}

const char *mode_name(u32 mode)
{
    switch (mode) {
    case 0:
        return "off";
    case 1:
        return "idle";
    case 2:
        return "run";
    case 3:
        return "test";
    case 7:
        return "service";
    default:
        return "?";
    }
}

u32 sum6(u32 a, u32 b, u32 c, u32 d, u32 e, u32 f)
{
    return a + 2 * b + 3 * c + 4 * d + 5 * e + 6 * f;
}

u64 mul64(u32 a, u32 b)
{
    return (u64)a * b;
}

u32 udiv_var(u32 a, u32 b)
{
    if (!b)
        return 0xFFFFFFFFu;
    return a / b;
}

int fmt_dec(char *out, u32 v)
{
    char tmp[12];
    int n = 0, i = 0;
    do {
        tmp[n++] = '0' + v % 10;
        v /= 10;
    } while (v);
    while (n)
        out[i++] = tmp[--n];
    out[i] = 0;
    return i;
}

void fmt_hex(char *out, u32 v, int digits)
{
    static const char hex[] = "0123456789abcdef";
    int i;
    for (i = digits - 1; i >= 0; i--) {
        out[i] = hex[v & 15];
        v >>= 4;
    }
    out[digits] = 0;
}

void ring_put(struct ring *r, u8 c)
{
    u32 next = (r->head + 1) & 31;
    if (next != r->tail) {
        r->buf[r->head] = c;
        r->head = next;
    }
}

int ring_get(struct ring *r)
{
    int c;
    if (r->head == r->tail)
        return -1;
    c = r->buf[r->tail];
    r->tail = (r->tail + 1) & 31;
    return c;
}

int cmd_led(u32 on)
{
    gpio_set(1, on);
    return 0;
}

int cmd_beep(u32 on)
{
    gpio_set(2, on);
    return 0;
}

int cmd_mode(u32 mode)
{
    if (mode > 7)
        return -1;
    g_config.mode = mode;
    return 0;
}

int cmd_volume(u32 level)
{
    g_config.volume = clamp(level, 0, 9);
    return g_config.volume;
}

static int (*const commands[])(u32) = { cmd_led, cmd_beep, cmd_mode, cmd_volume };

int dispatch(u32 cmd, u32 arg)
{
    if (cmd >= sizeof commands / sizeof commands[0])
        return -1;
    return commands[cmd](arg);
}

__attribute__((noinline)) void led_on(void) { gpio_set(1, 1); }
__attribute__((noinline)) void led_off(void) { gpio_set(1, 0); }
__attribute__((noinline)) void beep_on(void) { gpio_set(2, 1); }
__attribute__((noinline)) void beep_off(void) { gpio_set(2, 0); }

/* A dense switch after a call: GCC makes a jump table of it (ldr pc, [pc, rN, lsl #2] in ARM state,
 * tbb in Thumb-2). Several cases hold loops whose counters are set up inside the case: a tool that
 * does not follow the table, and enters the loops directly, takes what a case sets up for something
 * that came from before the switch. */
u32 script_run(const u8 *p, u32 n)
{
    u32 acc = 0, i;
    switch (keys_read() & 7) {
    case 0:
        for (i = 0; i < n; i++)
            acc += p[i];
        break;
    case 1:
        for (i = 0; i < n; i++)
            acc ^= (u32)p[i] << (i & 7);
        break;
    case 2:
        acc = n * 3 + 1;
        break;
    case 3:
        acc = checksum(p, n);
        break;
    case 4:
        for (i = n; i; i--)
            acc = acc * 31 + p[i - 1];
        break;
    case 5:
        acc = p[0] | (u32)p[1] << 8;
        break;
    case 6:
        for (i = 0; i < n && p[i]; i++)
            acc++;
        break;
    default:
        acc = ~0u;
        break;
    }
    return acc;
}
