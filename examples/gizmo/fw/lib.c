/* The few library functions the firmware needs; GCC also calls memcpy and memset itself. */
#include "gizmo.h"

void *memcpy(void *dst, const void *src, u32 n)
{
    u8 *d = dst;
    const u8 *s = src;
    while (n--)
        *d++ = *s++;
    return dst;
}

void *memset(void *dst, int c, u32 n)
{
    u8 *d = dst;
    while (n--)
        *d++ = (u8)c;
    return dst;
}

int memcmp(const void *a, const void *b, u32 n)
{
    const u8 *x = a, *y = b;
    for (; n; n--, x++, y++)
        if (*x != *y)
            return *x - *y;
    return 0;
}

u32 strlen(const char *s)
{
    const char *p = s;
    while (*p)
        p++;
    return p - s;
}

int strcmp(const char *a, const char *b)
{
    while (*a && *a == *b) {
        a++;
        b++;
    }
    return (u8)*a - (u8)*b;
}

char *strncpy(char *dst, const char *src, u32 n)
{
    char *d = dst;
    while (n && *src) {
        *d++ = *src++;
        n--;
    }
    while (n--)
        *d++ = 0;
    return dst;
}
