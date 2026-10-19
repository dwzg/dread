/* dread cclab: what C++ probes need from a runtime library, for a build without one (an adapter's
   cxx_support, by default its support): operator new and delete by their mangled names (_Znwj where
   size_t is unsigned int, _Znwm where it is unsigned long), the sized deletes of C++14, and the
   handler a pure virtual call ends in. A pool of the probes' own that is never freed: each of the
   lab's runs starts with zeroed memory. */
static unsigned char cclab_pool[512];
static unsigned cclab_used;

void *_Znwj(unsigned n)
{
    void *p = &cclab_pool[cclab_used];
    cclab_used += (n + 7u) & ~7u;
    return p;
}

void *_Znwm(unsigned long n)
{
    return _Znwj((unsigned)n);
}

void _ZdlPv(void *p)
{
    (void)p;
}

void _ZdlPvj(void *p, unsigned n)
{
    (void)p;
    (void)n;
}

void _ZdlPvm(void *p, unsigned long n)
{
    (void)p;
    (void)n;
}

void __cxa_pure_virtual(void)
{
    for (;;)
        ;
}
