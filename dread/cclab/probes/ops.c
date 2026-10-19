/* dread cclab probes: operations a compiler may compile through a runtime helper, inline, or with
   an idiom of its own (a division by a constant, a switch table, a struct copy, a tail call). The
   lab records what each function calls and the shape of its code, and runs it once on the inputs
   ops.toml gives, recording what every helper it calls reads, returns and changes. */
#include <stddef.h>

typedef long long i64;
typedef unsigned long long u64;
struct s20 { int a, b, c, d, e; };
void *memcpy(void *d, const void *s, size_t n);
void *memset(void *d, int c, size_t n);

unsigned op_udiv(unsigned a, unsigned b) { return a / b; }
int op_sdiv(int a, int b) { return a / b; }
unsigned op_umod(unsigned a, unsigned b) { return a % b; }
int op_smod(int a, int b) { return a % b; }
u64 op_udiv64(u64 a, u64 b) { return a / b; }
i64 op_sdiv64(i64 a, i64 b) { return a / b; }
u64 op_umod64(u64 a, u64 b) { return a % b; }
u64 op_mul64(u64 a, u64 b) { return a * b; }
u64 op_shl64(u64 a, int n) { return a << n; }
u64 op_shr64(u64 a, int n) { return a >> n; }
i64 op_sar64(i64 a, int n) { return a >> n; }
float op_fadd(float a, float b) { return a + b; }
float op_fmul(float a, float b) { return a * b; }
double op_dadd(double a, double b) { return a + b; }
int op_f2i(float a) { return (int)a; }
float op_i2f(int a) { return (float)a; }
double op_f2d(float a) { return a; }
int op_fcmp(float a, float b) { return a < b; }
unsigned op_udiv10(unsigned a) { return a / 10; }
int op_sdiv7(int a) { return a / 7; }
unsigned op_umod10(unsigned a) { return a % 10; }
void op_struct_copy(struct s20 *d, const struct s20 *s) { *d = *s; }
void op_memset64(char *d) { memset(d, 0, 64); }
void op_memcpy_n(char *d, const char *s, size_t n) { memcpy(d, s, n); }
void op_memcpy16(char *d, const char *s) { memcpy(d, s, 16); }
unsigned op_tail(unsigned a) { return op_udiv10(a + 1); }

int op_switch_dense(int x)
{
    switch (x) {
    case 0: return 100;
    case 1: return 101;
    case 2: return 102;
    case 3: return 103;
    case 4: return 104;
    case 5: return 105;
    case 6: return 106;
    case 7: return 107;
    case 8: return 108;
    case 9: return 109;
    default: return -1;
    }
}

int op_switch_sparse(int x)
{
    switch (x) {
    case 1: return 11;
    case 50: return 22;
    case 300: return 33;
    case 4000: return 44;
    case 50000: return 55;
    default: return -1;
    }
}
