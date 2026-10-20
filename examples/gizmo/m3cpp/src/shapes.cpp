// gizmo-m3cpp: a method and a template of the C++ part (../../fw/shapes.cpp) rewritten in C++.
//
// The class is described by its layout, the vtable pointer first, without virtual methods: a
// virtual method defined outside its class makes the compiler emit the class's vtable in this
// file, and the image keeps its own vtables (in its data, pointing at the methods' entries, where
// the overlay leaves a branch when a method moves). The mangled name does not say whether a method
// is virtual, so Tri::area here is the original's _ZNK3Tri4areaEv.
typedef unsigned int u32;

class Tri {
public:
    u32 area() const;
private:
    const void *vtable_;
    u32 base_, height_;
};

u32 Tri::area() const
{
    return base_ * height_ / 2;
}

// One template for both instances the original has (a family: dread functions --families)
template <typename T> T bound(T v, T lo, T hi)
{
    return v < lo ? lo : v > hi ? hi : v;
}

template unsigned int bound<unsigned int>(unsigned int, unsigned int, unsigned int);
template int bound<int>(int, int, int);
