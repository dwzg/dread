/* Hot emulator hooks in C, called by Unicorn without Python in between (dread/fasthooks.py).
 *
 * One state per emulator records what the Python hooks record: the blocks executed in a range
 * (coverage) and the starts of blocks outside it (what callees ran), the loads outside the stack and
 * the image (first size per address) and whether an instruction was loaded as data, the stores
 * outside the stack (address and size), and the pages any store touched (for the reset). On cores
 * with Thumb IT blocks every hook ends by clearing a stale IT state, as dread.arch.arm.settle_it
 * does. Results grow in open-addressing hash sets; Python copies them out after a run. */
#include <stdint.h>
#include <stdlib.h>
#include <string.h>

typedef int (*reg_rw_fn)(void *uc, int regid, void *value);
typedef int (*emu_stop_fn)(void *uc);

typedef struct {
    uint64_t *keys;     /* 0 = empty; stored keys are key + 1 */
    uint32_t *vals;
    uint64_t *order;    /* with ordered: the keys in the order they were first put */
    size_t cap, used, order_cap;
    int64_t ordered;
} set_t;

typedef struct {
    /* configuration */
    uint64_t cover_lo, cover_hi;
    uint64_t stack_lo, stack_hi;
    uint64_t image_lo, image_hi;
    const uint8_t *code_bits;       /* one bit per halfword of the image: an instruction starts there */
    int32_t tree, settle, status_reg;
    uint32_t it_mask;
    reg_rw_fn reg_read, reg_write;
    /* results */
    set_t blocks, tree_set, reads, writes, pages;
    int32_t code_reads;
    uint64_t last_block;            /* address << 16 | size of the last block run inside the range */
    const uint16_t *owners;         /* per halfword of the image: 1 + the function whose extent holds it */
    uint64_t owners_n;
    int32_t pc_reg;
    int64_t budget;                 /* bytes of code a run may still enter (dread_budget) */
    int32_t budget_hit;             /* the budget ran out and the run was stopped */
    emu_stop_fn emu_stop;
    int64_t stall;                  /* bytes a run may enter without a block it had not entered; 0: no limit */
    int64_t stall_left;
    int32_t stalled;                /* the run was stopped for that (budget_hit is set too) */
    set_t seen;                     /* the blocks this run entered (with stall) */
} state_t;

static uint64_t mix(uint64_t x)
{
    x ^= x >> 33; x *= 0xff51afd7ed558ccdULL; x ^= x >> 33; x *= 0xc4ceb9fe1a85ec53ULL; x ^= x >> 33;
    return x;
}

static int set_grow(set_t *s);

/* Insert key with value; returns 1 when the key was new. With keep_first an existing value stays. */
static int set_put(set_t *s, uint64_t key, uint32_t val, int keep_first)
{
    if ((s->used + 1) * 2 > s->cap && set_grow(s) != 0)
        return 0;
    uint64_t k = key + 1;
    size_t mask = s->cap - 1, i = mix(k) & mask;
    while (s->keys[i]) {
        if (s->keys[i] == k) {
            if (!keep_first)
                s->vals[i] = val;
            return 0;
        }
        i = (i + 1) & mask;
    }
    s->keys[i] = k;
    s->vals[i] = val;
    if (s->ordered) {
        if (s->used == s->order_cap) {
            size_t cap = s->order_cap ? s->order_cap * 2 : 1024;
            uint64_t *order = realloc(s->order, cap * sizeof(uint64_t));
            if (!order)
                return 1;
            s->order = order;
            s->order_cap = cap;
        }
        s->order[s->used] = key;
    }
    s->used++;
    return 1;
}

static uint32_t set_get(set_t *s, uint64_t key)
{
    if (!s->cap)                    /* nothing put yet: no table */
        return 0;
    uint64_t k = key + 1;
    size_t mask = s->cap - 1, i = mix(k) & mask;
    while (s->keys[i]) {
        if (s->keys[i] == k)
            return s->vals[i];
        i = (i + 1) & mask;
    }
    return 0;
}

static int set_grow(set_t *s)
{
    size_t cap = s->cap ? s->cap * 2 : 1024;
    set_t n = { calloc(cap, sizeof(uint64_t)), calloc(cap, sizeof(uint32_t)), NULL, cap, 0, 0, 0 };
    if (!n.keys || !n.vals) {
        free(n.keys);
        free(n.vals);
        return -1;
    }
    for (size_t i = 0; i < s->cap; i++)
        if (s->keys[i])
            set_put(&n, s->keys[i] - 1, s->vals[i], 1);
    free(s->keys);
    free(s->vals);
    s->keys = n.keys;                       /* used, order and ordered stay */
    s->vals = n.vals;
    s->cap = cap;
    return 0;
}

static void set_clear(set_t *s)
{
    if (s->cap) {
        memset(s->keys, 0, s->cap * sizeof(uint64_t));
        memset(s->vals, 0, s->cap * sizeof(uint32_t));
    }
    s->used = 0;
}

static set_t *pick(state_t *st, int which)
{
    return which == 0 ? &st->blocks : which == 1 ? &st->tree_set : which == 2 ? &st->reads
         : which == 3 ? &st->writes : which == 5 ? &st->seen : &st->pages;
}

