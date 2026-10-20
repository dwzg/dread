/* dread cclab: what objects relinked here (adapters.relink_adapter) may name of a C++ runtime that a
   probe build never uses at run time: the vtables of the C++ ABI's type_info classes, which a
   compiler refers to from a class's type_info even with RTTI off. Weak: a C++ runtime the linker
   finds wins. */
__attribute__((weak)) const void *_ZTVN10__cxxabiv117__class_type_infoE[4];
__attribute__((weak)) const void *_ZTVN10__cxxabiv120__si_class_type_infoE[4];
