/* Gizmo, MSP430 build: the multiply and divide helpers of the MSP430 EABI that clang calls (the base
 * instruction set has neither a multiplier nor a divider). Written with shifts by one and additions,
 * which clang compiles inline, so that no helper calls itself. Division by zero gives all ones. */
#include "gizmo.h"

u32 __mspabi_mpyl(u32 a, u32 b)
{
    u32 r = 0;
    while (b) {
        if (b & 1)
            r += a;
        a <<= 1;
        b >>= 1;
    }
    return r;
}

u64 __mspabi_mpyll(u64 a, u64 b)
{
    u64 r = 0;
    while (b) {
        if (b & 1)
            r += a;
        a <<= 1;
        b >>= 1;
    }
    return r;
}

u32 __mspabi_divul(u32 n, u32 d)
{
    u32 q = 0, r = 0;
    int i;
    for (i = 0; i < 32; i++) {
        r = r << 1 | n >> 31;
        n <<= 1;
        q <<= 1;
        if (r >= d) {
            r -= d;
            q |= 1;
        }
    }
    return q;
}
