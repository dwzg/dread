/* dread cclab: what a probe build without a C library needs (an adapter's support = true): the
   memory and string functions and the AEABI memory helpers that compilers call. Volatile accesses,
   so that no compiler turns a loop of them into a call of itself. Builds that link the toolchain's
   own library do not get this file. */
#include <stddef.h>

void *memcpy(void *d, const void *s, size_t n)
{
    volatile unsigned char *dd = d;
    const volatile unsigned char *ss = s;
    while (n--)
        *dd++ = *ss++;
    return d;
}

void *memmove(void *d, const void *s, size_t n)
{
    volatile unsigned char *dd = d;
    const volatile unsigned char *ss = s;
    if (dd < ss) {
        while (n--)
            *dd++ = *ss++;
    } else {
        while (n--)
            dd[n] = ss[n];
    }
    return d;
}

void *memset(void *d, int c, size_t n)
{
    volatile unsigned char *dd = d;
    while (n--)
        *dd++ = (unsigned char)c;
    return d;
}

int memcmp(const void *a, const void *b, size_t n)
{
    const volatile unsigned char *x = a, *y = b;
    for (; n; n--, x++, y++)
        if (*x != *y)
            return *x < *y ? -1 : 1;
    return 0;
}

size_t strlen(const char *s)
{
    const volatile char *p = s;
    while (*p)
        p++;
    return (size_t)(p - s);
}

int strcmp(const char *a, const char *b)
{
    const volatile unsigned char *x = (const volatile unsigned char *)a, *y = (const volatile unsigned char *)b;
    while (*x && *x == *y) {
        x++;
        y++;
    }
    return *x - *y;
}

void __aeabi_memcpy(void *d, const void *s, size_t n) { memcpy(d, s, n); }
void __aeabi_memcpy4(void *d, const void *s, size_t n) { memcpy(d, s, n); }
void __aeabi_memcpy8(void *d, const void *s, size_t n) { memcpy(d, s, n); }
void __aeabi_memmove(void *d, const void *s, size_t n) { memmove(d, s, n); }
void __aeabi_memmove4(void *d, const void *s, size_t n) { memmove(d, s, n); }
void __aeabi_memmove8(void *d, const void *s, size_t n) { memmove(d, s, n); }
void __aeabi_memset(void *d, size_t n, int c) { memset(d, c, n); }
void __aeabi_memset4(void *d, size_t n, int c) { memset(d, c, n); }
void __aeabi_memset8(void *d, size_t n, int c) { memset(d, c, n); }
void __aeabi_memclr(void *d, size_t n) { memset(d, 0, n); }
void __aeabi_memclr4(void *d, size_t n) { memset(d, 0, n); }
void __aeabi_memclr8(void *d, size_t n) { memset(d, 0, n); }