/* Copy the keys (and values) of a set into out arrays of at least s->used entries. */
size_t dread_copy(state_t *st, int which, uint64_t *keys, uint32_t *vals)
{
    set_t *s = pick(st, which);
    size_t n = 0;
    if (s->ordered && s->order) {
        for (; n < s->used; n++) {
            keys[n] = s->order[n];
            if (vals)
                vals[n] = set_get(s, s->order[n]);
        }
        return n;
    }
    for (size_t i = 0; i < s->cap; i++)
        if (s->keys[i]) {
            keys[n] = s->keys[i] - 1;
            if (vals)
                vals[n] = s->vals[i];
            n++;
        }
    return n;
}

size_t dread_count(state_t *st, int which)
{
    set_t *s = pick(st, which);
    return s->used;
}

void dread_clear(state_t *st, int which)
{
    set_t *s = pick(st, which);
    set_clear(s);
    if (which == 2)
        st->code_reads = 0;
}

static void settle(void *uc, state_t *st)
{
    if (!st->settle)
        return;
    uint32_t v = 0;
    st->reg_read(uc, st->status_reg, &v);
    if (v & st->it_mask) {
        v &= ~st->it_mask;
        st->reg_write(uc, st->status_reg, &v);
    }
}

/* UC_HOOK_BLOCK: void (uc, address, size, user) */
void dread_block(void *uc, uint64_t address, uint32_t size, state_t *st)
{
    if (address >= st->cover_lo && address < st->cover_hi) {
        set_put(&st->blocks, address << 16 | (size & 0xFFFF), 0, 1);
        st->last_block = address << 16 | (size & 0xFFFF);
    } else if (st->tree)
        set_put(&st->tree_set, address, 0, 1);
    settle(uc, st);
}

/* UC_HOOK_BLOCK over all code: a run's limit, counted in bytes of the blocks it enters. Unicorn's
 * own instruction count puts a hook call in front of every instruction: runs went six times slower
 * with it. Deterministic, unlike a time limit; a run may overshoot by the block that crosses it. */
void dread_budget(void *uc, uint64_t address, uint32_t size, state_t *st)
{
    st->budget -= size;
    /* With stall: a run that enters no block it had not entered for that many bytes circles in code
     * it knows (a loop over a list that points at itself, a count of billions) and stops as at the
     * limit, which it would mostly run into. */
    if (st->stall > 0 && !st->budget_hit) {
        if (set_put(&st->seen, address, 0, 1))
            st->stall_left = st->stall;
        else if ((st->stall_left -= size) <= 0) {
            st->stalled = 1;
            st->budget_hit = 1;
            st->emu_stop(uc);
            settle(uc, st);
            return;
        }
    }
    if (st->budget <= 0 && !st->budget_hit) {
        st->budget_hit = 1;
        st->emu_stop(uc);
        settle(uc, st);     /* only a hook that calls into the emulator leaves a stale IT state */
    }
}

/* UC_HOOK_MEM_READ: bool (uc, type, address, size, value, user) */
int dread_read(void *uc, int type, uint64_t address, int size, int64_t value, state_t *st)
{
    (void)type;
    (void)value;
    if (address >= st->image_lo && address < st->image_hi) {
        int code = 0;
        if (st->code_bits) {
            for (uint64_t a = address & ~1ULL; a < address + (uint64_t)size; a += 2) {
                uint64_t h = (a - st->image_lo) >> 1;
                if (a < st->image_hi && (st->code_bits[h >> 3] >> (h & 7) & 1)) {
                    st->code_reads = 1;
                    code = 1;
                    break;
                }
            }
        }
        if (!code && st->owners) {      /* a function's extent, loaded by another function's code */
            uint64_t h = (address - st->image_lo) >> 1;
            uint16_t owner = h < st->owners_n ? st->owners[h] : 0;
            if (owner) {
                uint64_t pc = 0;
                st->reg_read(uc, st->pc_reg, &pc);
                pc &= ~1ULL;
                uint64_t ph = (pc - st->image_lo) >> 1;
                uint16_t mine = (pc >= st->image_lo && ph < st->owners_n) ? st->owners[ph] : 0;
                if (mine != owner)
                    st->code_reads = 1;
            }
        }
    } else if (!(address >= st->stack_lo && address < st->stack_hi)) {
        set_put(&st->reads, address, (uint32_t)size, 1);
    }
    settle(uc, st);
    return 1;
}

/* UC_HOOK_MEM_WRITE: bool (uc, type, address, size, value, user) */
int dread_write(void *uc, int type, uint64_t address, int size, int64_t value, state_t *st)
{
    (void)type;
    (void)value;
    set_put(&st->pages, address >> 12, 0, 1);
    set_put(&st->pages, (address + (uint64_t)size - 1) >> 12, 0, 1);
    if (!(address >= st->stack_lo && address < st->stack_hi))
        set_put(&st->writes, address << 8 | ((uint64_t)size & 0xFF), 0, 1);
    settle(uc, st);
    return 1;
}

