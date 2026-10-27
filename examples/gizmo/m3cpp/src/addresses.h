/* written by examples/gizmo/fw/addresses.py from gizmo.elf; do not edit */
#define UART_BASE 0x40011000u
#define GPIO_BASE 0x40010800u
#define COMMANDS 0x08000994u
#define STR_OFF ((const char *)0x08000902)
#define STR_IDLE ((const char *)0x08000906)
#define STR_RUN ((const char *)0x0800090b)
#define STR_TEST ((const char *)0x0800090f)
#define STR_UNKNOWN ((const char *)0x08000900)
#define STR_SERVICE ((const char *)0x08000914)
#define HEX_DIGITS ((const char *)0x08000980)
#define HAVE_UDIV 1
