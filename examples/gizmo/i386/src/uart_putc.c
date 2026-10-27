/* dread: call-saved edx */
/* gizmo-i386's rewritten uart_putc, apart from the shared ones (rewritten.c leaves it out here). GCC's
   original leaves edx as it came, which uart_puts reads behind the call (GCC's interprocedural register
   allocation), and the byte it wrote in eax, which the cases compare as every function's result.
   -fcall-saved-edx (the line above, dread.build) keeps edx whichever GCC compiles it: GCC 15 compiled
   the shared version without touching edx, GCC 11 and 13 set it; the byte is the result. */
#include "addresses.h"
#include <dread.h>

u32 uart_putc(char c)
{
    u32 v = (u8)c;
    while ((REG32(UART_BASE + 4) & 1) == 0)
        ;
    REG32(UART_BASE) = v;
    return v;
}
