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

/* A bound call (dread.lift: a call whose callee reads registers its caller leaves, or behind which the
 * caller reads what the callee leaves beyond a C result) in one statement, as readable C writes it
 * (dread.bound): the registers named are handed in, the call is made, and they are taken back. ARM with
 * GCC (the C is built with -ffixed for the registers it reads as left), up to sixteen registers:
 *     BOUND(f, IO(r0, a), IO(r1, b), IN(r2, 5), INOUT(r3, c + 1, d));
 * IO(r, v): v handed in r, and r back into v; IN(r, e): e handed in r, what the call leaves there unread;
 * INOUT(r, e, v): e handed in r, r back into v. BOUND clobbers r12, BOUND_R12 binds it instead (r12 among
 * the registers named). */
#if defined(__arm__) || defined(__thumb__)
#define IO(r, v) (IO, r, v, v)
#define IN(r, e) (IN, r, e, _)
#define INOUT(r, e, v) (INOUT, r, e, v)
#define DREAD_BIND_(k, r, e, v) register u32 dread_##r##_ __asm__(#r) = (u32)(e);
#define DREAD_OPND_(k, r, e, v) "+r"(dread_##r##_)
#define DREAD_BACK_(k, r, e, v) DREAD_BACK_##k(r, v)
#define DREAD_BACK_IO(r, v) (v) = dread_##r##_;
#define DREAD_BACK_INOUT(r, v) (v) = dread_##r##_;
#define DREAD_BACK_IN(r, v)
#define DREAD_BIND(p) DREAD_BIND_ p
#define DREAD_OPND(p) DREAD_OPND_ p
#define DREAD_BACK(p) DREAD_BACK_ p
#define DREAD_CAT(a, b) DREAD_CAT_(a, b)
#define DREAD_CAT_(a, b) a##b
#define DREAD_N_(_1, _2, _3, _4, _5, _6, _7, _8, _9, _10, _11, _12, _13, _14, _15, _16, N, ...) N
#define DREAD_N(...) DREAD_N_(__VA_ARGS__, 16, 15, 14, 13, 12, 11, 10, 9, 8, 7, 6, 5, 4, 3, 2, 1)
#define DREAD_EACH_1(m, a) m(a)
#define DREAD_EACH_2(m, a, ...) m(a) DREAD_EACH_1(m, __VA_ARGS__)
#define DREAD_EACH_3(m, a, ...) m(a) DREAD_EACH_2(m, __VA_ARGS__)
#define DREAD_EACH_4(m, a, ...) m(a) DREAD_EACH_3(m, __VA_ARGS__)
#define DREAD_EACH_5(m, a, ...) m(a) DREAD_EACH_4(m, __VA_ARGS__)
#define DREAD_EACH_6(m, a, ...) m(a) DREAD_EACH_5(m, __VA_ARGS__)
#define DREAD_EACH_7(m, a, ...) m(a) DREAD_EACH_6(m, __VA_ARGS__)
#define DREAD_EACH_8(m, a, ...) m(a) DREAD_EACH_7(m, __VA_ARGS__)
#define DREAD_EACH_9(m, a, ...) m(a) DREAD_EACH_8(m, __VA_ARGS__)
#define DREAD_EACH_10(m, a, ...) m(a) DREAD_EACH_9(m, __VA_ARGS__)
#define DREAD_EACH_11(m, a, ...) m(a) DREAD_EACH_10(m, __VA_ARGS__)
#define DREAD_EACH_12(m, a, ...) m(a) DREAD_EACH_11(m, __VA_ARGS__)
#define DREAD_EACH_13(m, a, ...) m(a) DREAD_EACH_12(m, __VA_ARGS__)
#define DREAD_EACH_14(m, a, ...) m(a) DREAD_EACH_13(m, __VA_ARGS__)
#define DREAD_EACH_15(m, a, ...) m(a) DREAD_EACH_14(m, __VA_ARGS__)
#define DREAD_EACH_16(m, a, ...) m(a) DREAD_EACH_15(m, __VA_ARGS__)
#define DREAD_LIST_1(m, a) m(a)
#define DREAD_LIST_2(m, a, ...) m(a), DREAD_LIST_1(m, __VA_ARGS__)
#define DREAD_LIST_3(m, a, ...) m(a), DREAD_LIST_2(m, __VA_ARGS__)
#define DREAD_LIST_4(m, a, ...) m(a), DREAD_LIST_3(m, __VA_ARGS__)
#define DREAD_LIST_5(m, a, ...) m(a), DREAD_LIST_4(m, __VA_ARGS__)
#define DREAD_LIST_6(m, a, ...) m(a), DREAD_LIST_5(m, __VA_ARGS__)
#define DREAD_LIST_7(m, a, ...) m(a), DREAD_LIST_6(m, __VA_ARGS__)
#define DREAD_LIST_8(m, a, ...) m(a), DREAD_LIST_7(m, __VA_ARGS__)
#define DREAD_LIST_9(m, a, ...) m(a), DREAD_LIST_8(m, __VA_ARGS__)
#define DREAD_LIST_10(m, a, ...) m(a), DREAD_LIST_9(m, __VA_ARGS__)
#define DREAD_LIST_11(m, a, ...) m(a), DREAD_LIST_10(m, __VA_ARGS__)
#define DREAD_LIST_12(m, a, ...) m(a), DREAD_LIST_11(m, __VA_ARGS__)
#define DREAD_LIST_13(m, a, ...) m(a), DREAD_LIST_12(m, __VA_ARGS__)
#define DREAD_LIST_14(m, a, ...) m(a), DREAD_LIST_13(m, __VA_ARGS__)
#define DREAD_LIST_15(m, a, ...) m(a), DREAD_LIST_14(m, __VA_ARGS__)
#define DREAD_LIST_16(m, a, ...) m(a), DREAD_LIST_15(m, __VA_ARGS__)
#define DREAD_EACH(m, ...) DREAD_CAT(DREAD_EACH_, DREAD_N(__VA_ARGS__))(m, __VA_ARGS__)
#define DREAD_LIST(m, ...) DREAD_CAT(DREAD_LIST_, DREAD_N(__VA_ARGS__))(m, __VA_ARGS__)
#define BOUND(f, ...) do { DREAD_EACH(DREAD_BIND, __VA_ARGS__) \
    __asm__ volatile ("bl " #f : DREAD_LIST(DREAD_OPND, __VA_ARGS__) : : "lr", "cc", "memory", "r12"); \
    DREAD_EACH(DREAD_BACK, __VA_ARGS__) } while (0)
#define BOUND_R12(f, ...) do { DREAD_EACH(DREAD_BIND, __VA_ARGS__) \
    __asm__ volatile ("bl " #f : DREAD_LIST(DREAD_OPND, __VA_ARGS__) : : "lr", "cc", "memory"); \
    DREAD_EACH(DREAD_BACK, __VA_ARGS__) } while (0)
#endif

#endif