static int by_start(const void *a, const void *b)
{
    uint64_t x = *(const uint64_t *)a >> 8, y = *(const uint64_t *)b >> 8;
    return x < y ? -1 : x > y;
}

/* The stores as merged ranges [lo, hi), sorted: lo and hi need room for as many entries as there
 * are stores (dread_count(st, 3)). Returns the number of ranges. */
size_t dread_ranges(state_t *st, uint64_t *lo, uint64_t *hi)
{
    size_t n = dread_copy(st, 3, lo, NULL), out = 0;
    qsort(lo, n, sizeof(uint64_t), by_start);
    for (size_t i = 0; i < n; i++) {
        uint64_t a = lo[i] >> 8, b = a + (lo[i] & 0xFF);
        if (out && a <= hi[out - 1]) {
            if (b > hi[out - 1])
                hi[out - 1] = b;
        } else {
            lo[out] = a;
            hi[out] = b;
            out++;
        }
    }
    return out;
}

size_t dread_state_size(void)
{
    return sizeof(state_t);
}

/* The loads are reported in the order they first happened, as Python's dict keeps them. */
void dread_init(state_t *st)
{
    st->reads.ordered = 1;
}

/* ---- the system run's block hook (dread.sysrun) ----
 *
 * Nearly every block of a system run is a known block of shared code with nothing due: the clock
 * moves on by its size, and that is all (79 % of a FreeRTOS run's 3.4 million blocks; with the
 * entries of functions counted here and the masked interrupt's answer compared here, 99 %). This
 * hook does those and calls the Python hook (System._on_block) for every other block, which keeps
 * the rules in one place; the state both read and write is sys_t. */

typedef void (*block_fn)(void *uc, uint64_t address, uint32_t size, void *user);

typedef struct {
    int64_t clock, progress, spin_check, next_event, limit, spin;  /* next_event INT64_MAX: none */
    int32_t frozen, due, irq_check, observers;
    int32_t memo;                   /* the last interrupt answer was "masked" and nothing changed since */
    int32_t memo_n;                 /* registers its answer depends on */
    int32_t memo_regs[4];
    uint32_t memo_bits[4];
    uint32_t memo_vals[4];          /* their bits then */
    set_t known;                    /* block address -> its largest size + 1 */
    set_t entry_set;                /* addresses where functions are entered */
    set_t entered;                  /* entry address -> the entries counted here */
    const uint64_t *excl_lo, *excl_hi;  /* code that is not shared, sorted */
    uint64_t excl_n;
    reg_rw_fn reg_read;
    block_fn python;
    int64_t fast;                   /* blocks this hook did alone */
} sys_t;

static int sys_shared(const sys_t *st, uint64_t address)
{
    uint64_t lo = 0, hi = st->excl_n;
    while (lo < hi) {               /* the last range starting at or below address */
        uint64_t mid = (lo + hi) / 2;
        if (st->excl_lo[mid] <= address)
            lo = mid + 1;
        else
            hi = mid;
    }
    return lo == 0 || address >= st->excl_hi[lo - 1];
}

static int sys_masks_hold(void *uc, sys_t *st)
{
    if (!st->memo)
        return 0;
    for (int i = 0; i < st->memo_n; i++) {
        uint64_t v = 0;
        st->reg_read(uc, st->memo_regs[i], &v);
        if (((uint32_t)v & st->memo_bits[i]) != st->memo_vals[i])
            return 0;
    }
    return 1;
}

/* UC_HOOK_BLOCK */
void dread_sysblock(void *uc, uint64_t address, uint32_t size, sys_t *st)
{
    if (!st->observers && !st->due) {
        uint32_t known = set_get(&st->known, address);
        if (known && size < known && sys_shared(st, address)) {
            int64_t clock = st->clock + size;
            if ((clock - st->progress < st->spin || clock < st->spin_check)
                    && clock < st->next_event && clock < st->limit
                    && (!st->irq_check || sys_masks_hold(uc, st))) {
                if (set_get(&st->entry_set, address))
                    set_put(&st->entered, address, set_get(&st->entered, address) + 1, 0);
                st->clock = clock;
                st->frozen = 0;
                st->fast++;
                return;
            }
        }
    }
    int due = st->due;
    st->python(uc, address, size, NULL);
    if (!due && !st->observers) {   /* the Python hook recorded the block (it returns first only when due) */
        uint32_t known = set_get(&st->known, address);
        if (size + 1 > known)
            set_put(&st->known, address, size + 1, 0);
    }
}

size_t dread_sys_size(void)
{
    return sizeof(sys_t);
}

/* The entries counted here, as (address, count) pairs, and the counts set back to none. */
size_t dread_sys_entered(sys_t *st, uint64_t *keys, uint32_t *vals)
{
    size_t n = 0;
    for (size_t i = 0; i < st->entered.cap; i++)
        if (st->entered.keys[i]) {
            keys[n] = st->entered.keys[i] - 1;
            vals[n] = st->entered.vals[i];
            n++;
        }
    set_clear(&st->entered);
    return n;
}

void dread_sys_entry(sys_t *st, uint64_t address)
{
    set_put(&st->entry_set, address, 1, 1);
}
