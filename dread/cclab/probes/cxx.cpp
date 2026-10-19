/* dread cclab: C++ (the Itanium C++ ABI, and the ARM C++ ABI built on it). The lab calls these by
   their mangled names; each answers one question: where 'this' arrives and a member function's
   arguments behind it; whether a constructor returns 'this' (the ARM C++ ABI says it does); how a
   class that cannot be copied bit by bit comes back (through memory, however small); how a virtual
   call is made (the object passes through cxx_opaque in another file, so that no compiler can
   devirtualise it); what new and delete call. Built without exceptions and RTTI. */

struct S {
    int a, b;
    const S *self() const;
    int third(int x, int y) const;
};

const S *S::self() const { return this; }
int S::third(int x, int y) const { (void)x; return y; }

struct NT {
    int a;
    NT();
    NT(const NT &o);
    ~NT();
};

NT::NT() : a(5) {}
NT::NT(const NT &o) : a(o.a) {}
NT::~NT() {}

NT make_nt() { return NT(); }

int cxx_seven();

struct NT2 {
    int a;
    NT2();
};

NT2::NT2() : a(cxx_seven()) {}      /* the call takes the first argument register: afterwards it
                                       holds 'this' again only where the ABI says so (ARM's) */

struct V {
    int pad;
    virtual int f(int x) const;
};

struct W : V {
    int f(int x) const;
};

int V::f(int x) const { return x + 1; }
int W::f(int x) const { return x * 3; }

const V *cxx_opaque(const V *v);

int cxx_virtual(int x)
{
    W w;
    return cxx_opaque(&w)->f(x);
}

int cxx_new_delete(int x)
{
    NT *p = new NT();
    int a = p->a + x;
    delete p;
    return a;
}
