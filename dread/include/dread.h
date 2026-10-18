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

#endif
