/* DREAD: what rewritten C needs to reach the original image.
 *
 * The C of a rewritten function is linked over the original image (dread overlay). It reaches the
 * original's data by address, calls functions that are still original by their names (the overlay
 * provides them as symbols), and may not add data of its own that the original's startup would have
 * to set up: no zero-initialised and no writable initialised variables. Constants are fine.
 */
#ifndef DREAD_H
#define DREAD_H

typedef unsigned char u8;
typedef signed char s8;
typedef unsigned short u16;
typedef signed short s16;
#if __SIZEOF_INT__ == 2             /* MSP430: int is 16 bits */
typedef unsigned long u32;
typedef signed long s32;
#else
typedef unsigned int u32;
typedef signed int s32;
#endif
typedef unsigned long long u64;
typedef signed long long s64;

/* Memory of the original at a fixed address. VAR may be merged or reordered by the compiler like
 * any variable; VVAR keeps every access, in order (for memory another task, an interrupt or a
 * callee changes behind the compiler's back). */
#define VAR(type, address)  (*(type *)(address))
#define VVAR(type, address) (*(volatile type *)(address))

/* Peripheral registers: every access kept, in order. */
#define REG8(address)  (*(volatile u8 *)(address))
#define REG16(address) (*(volatile u16 *)(address))
#define REG32(address) (*(volatile u32 *)(address))

/* A helper shared by several rewritten functions, inlined into each (the build turns automatic
 * inlining off, as old compilers did not inline). */
#define ALWAYS_INLINE static inline __attribute__((always_inline))

/* A function of the original under another C name or type:
 *     u32 my_name(u32 a) ORIGINAL(name_in_the_table);  */
#define ORIGINAL(name) __asm__(#name)

/* A function of the original at its address, for its address as a value: with
 *     #define name FUNCTION_AT(0x913du)
 * (u32)&name is 0x913d (the address code takes, bit 0 set for Thumb code) wherever the function's own
 * C lands, so the code is the same as with the number. Readable C writes it so (dread.naming). */
#define FUNCTION_AT(address) (*(void (*)(void))(address))

/* A second word that is no result: a function whose callers read the second result register after it
 * where it holds only a constant or what a callee left there (dread.regflow's leftover; readable C prints
 * such a function u32, not u64). GCC and clang, ARM and RISC-V.
 *     return RETURN2(lo, hi);    returns lo and leaves hi in the second result register (r1, a1)
 *     u64 t = CALL2(f, a, b);    calls such a function f for both words: its result low, what it leaves high */
#if defined(__arm__) || defined(__thumb__)
#define DREAD_RESULT_0 "r0"
#define DREAD_RESULT_1 "r1"
#elif defined(__riscv)
#define DREAD_RESULT_0 "a0"
#define DREAD_RESULT_1 "a1"
#endif
#ifdef DREAD_RESULT_1
#define RETURN2(lo, hi) ({ \
    u32 dread_lo_ = (lo), dread_hi_ = (hi); \
    register u32 dread_r0_ __asm__(DREAD_RESULT_0) = dread_lo_; \
    register u32 dread_r1_ __asm__(DREAD_RESULT_1) = dread_hi_; \
    __asm__ volatile ("" : : "r"(dread_r0_), "r"(dread_r1_)); \
    dread_r0_; })
#define CALL2(f, ...) (((u64 (*)())(void (*)(void))(f))(__VA_ARGS__))
#endif

#endif
