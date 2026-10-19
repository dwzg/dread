/* dread cclab: floating-point arguments and results. With soft float (or no FPU) they travel in the
   core registers like integers of their size; with AAPCS's VFP variant (hard float) in s0-s15,
   doubles in even pairs (d0-d7), a float filling a single left free by a double ("back-filling"),
   a struct of up to four floats or doubles in consecutive VFP registers, and a variadic argument
   always in the core registers. ret_f and ret_d tell the lab where a build returns them. */
#include <stdarg.h>

struct f3 { float a, b, c; };

float ret_f(void) { return 1.5f; }
double ret_d(void) { return 1.5; }

float pick_f_1(float a) { return a; }
float pick_f_3(float a, float b, float c) { (void)a; (void)b; return c; }
float pick_f_17(float a1, float a2, float a3, float a4, float a5, float a6, float a7, float a8,
                float a9, float a10, float a11, float a12, float a13, float a14, float a15,
                float a16, float a17)
{
    (void)a1; (void)a2; (void)a3; (void)a4; (void)a5; (void)a6; (void)a7; (void)a8;
    (void)a9; (void)a10; (void)a11; (void)a12; (void)a13; (void)a14; (void)a15; (void)a16;
    return a17;
}
float pick_if_f(int i, float f) { (void)i; return f; }
float pick_i5_f(int a, int b, int c, int d, int e, float f)
{
    (void)a; (void)b; (void)c; (void)d; (void)e;
    return f;
}
double pick_d_2(double a, double b) { (void)a; return b; }
double pick_fd_d(float a, double b) { (void)a; return b; }
float pick_dfdf_f(double a, float b, double c, float d) { (void)a; (void)b; (void)c; return d; }
float pick_hfa_c(struct f3 s) { return s.c; }

double pick_va_d(int n, ...)
{
    va_list ap;
    double d;
    va_start(ap, n);
    d = va_arg(ap, double);
    va_end(ap);
    return d;
}
