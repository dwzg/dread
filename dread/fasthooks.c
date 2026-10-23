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
 * entries of functions counted here and the masked interrupt's answer compared here, 99 %). With
 * most functions rebuilt, most blocks lie outside shared code instead, in stretches in which the
 * clock stands still (System._stretch): the blocks of a stretch that decide nothing are done here
 * too (sys_stretch). This hook calls the Python hook (System._on_block) for every other block, which
 * keeps the rules in one place; the state both read and write is sys_t. */

typedef void (*block_fn)(void *uc, uint64_t address, uint32_t size, void *user);
typedef void (*io_fn)(void *uc, int type, uint64_t address, int size, int64_t value, void *user);
typedef int (*mem_rw_fn)(void *uc, uint64_t address, void *bytes, uint64_t size);

#define SYS_REGS 64
#define UC_MEM_READ 16              /* Unicorn's uc_mem_type of a load */
#define UC_MEM_WRITE 17             /* and of a store */
#define NO_MODEL 3                  /* sysreg_t's kind and wkind: no model owns the register (memory) */

typedef struct {                    /* a device log entry made here (dread_sysio) */
    uint64_t address, value, count; /* count: the reads it stands for (1 for a write) */
    uint64_t pc;                    /* the instruction that made the access (System.where) */
    int32_t model, size;            /* model: its index in System.models, -1: no model owns it */
    int32_t write;                  /* a write's entry, else a read's */
} sysentry_t;

typedef struct {                    /* a register whose accesses may be done here (System._new_reg) */
    uint64_t address, value;        /* value: what its read gives (VALUE) */
    uint64_t written;               /* the value of the last write System did to it (REPEAT) */
    int64_t epoch;                  /* VALUE and MEMORY hold while this is sys_t's models_epoch */
    int64_t wepoch;                 /* REPEAT holds while this is models_epoch */
    int32_t size, model;
    int32_t kind;                   /* reads: 0 ask Python; 1 VALUE: the read gives value; 2 MEMORY:
                                       the model leaves it to memory; 3 NO_MODEL: memory, always */
    int32_t wkind;                  /* writes: 0 ask Python; 4 REPEAT: the same write again changes
                                       nothing (System._note_write); 3 NO_MODEL: memory, always */
    int32_t used;                   /* read since System last refreshed the entries */
} sysreg_t;

