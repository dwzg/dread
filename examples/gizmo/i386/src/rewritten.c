/* gizmo-i386: the rewritten functions (../../rewritten) with this build's addresses; uart_putc is in a
   file of its own (uart_putc.c: it keeps edx). */
#include "addresses.h"
#define UART_PUTC_ELSEWHERE
#include "../../rewritten/rewritten.c"
