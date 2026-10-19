/* dread cclab: an object passed through, in a file of its own, so that cxx.cpp's virtual call
   cannot be devirtualised. */
struct V;

const V *cxx_opaque(const V *v)
{
    return v;
}

int cxx_seven()
{
    return 7;
}