typedef struct {
    int64_t clock, progress, spin_check, next_event, limit, spin;  /* next_event INT64_MAX: none */
    int64_t chunk;                  /* blocks the run's current chunk may still enter */
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
    /* the stretch outside shared code (System._stretch, _loops) */
    int64_t stretch_from, loop_max; /* sysrun's FROZEN and LOOP_MAX */
    int64_t anchor_next;            /* the stretch's block count at which the anchor moves on */
    int64_t waited;                 /* the block count at which the stretch was found a wait */
    uint64_t anchor, resume;        /* + 1: the anchor's block, the block a stop came in front of; 0: none */
    int32_t waiting;                /* the stretch is a wait */
    int32_t nregs;                  /* registers the loop search compares; -1: more than this hook holds */
    int32_t regs[SYS_REGS];
    uint32_t anchor_vals[SYS_REGS]; /* their values at the anchor */
    /* the device accesses (dread_sysio, System._io) */
    io_fn io_python;                /* System._io_from_c: every access not done here */
    reg_rw_fn reg_write;
    mem_rw_fn mem_read, mem_write;
    int64_t access;                 /* sysrun's ACCESS: the clock units an access takes */
    int32_t settle, status_reg;     /* clear a stale Thumb IT state behind every access (settle()) */
    uint32_t it_mask;
    int32_t io_done;                /* accesses were done here since System last took them (_take_log) */
    int32_t tail_read;              /* the log's last entry (System.log, then log) is a read: */
    int32_t tail_model, tail_size;
    uint64_t tail_address, tail_value;
    uint64_t tail_extra;            /* reads done here that repeat System.log's last entry */
    int64_t last_new;               /* the clock at the last new entry made here; -1: none since taken */
    int64_t last_access;            /* the clock at the last device access (System.last_access) */
    sysentry_t *log;                /* the log entries made here, not yet in System.log */
    uint64_t log_n, log_cap;
    set_t io_index;                 /* address << 4 | size -> 1 + its entry in io_regs */
    sysreg_t *io_regs;
    uint64_t io_regs_n, io_regs_cap;
    int64_t models_epoch;           /* moves on after every call into the models that may change one */
    uint64_t *io_used;              /* the entries read since System last refreshed them, each once */
    uint64_t io_used_n;
    int64_t io_fast;                /* accesses this hook did alone */
    int64_t writes_fast;            /* the writes among them */
    /* the transfers into functions (System._ran): where the last block that ran ends, or a mark
       (System's CAME_*), the transfers counted here, and the entries an exception came in front of */
    uint64_t came;
    int32_t vector;                 /* the block an exception enters next: no entry (1) or a lone branch of a
                                       vector made of instructions (2), it hands the mark on (sys_ran) */
    int32_t sp_reg, pc_reg;         /* the stack pointer's and the program counter's register ids */
    set_t calls;                    /* came << 32 | entry -> the transfers counted here */
    set_t suspended;                /* entry << 32 | sp -> came then; 0: gone (System._suspend) */
    int64_t suspended_n;            /* those not gone */
    uint64_t io_pc;                 /* the pc of the access handed to System._io_from_c */
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

/* A block that runs (System._ran): where it is a function's entry, the entry counted and the transfer
 * into it, from where the last block that ran ends (or the mark an exception, a system call or the
 * reset left; an entry an exception came in front of keeps what came before the exception, found by
 * its stack pointer); then where it ends. The block an exception enters hands the exception's mark
 * on when it is no entry (a vector that branches on), and also at an entry when System said the
 * exception enters a vector made of instructions that only branches there (vector 2: PASS_ON). Keep
 * the two alike. */
static void sys_ran(void *uc, uint64_t address, uint32_t size, sys_t *st)
{
    if (set_get(&st->entry_set, address)) {
        set_put(&st->entered, address, set_get(&st->entered, address) + 1, 0);
        if (st->vector == 2) {      /* (a slot that only branches on, whatever the tables call it) */
            st->vector = 0;
            return;
        }
        uint64_t came = st->came;
        if (st->suspended_n > 0) {
            uint64_t sp = 0;
            st->reg_read(uc, st->sp_reg, &sp);
            uint64_t key = address << 32 | (sp & 0xffffffffULL);
            uint32_t back = set_get(&st->suspended, key);
            if (back) {
                came = back;
                set_put(&st->suspended, key, 0, 0);
                st->suspended_n--;
            }
        }
        if (address <= 0xffffffffULL && came <= 0xffffffffULL) {
            uint64_t key = came << 32 | address;
            set_put(&st->calls, key, set_get(&st->calls, key) + 1, 0);
        }
    } else if (st->vector) {
        st->vector = 0;
        return;
    }
    st->vector = 0;
    st->came = address + size;
}

/* Whether the block at address is entered with the anchor's registers (System._came_round). */
static int sys_at_anchor(void *uc, uint64_t address, sys_t *st)
{
    if (st->anchor != address + 1)
        return 0;
    for (int i = 0; i < st->nregs; i++) {
        uint64_t v = 0;
        st->reg_read(uc, st->regs[i], &v);
        if ((uint32_t)v != st->anchor_vals[i])
            return 0;
    }
    return 1;
}

/* A known block outside shared code: done here (1) when System._stretch would only count it, else
 * left to it (0). Keep the two alike. */
static int sys_stretch(void *uc, uint64_t address, uint32_t size, sys_t *st)
{
    if (st->resume == address + 1 && st->frozen) {
        st->resume = 0;             /* the block a stop came in front of, run now */
    } else {
        int64_t n = (int64_t)st->frozen + 1;
        if (n == 1) {
            st->waiting = 0;
            st->anchor = st->resume = 0;
            st->anchor_next = st->stretch_from;
        }
        if (!st->waiting) {
            if (n >= st->stretch_from && (st->nregs < 0 || n >= st->loop_max || n >= st->anchor_next
                                          || sys_at_anchor(uc, address, st)))
                return 0;
        } else if (!st->irq_check || !sys_masks_hold(uc, st) || st->nregs < 0 || n - st->waited >= st->loop_max
                   || (n > st->waited && sys_at_anchor(uc, address, st))) {
            return 0;
        }
        st->frozen = (int32_t)n;
    }
    sys_ran(uc, address, size, st);
    st->chunk--;
    st->fast++;
    return 1;
}

/* UC_HOOK_BLOCK */
void dread_sysblock(void *uc, uint64_t address, uint32_t size, sys_t *st)
{
    if (!st->observers && !st->due && st->chunk > 0) {
        uint32_t known = set_get(&st->known, address);
        int shared = known && size < known && sys_shared(st, address);
        if (known && size < known && !shared && sys_stretch(uc, address, size, st))
            return;
        if (shared) {
            int64_t clock = st->clock + size;
            if ((clock - st->progress < st->spin || clock < st->spin_check)
                    && clock < st->next_event && clock < st->limit
                    && (!st->irq_check || sys_masks_hold(uc, st))) {
                sys_ran(uc, address, size, st);
                st->clock = clock;
                st->frozen = 0;
                st->chunk--;
                st->fast++;
                return;
            }
        }
    }
    int stops = st->due || st->chunk <= 0;
    st->python(uc, address, size, NULL);
    if (!stops && !st->observers) { /* the Python hook recorded the block (it stops first, recording
                                       nothing, when a stop is due or the chunk is used up) */
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

/* The transfers counted here, as (came << 32 | entry, count) pairs, and the counts set back to none. */
size_t dread_sys_calls(sys_t *st, uint64_t *keys, uint32_t *vals)
{
    size_t n = 0;
    for (size_t i = 0; i < st->calls.cap; i++)
        if (st->calls.keys[i]) {
            keys[n] = st->calls.keys[i] - 1;
            vals[n] = st->calls.vals[i];
            n++;
        }
    set_clear(&st->calls);
    return n;
}

/* An exception comes in front of the entry `key` >> 32, its stack pointer the key's low half: what
 * came before it, unless one came in front of it already and the entry has not run since. */
void dread_sys_suspend(sys_t *st, uint64_t key, uint32_t came)
{
    if (!came || set_get(&st->suspended, key))
        return;
    set_put(&st->suspended, key, came, 0);
    st->suspended_n++;
}

/* What came before the exception that came in front of the entry `key` (as dread_sys_suspend's), now
 * gone; 0: none. */
uint32_t dread_sys_resume(sys_t *st, uint64_t key)
{
    uint32_t back = set_get(&st->suspended, key);
    if (back) {
        set_put(&st->suspended, key, 0, 0);
        st->suspended_n--;
    }
    return back;
}

/* ---- the system run's device accesses (System._io) ----
 *
 * A device access moves the clock by ACCESS units and goes into the device log (a read repeated
 * with nothing between is one entry, counted). A read of a register whose read changes nothing and
 * whose value changes only by a call into the models (a model's plain ranges, dread.models.Model)
 * or that no model owns (memory) is done here, from the value System last asked the model for
 * (System._plain, _refresh_plain). So is a write to a register no model owns, and a write that
 * repeats the last one System did, to a register its model calls idempotent, with no call into the
 * models since (System._note_write): it changes nothing but the clock and the log. Every other
 * access goes to System._io_from_c, which keeps the rules. The entries made here wait in `log`
 * until System takes them into its log (System._take_log), before it adds to the log or looks
 * at it; tail_* is the log's last entry, for the counting of repeats. */

/* Room for one more entry (1), or none (0: no memory). */
static int sys_room(sys_t *st)
{
    if (st->log_n < st->log_cap)
        return 1;
    uint64_t cap = st->log_cap ? st->log_cap * 2 : 256;
    sysentry_t *log = realloc(st->log, cap * sizeof(sysentry_t));
    if (!log)
        return 0;
    st->log = log;
    st->log_cap = cap;
    return 1;
}

/* The entry of the register (address, size), or NULL. */
static sysreg_t *sys_reg(sys_t *st, uint64_t address, int size)
{
    uint32_t at = set_get(&st->io_index, address << 4 | (uint64_t)size);
    if (!at || st->io_regs[at - 1].address != address)
        return NULL;                /* (the key holds 60 bits of the address) */
    return &st->io_regs[at - 1];
}

/* A read done here (1), or left to System (0, nothing changed). */
static int sys_read_here(void *uc, uint64_t address, int size, sys_t *st)
{
    if (st->observers || !st->io_regs_n || size < 1 || size > 8 || st->clock < 0 || st->clock >= st->limit
            || st->access < 0 || st->clock > INT64_MAX - st->access)
        return 0;
    sysreg_t *e = sys_reg(st, address, size);
    if (!e || e->kind == 0 || (e->kind != NO_MODEL && e->epoch != st->models_epoch) || !sys_room(st))
        return 0;
    uint8_t bytes[8];
    uint64_t got = 0;
    if (e->kind == 1) {             /* the model's value, put where the load finds it */
        got = e->value;
        for (int i = 0; i < size; i++)
            bytes[i] = (uint8_t)(got >> (8 * i));
        if (st->mem_write(uc, address, bytes, (uint64_t)size))
            return 0;
    } else {
        if (st->mem_read(uc, address, bytes, (uint64_t)size))
            return 0;
        for (int i = 0; i < size; i++)
            got |= (uint64_t)bytes[i] << (8 * i);
    }
    st->clock += st->access;
    st->frozen = 0;                 /* the clock moved: a stretch outside shared code ends */
    st->progress = st->last_access = st->clock;
    if (st->tail_read && st->tail_model == e->model && st->tail_address == address && st->tail_size == size
            && st->tail_value == got) {
        if (st->log_n)
            st->log[st->log_n - 1].count++;
        else
            st->tail_extra++;
    } else {
        sysentry_t *r = &st->log[st->log_n++];
        uint64_t pc = 0;
        st->reg_read(uc, st->pc_reg, &pc);
        r->address = address;
        r->value = got;
        r->count = 1;
        r->pc = pc;
        r->model = e->model;
        r->size = size;
        r->write = 0;
        st->tail_read = 1;
        st->tail_model = e->model;
        st->tail_size = size;
        st->tail_address = address;
        st->tail_value = got;
        st->last_new = st->clock;
    }
    st->io_done = 1;
    st->io_fast++;
    if (!e->used) {
        e->used = 1;
        st->io_used[st->io_used_n++] = (uint64_t)(e - st->io_regs);
    }
    if (st->clock >= st->next_event || st->clock >= st->limit)
        st->due = 1;
    return 1;
}

/* A write done here (1), or left to System (0, nothing changed): one to a register no model owns,
 * or one that repeats the last write System did (the same register, size and value) to a register
 * its model calls idempotent, with no call into the models since (wepoch). Such a write changes
 * nothing in the models (dread.models.Model.idempotent), so nothing the models answer changes either:
 * no plain value to ask again, no next event to schedule, and no interrupt question to raise (what
 * the controllers want did not change, and the masks are compared as before: System.interrupts_changed
 * is not called). Only the clock and the log move, as System._io moves them. */
static int sys_write_here(void *uc, uint64_t address, int size, int64_t value, sys_t *st)
{
    if (st->observers || !st->io_regs_n || size < 1 || size > 8 || st->clock < 0 || st->clock >= st->limit
            || st->access < 0 || st->clock > INT64_MAX - st->access)
        return 0;
    sysreg_t *e = sys_reg(st, address, size);
    uint64_t v = size < 8 ? (uint64_t)value & ((1ULL << (8 * size)) - 1) : (uint64_t)value;
    if (!e || e->wkind == 0 || (e->wkind != NO_MODEL && (e->wepoch != st->models_epoch || e->written != v))
            || !sys_room(st))
        return 0;
    st->clock += st->access;
    st->frozen = 0;                 /* the clock moved: a stretch outside shared code ends */
    st->progress = st->last_access = st->clock;
    sysentry_t *r = &st->log[st->log_n++];
    uint64_t pc = 0;
    st->reg_read(uc, st->pc_reg, &pc);
    r->address = address;
    r->value = v;
    r->count = 1;
    r->pc = pc;
    r->model = e->model;
    r->size = size;
    r->write = 1;
    st->tail_read = 0;              /* a read behind it is a new entry */
    st->last_new = st->clock;
    st->io_done = 1;
    st->io_fast++;
    st->writes_fast++;
    if (st->clock >= st->next_event || st->clock >= st->limit)
        st->due = 1;
    return 1;
}

/* UC_HOOK_MEM_READ | UC_HOOK_MEM_WRITE over the peripheral ranges */
void dread_sysio(void *uc, int type, uint64_t address, int size, int64_t value, sys_t *st)
{
    int done = type == UC_MEM_READ ? sys_read_here(uc, address, size, st)
             : type == UC_MEM_WRITE ? sys_write_here(uc, address, size, value, st) : 0;
    if (!done) {
        st->reg_read(uc, st->pc_reg, &st->io_pc);
        st->io_python(uc, type, address, size, value, NULL);
    }
    if (st->settle) {               /* as System._on_io's settle_it, on both ways */
        uint32_t v = 0;
        st->reg_read(uc, st->status_reg, &v);
        if (v & st->it_mask) {
            v &= ~st->it_mask;
            st->reg_write(uc, st->status_reg, &v);
        }
    }
}

/* The entry of the register (address, size) of `model`, made when there is none (kind and wkind 0):
 * its index, or -1 when there is no memory for it. */
int64_t dread_sys_reg(sys_t *st, uint64_t address, int32_t size, int32_t model)
{
    uint64_t key = address << 4 | (uint64_t)size;
    uint32_t at = set_get(&st->io_index, key);
    if (at)                         /* (another address with the same 60 low bits: none) */
        return st->io_regs[at - 1].address == address ? (int64_t)at - 1 : -1;
    if (st->io_regs_n == st->io_regs_cap) {
        uint64_t cap = st->io_regs_cap ? st->io_regs_cap * 2 : 64;
        sysreg_t *regs = realloc(st->io_regs, cap * sizeof(sysreg_t));
        if (!regs)
            return -1;
        st->io_regs = regs;
        uint64_t *used = realloc(st->io_used, cap * sizeof(uint64_t));
        if (!used)
            return -1;
        st->io_used = used;
        st->io_regs_cap = cap;
    }
    if (!set_put(&st->io_index, key, (uint32_t)(st->io_regs_n + 1), 1))
        return -1;
    sysreg_t *e = &st->io_regs[st->io_regs_n];
    memset(e, 0, sizeof *e);
    e->address = address;
    e->size = size;
    e->model = model;
    e->epoch = e->wepoch = -1;
    return (int64_t)st->io_regs_n++;
}

static void set_free(set_t *s)
{
    free(s->keys);
    free(s->vals);
    free(s->order);
    memset(s, 0, sizeof *s);
}

/* What the system run's state holds in memory of its own, freed (when the run is gone). */
void dread_sys_free(sys_t *st)
{
    set_free(&st->known);
    set_free(&st->entry_set);
    set_free(&st->entered);
    set_free(&st->calls);
    set_free(&st->suspended);
    set_free(&st->io_index);
    free(st->log);
    free(st->io_regs);
    free(st->io_used);
    st->log = NULL;
    st->io_regs = NULL;
    st->io_used = NULL;
    st->log_n = st->log_cap = st->io_regs_n = st->io_regs_cap = st->io_used_n = 0;
}
