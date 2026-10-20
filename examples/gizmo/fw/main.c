/* Start-up of the application, the configuration text and the main loop. */
#include "gizmo.h"

struct config g_config = { 115200, 5, 1, "gizmo" };
volatile u32 g_ticks;
struct ring g_rx;

static const char default_config[] = "volume = 7\nmode=2\n  baud = 0x2580\nname=3\n";

void apply_config(const char *text)
{
    char line[40], key[12];
    u32 value;
    while (*text) {
        u32 n = 0;
        while (*text && *text != '\n') {
            if (n + 1 < sizeof line)
                line[n++] = *text;
            text++;
        }
        line[n] = 0;
        if (*text == '\n')
            text++;
        if (parse_kv(line, key, sizeof key, &value) != 0)
            continue;
        if (!strcmp(key, "volume"))
            g_config.volume = clamp(value, 0, 9);
        else if (!strcmp(key, "mode"))
            cmd_mode(value);
        else if (!strcmp(key, "baud"))
            g_config.baud = value;
    }
}

/* Reads one line through uart_getc into buf (at most max - 1 characters, without the line end;
 * carriage returns are dropped) and returns its length, or -1 when the UART has nothing before the
 * line ends. */
int read_line(char *buf, u32 max)
{
    u32 n = 0;
    for (;;) {
        int c = uart_getc();
        if (c < 0)
            return -1;
        if (c == '\r')
            continue;
        if (c == '\n')
            break;
        if (n + 1 < max)
            buf[n++] = (char)c;
    }
    buf[n] = 0;
    return (int)n;
}

void print_status(void)
{
    char buf[12];
    uart_puts("mode ");
    uart_puts(mode_name(g_config.mode));
    uart_puts(" volume ");
    fmt_dec(buf, g_config.volume);
    uart_puts(buf);
    uart_puts("\r\n");
}

void handle_key(int c)
{
    switch (c) {
    case 'l':
        led_on();
        break;
    case 'L':
        led_off();
        break;
    case 'b':
        beep_on();
        delay(500);
        beep_off();
        break;
    case 's':
        print_status();
        break;
    case 'k':
        uart_putc((char)script_run((const u8 *)&g_config, sizeof g_config));
        break;
    case 'c': {
        char line[40];
        if (read_line(line, sizeof line) > 0)
            apply_config(line);
        break;
    }
#ifdef GIZMO_M4
    case 'f': {
        char buf[12];
        fmt_dec(buf, (u32)signal_step(g_config.volume));
        uart_puts(buf);
        uart_puts("\r\n");
        break;
    }
#endif
    default:
        if (c >= '0' && c <= '9')
            dispatch(3, c - '0');
        break;
    }
}

int main(void)
{
    char buf[12];
    u32 last;
    uart_init();
    uart_puts("GIZMO 1000\r\n");
    apply_config(default_config);
    print_status();
#ifdef GIZMO_CPP
    fmt_dec(buf, shapes_demo(sizeof g_config + 9));
    uart_puts("shapes ");
    uart_puts(buf);
    uart_puts("\r\n");
#endif
    fmt_hex(buf, ram_crc((const u8 *)&g_config, sizeof g_config), 8);
    uart_puts("crc ");
    uart_puts(buf);
    uart_puts("\r\n");
    irq_enable_timer();
    uart_puts("READY\r\n");
    last = g_ticks;
    for (;;) {
        int c = ring_get(&g_rx);
        if (c >= 0) {
            handle_key(c);
            continue;
        }
        while (g_ticks == last)
            ;
        last = g_ticks;
    }
}
