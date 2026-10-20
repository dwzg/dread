/* gizmo-m3cpp: Rect::area() const of the C++ part (../../fw/shapes.cpp) rewritten in C. A method is
   a function whose first argument is the object (`this`); C names it by its mangled name, which
   the linker matches with the original's. */
typedef unsigned int u32;

struct rect {
    const void *vtable;             /* the class's vtable pointer, first, as C++ lays a class out */
    u32 w, h;
};

u32 _ZNK4Rect4areaEv(const struct rect *self);

u32 _ZNK4Rect4areaEv(const struct rect *self)
{
    return self->w * self->h;
}
