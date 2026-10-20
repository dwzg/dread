// Gizmo's C++ part, linked into the m3cpp (g++) and rv32cpp (clang++) builds: a class hierarchy
// whose methods are called through vtables, a template instantiated for two types, and a static
// object with a constructor (run by the startup from .init_array). Built with -fno-exceptions
// -fno-rtti, no heap, no library.
extern "C" {
#include "gizmo.h"
}

extern "C" void __cxa_pure_virtual(void)        // a pure virtual method called: never, in this program
{
    for (;;)
        ;
}

class Shape {
public:
    virtual u32 area() const = 0;
    virtual u32 sides() const { return 0; }
};

class Rect : public Shape {
public:
    Rect(u32 w, u32 h) : w_(w), h_(h) {}
    u32 area() const override { return w_ * h_; }
    u32 sides() const override { return 4; }
private:
    u32 w_, h_;
};

class Square : public Rect {
public:
    explicit Square(u32 side) : Rect(side, side) {}
    u32 sides() const override { return 4 + 0 * area(); }
};

class Tri : public Shape {
public:
    Tri(u32 base, u32 height) : base_(base), height_(height) {}
    u32 area() const override { return base_ * height_ / 2; }
    u32 sides() const override { return 3; }
private:
    u32 base_, height_;
};

template <typename T> __attribute__((noinline)) T bound(T v, T lo, T hi)
{
    return v < lo ? lo : v > hi ? hi : v;
}

struct Counter {
    Counter() : count(42 + g_ticks) {}         // (a volatile read: no compiler folds it into .data)
    void bump(u32 by) { count += bound<u32>(by, 1, 10); }
    u32 count;
};

static Counter counter;                         // constructed before main (.init_array)

// Kept out of interprocedural analysis, so that the compiler cannot see which Shape it gets: the
// calls stay calls through the vtable (clang has no noipa; it does not specialise a function per call)
#ifdef __clang__
#define OPAQUE __attribute__((noinline))
#else
#define OPAQUE __attribute__((noinline, noipa))
#endif

OPAQUE static u32 measure(const Shape *s)
{
    return s->area() * 2 + s->sides();
}

extern "C" u32 shapes_demo(u32 x)
{
    Rect r(x & 15, 3);
    Square sq(x & 7);
    Tri t(x & 31, 4);
    const Shape *all[] = {&r, &sq, &t};
    u32 total = 0;
    for (const Shape *s : all)
        total += measure(s);
    counter.bump(x);
    return total + counter.count + (u32)bound<int>((int)x - 100, -5, 5);
}
