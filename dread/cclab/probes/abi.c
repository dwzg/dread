/* dread cclab probes: the calling convention and the basic types. Every function is external, so
   that nothing inlines it away, and returns one of its arguments or a known value: the lab runs it
   with a different marker in every register and stack word an argument can be in, and the marker
   that comes back says where that argument arrives (abi.toml says which each function shows). */
#include <stdarg.h>
#include <stddef.h>

typedef long long i64;
struct s1 { char a; };
struct s4 { int a; };
struct s8 { int a, b; };
struct s12 { int a, b, c; };
struct s20 { int a, b, c, d, e; };

int pick_i5_1(int a, int b, int c, int d, int e) { return a; }
int pick_i5_2(int a, int b, int c, int d, int e) { return b; }
int pick_i5_4(int a, int b, int c, int d, int e) { return d; }
int pick_i5_5(int a, int b, int c, int d, int e) { return e; }
int pick_i8_8(int a, int b, int c, int d, int e, int f, int g, int h) { return h; }

i64 pick_ll_1(i64 a) { return a; }
i64 pick_i_ll(int a, i64 b) { return b; }
int pick_i_ll_i(int a, i64 b, int c) { return c; }
i64 pick_iii_ll(int a, int b, int c, i64 d) { return d; }
int pick_iii_ll_i(int a, int b, int c, i64 d, int e) { return e; }

int pick_s1(struct s1 s) { return s.a; }
int pick_s8_b(struct s8 s) { return s.b; }
int pick_s12_c(struct s12 s) { return s.c; }
int pick_s12_after(struct s12 s, int x) { return x; }
int pick_ii_s12_c(int a, int b, struct s12 s) { return s.c; }
int pick_s20_e(struct s20 s) { return s.e; }

int pick_va_3(int n, ...)
{
    va_list ap;
    int i, v = 0;
    va_start(ap, n);
    for (i = 0; i < 3; i++)
        v = va_arg(ap, int);
    va_end(ap);
    return v;
}

int pick_va_5(int n, ...)
{
    va_list ap;
    int i, v = 0;
    va_start(ap, n);
    for (i = 0; i < 5; i++)
        v = va_arg(ap, int);
    va_end(ap);
    return v;
}

i64 pick_va_ll(int n, ...)
{
    va_list ap;
    i64 v;
    va_start(ap, n);
    v = va_arg(ap, i64);
    va_end(ap);
    return v;
}

int ret_i(void) { return 0x11223344; }
i64 ret_ll(void) { return 0x1122334455667788LL; }
struct s1 ret_s1(void) { struct s1 r = { 0x5a }; return r; }
struct s4 ret_s4(void) { struct s4 r = { 0x11223344 }; return r; }
struct s8 ret_s8(void) { struct s8 r = { 0x11223344, 0x55667788 }; return r; }
struct s12 ret_s12(void) { struct s12 r = { 0x11223344, 0x55667788, 0x0a0b0c0d }; return r; }

char ret_char(void) { return (char)0x88; }
signed char ret_schar(void) { return (signed char)0x88; }
short ret_short(void) { return (short)0x8899; }
unsigned short ret_ushort(void) { return (unsigned short)0x8899; }

enum small { SMALL_A, SMALL_B };
struct with_ll { char c; i64 x; };
struct with_double { char c; double x; };

int size_long(void) { return (int)sizeof(long); }
int size_long_double(void) { return (int)sizeof(long double); }
int size_double(void) { return (int)sizeof(double); }
int size_enum_small(void) { return (int)sizeof(enum small); }
int size_wchar(void) { return (int)sizeof(L'x'); }
int size_bool(void) { return (int)sizeof(_Bool); }
int align_ll(void) { return (int)offsetof(struct with_ll, x); }
int align_double(void) { return (int)offsetof(struct with_double, x); }
int char_is_signed(void) { return (char)-1 < 0; }

unsigned bitfields(void)
{
    union { struct { unsigned a : 3, b : 5; } s; unsigned u; } x;
    x.u = 0;
    x.s.b = 1;
    return x.u;
}
