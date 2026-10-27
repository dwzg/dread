/* written by examples/gizmo/fw/addresses.py from gizmo.elf; do not edit */
#define UART_BASE 0x00000100u
#define GPIO_BASE 0x00000130u
#define COMMANDS 0x000094e6u
#define STR_OFF ((const char *)0x000094aa)
#define STR_IDLE ((const char *)0x000094ae)
#define STR_RUN ((const char *)0x000094b3)
#define STR_TEST ((const char *)0x000094b7)
#define STR_UNKNOWN ((const char *)0x000094a8)
#define STR_SERVICE ((const char *)0x000094bc)
#define HEX_DIGITS ((const char *)0x000094d4)
#define HAVE_UDIV 1
#define HWREG(a) (*(volatile u16 *)(a))
