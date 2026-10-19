/* dread cclab probes: what a function keeps across a call and how. Each keeps values live across
   calls of op_udiv10 (in ops.c, another file: nothing inlines it), some under register pressure;
   the lab runs them with a marker in every register that a call must keep and reports which ones
   came back changed, and the stack pointer's alignment at each call. */
unsigned op_udiv10(unsigned a);

int keeps_leaf(int a, int b)
{
    return a * b + 1;
}

int keeps_across_call(int a, int b, int c, int d)
{
    int x = (int)op_udiv10((unsigned)a);
    int y = (int)op_udiv10((unsigned)b);
    return x + y + a + b + c + d;
}

int keeps_pressure(int a, int b, int c, int d)
{
    int p = a * 3, q = b * 5, r = c * 7, s = d * 11, t = a ^ b, u = c ^ d, v = a + d, w = b + c;
    int k = (int)op_udiv10((unsigned)(a + b));
    return p + q + r + s + t + u + v + w + k * (p - q) + (r ^ s) * (t | u) + v * w;
}

long long keeps_ll(long long a, long long b)
{
    unsigned k = op_udiv10((unsigned)a);
    return a * b + k;
}
