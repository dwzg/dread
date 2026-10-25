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
 *     BOUND(f, R0(a), R1(b), R2_IN(5), R5_IN_OUT(c + 1, d));
 * Rn(v): v handed in rn, and rn taken back into v; Rn_IN(e): e handed in rn, what the call leaves there
 * unread; Rn_IN_OUT(e, v): e handed in rn, rn taken back into v. The register is in the macro's name, not
 * among its arguments, so that renaming a variable r0 renames no register. BOUND clobbers r12, BOUND_R12
 * binds it instead (R12 among the registers named). */
#if defined(__arm__) || defined(__thumb__)
#define R0(v) (IO, r0, v, v)
#define R0_IN(e) (IN, r0, e, _)
#define R0_IN_OUT(e, v) (INOUT, r0, e, v)
#define R1(v) (IO, r1, v, v)
#define R1_IN(e) (IN, r1, e, _)
#define R1_IN_OUT(e, v) (INOUT, r1, e, v)
#define R2(v) (IO, r2, v, v)
#define R2_IN(e) (IN, r2, e, _)
#define R2_IN_OUT(e, v) (INOUT, r2, e, v)
#define R3(v) (IO, r3, v, v)
#define R3_IN(e) (IN, r3, e, _)
#define R3_IN_OUT(e, v) (INOUT, r3, e, v)
#define R4(v) (IO, r4, v, v)
#define R4_IN(e) (IN, r4, e, _)
#define R4_IN_OUT(e, v) (INOUT, r4, e, v)
#define R5(v) (IO, r5, v, v)
#define R5_IN(e) (IN, r5, e, _)
#define R5_IN_OUT(e, v) (INOUT, r5, e, v)
#define R6(v) (IO, r6, v, v)
#define R6_IN(e) (IN, r6, e, _)
#define R6_IN_OUT(e, v) (INOUT, r6, e, v)
#define R7(v) (IO, r7, v, v)
#define R7_IN(e) (IN, r7, e, _)
#define R7_IN_OUT(e, v) (INOUT, r7, e, v)
#define R8(v) (IO, r8, v, v)
#define R8_IN(e) (IN, r8, e, _)
#define R8_IN_OUT(e, v) (INOUT, r8, e, v)
#define R9(v) (IO, r9, v, v)
#define R9_IN(e) (IN, r9, e, _)
#define R9_IN_OUT(e, v) (INOUT, r9, e, v)
#define R10(v) (IO, r10, v, v)
#define R10_IN(e) (IN, r10, e, _)
#define R10_IN_OUT(e, v) (INOUT, r10, e, v)
#define R11(v) (IO, r11, v, v)
#define R11_IN(e) (IN, r11, e, _)
#define R11_IN_OUT(e, v) (INOUT, r11, e, v)
#define R12(v) (IO, r12, v, v)
#define R12_IN(e) (IN, r12, e, _)
#define R12_IN_OUT(e, v) (INOUT, r12, e, v)
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
