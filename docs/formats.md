# File formats

The tables, case files and reports that DREAD's tools exchange, and that target repositories keep.
They are interfaces: a change needs a note here on what older files mean, and readers stay
tolerant of older files.

Common to the text tables:

- one entry per line;
- `#` starts a comment;
- addresses are hexadecimal with `0x`;
- unknown extra columns are ignored, so a table may carry notes.

## An image's tables (in its `config` directory)

Each table's key, as `[image.NAME] tables` names it, is in parentheses.

### `functions.tsv` (`functions`)

The functions of the image: tab-separated `address`, `name`, `bytes`, then anything (Ghidra's
export adds the file and the prototype). A first line whose first field is not a number is a
header.

`bytes` counts the function's **instruction bytes** only. Literal pools and other data inside the
function are not counted. The extent of a function is found by walking the code ranges from its
entry until that many instruction bytes are collected. The walk stops at the next function's entry
and gives up 64 KiB behind the entry. `dread.program.Program` is the one implementation of that
walk. Entry plus size is **not** the end of a function with pools inside.

Code the startup copies to RAM is listed at its **run** address.

Written by the Ghidra pipeline (`dread ghidra`) or by `dread import-elf`. Not edited by hand. Two
details of the writers:

- `dread ghidra` names a thunk Ghidra shows under its target's name `thunk_NAME`, so that names stay
  unique.
- `dread import-elf` leaves out an alignment `nop` at the end of a function's last instruction run.
  GCC counts that nop in the symbol's size, but it never executes, and a test that has to execute
  every instruction would never be complete. Ghidra's analysis does not count it either.
- For the same reason `dread import-elf` cuts out of the code ranges the `nop` that GCC puts right
  behind an unconditional `add pc, pc, ...` in ARM state (a switch: pc reads as the instruction + 8,
  so no target lies at + 4) when no branch goes there. Ghidra's analysis leaves it out too.

### `code_ranges.tsv` (`code_ranges`)

The instruction runs of the image: `start`, `end` (exclusive), and optionally the instruction set
(`a32` or `t32`; default: the image's `[arch] isa`). Literal pools lie between runs. Runs use run
addresses, like the function table.

In Thumb code, instruction boundaries are found by walking a run from its start, so a run must
start at an instruction. Written by the Ghidra pipeline (always with the instruction set) or by
`dread import-elf`. Ghidra's heuristics sometimes take data for code, for instance a string behind a
pointer that a register carries. The listing still rebuilds, but such ranges are worth a look before
functions are written there.

### `symbols.txt` (`symbols`)

Names given by people or by generators: `f ADDRESS NAME` (a function), `d ADDRESS NAME` (data), or
`- ADDRESS` (a function start the analysis should take, without a name). Generated blocks inside
the file are marked by their generator. The analysis applies the names; the function table then
carries them.

### `annotations.txt` (`annotations`)

What is known about an address: `ADDRESS | text`, as many lines per address as needed. The listing
shows them as comments. `dread gui` writes it: a note replaces the lines of its address in place
(or goes to the end) and leaves every other line, comments included, as it was.

### `data_ranges.txt`, `asm_only.txt`, `left_out.txt`

Range tables, `START END reason` or `START-END reason` (end exclusive). `asm_only.txt` may also give
a single `ADDRESS reason`.

| Table | What it holds |
|---|---|
| `data_ranges` | ranges of the image that are data, with the evidence; the analysis creates no function there |
| `asm_only` | functions that stay assembly, with the reason (they use something C cannot express, for instance) |
| `left_out` | ranges the bulk tools leave alone, with the reason |

### `asm_raw_words.txt` (`asm_raw_words`)

Instructions that `dread asm` writes as raw `.inst` because GNU as encodes them differently from the
original bytes: one stored address per line. Maintained by `dread asm --verify`.

### `lifted.txt` (`lifted`)

The functions `dread recon` builds from lifted C (`dread lift`): one name per line, `#` comments.
Written by `dread lift --check`, which lists a function only when its generated cases (and the
kept and written ones) agree with the original and run every instruction of it, and when the target's
scenarios agree on the image with all of the table built (since 2026-10-22; a table written before
may hold functions whose C makes the whole image differ); not edited by hand.
The lifted C itself is made again at every build (`BUILD/recon/IMAGE/lifted/`) and not kept; a name
that the src directory defines is left to src. `BUILD/recon/IMAGE/lifted.txt` says which functions
the last build lifted, and `dread test` gives each of them its generated cases.

### `readable.txt` (`readable`)

The functions of the lifted table that `dread recon` builds from readable C (`dread readable`): one
name per line, `#` comments. Written by `dread readable --check`, which lists a function only when its
readable C, built over the original with the other lifted functions, passes the cases its lifted C
passed (they agree and run every instruction) and the whole image's scenarios agree; not edited by
hand. The readable C is made from the lifted C at every build, as the lifted C is from the original:
after `dread lift --check` or a change of the transformations, `dread test` tests what the build now
makes, and `dread readable --check` decides again. A name not in the lifted table is ignored.

### `readable_names.json` (`readable_names`)

Names for the variables of readable C (dread.naming): `{"version": 1, "functions": {NAME: {"digest",
"names", "ids", "what", "by", "carried"}}}`. `names` maps the readable C's variable names (r0, r4,
r2_1, s36) to the proposed ones, `what` is a sentence on what the function does (a comment above it),
`by` the provider that proposed them, `digest` a digest of the readable C without names the proposal
was made for: the entry applies only to that C. Written by `dread pilot --task readable`, which keeps
an entry only when every name is a C identifier nothing else in the file uses and the C with the names
compiles to the same code; not edited by hand. `dread readable --score ELF` compares the names with
the source's (the ELF's debug information).

`ids` (since 2026-10-23) holds the identity of every variable of the C the entry was made for
(dread.naming identities), one string a variable: a parameter `"param N"`, N its position in the head
(the head lists the argument registers in order); a local `"BASE DEF..."`, BASE the register or frame
slot it lives in (its name without the numbers of its webs: r4 for r4_2, s36 for s36_1), each DEF an
instruction that writes it (`0x1a2b`, in order; `entry` for a declaration's initial value), BASE alone
for a local nothing writes. When the transformations change the C, the names go to the variables of
the new C with the same identity (carrying): a parameter's to the parameter at its position, a
local's to the local of the same base whose definitions it shares most, more than half of all the two
have (Jaccard, dread.naming CARRY_OVERLAP), where each is the other's only best match. Every carried
name passes the identifier check again, and the C with them must compile to the same code; a name
whose variable has no match, or that the check refuses, is dropped. The entry then is one for the new
C: its digest and `ids`, its `what` and `by` as they were, and `carried`: `{"from": the digest it was
made for, "names": how many came over, "dropped": {old variable: name}, "open": [the variables of the
new C left without a name that the old C did not have, or whose name was refused]}`. `dread readable
--check` carries at its end (the check is what runs after the transformations change), `dread
readable --carry` alone, and `dread pilot --task readable` before it asks; the pilot asks a function
with carried names again only where `open` is not empty (its question shows the carried names in its
C; they stay unless the answer changes one), and its answer's entry has no `carried`. A carried
entry's comment says so (`(names: haiku (claude-haiku-5-5), carried from earlier C)`). An entry
without `ids` (a table from before) applies by its digest only and is never carried: it waits for a
new proposal, as every entry did before; an identity in another form matches nothing. With `ids` the
table of FreeRTOS's GCC -Os build (315 functions) is 266 KB instead of 208.

Since 2026-10-24 the names may name the frame's objects (dread.frames: `stack20` for the object that
starts 20 bytes below the frame's top): dread readable makes them before it applies the names, and the
pilot's question shows them. Their `ids` are their base (`stack20`, the offset) for an array or a
structure, with the instructions that write it for a scalar (`stack36 0x1a2b`), and their names carry
to the object of the same identity. An entry from before was made from a question that showed the
frame raw (`u32 frame[6]; u32 sp0 = (u32)&frame[6];`, `sp0 - 20` for the object): it has no identity
of an object and may name `frame` or `sp0`. Such a name is dropped where the objects took those away
(it used to keep the objects from being made), the entry's other names apply as before, and `dread
pilot --task readable` asks the function again for its objects (its question shows the other names).
A name for an object the C has not (the region split otherwise since, by the readable_types table's
`whole` or prototypes) is dropped alone.

Its `globals` part names memory at fixed addresses: `{"0xADDRESS TYPE": {"name", "by"}}` (TYPE the
access's: u32, u8, ...). dread readable writes such an access as the name, a macro above the
function that is the access itself; without an entry, or where the function's C uses the name for
something else, the default name says where it is: `io_40004004` (a device register: `[memory]
io`), `rom_c6e4` (a rom region), `g_20029aac` (anything else), with the type for an access other
than a u32 one (`g_20029aac_u8`). The pilot asks for these before the functions' variables, or
alone for every function's readable C (`dread pilot --task globals`, with the structures below);
they need no digest, since an address does not change with the transformations. Since 2026-10-24 a
proposed name keeps no such type at its end (`uxTimerFlags_u8` is stored as `uxTimerFlags`), unless the
name without it is taken or another proposal of the same question (two views of one address). A structure of
static data (the readable_types table's `statics`, since 2026-10-23) is named under `"0xADDRESS
struct"` (its address), optionally with `"fields": {"OFFSET": name}`; a field without a name there
takes the name of its own address and type (`"0x20000034 u32"`: the pilot asks for each field so,
written `g_20000030.f_4` in its question), else `f_4`. An array of static data (`arrays`, since
2026-10-23) is named alike under its address, its `fields` an element's by their offset in it; a
field without one takes the name of the first element's field's address and type (written
`g_200299f8[].f_4` in the pilot's question). A name that a global staying a macro has, or
another structure's, is not used for a structure or a field (it would expand inside them). A file
from before has no such entries: its structures read `g_20000030.f_4`.

Its `structs` part names the structures of the readable_types plan and their fields:
`{"FUNCTION:VARIABLE": {"name", "fields": {"OFFSET": name}, "anchors": ["FUNCTION:VARIABLE", ...],
"by"}}`. The anchors are the variables that pointed to the structure when it was named; an entry
goes to the plan's structure that most of them point to now (its number s_N changes whenever the
plan is made anew), its field names to the offsets that structure still has (dread.naming
struct_names). A name that is a keyword, a global's name (a macro: it would expand inside a tag or a
member) or a default name (s_3, f_2c) is not used. The pilot asks for these after the globals and
before the functions' variables, which then see the names.

### `readable_types.json` (`readable_types`)

The structures readable C declares (dread.shapes): `{"version": 1, "structs": {NAME: [[offset,
type], ...]}, "functions": {FUNCTION: {"digest", "vars": {VARIABLE: NAME}}}, "pointers": {NAME:
{"OFFSET": NAME}}, "embeds": {NAME: {"OFFSET": NAME}}}`. `pointers` (left out when empty) lists the
word fields that point to a structure's base: every object of the kind that points somewhere from
the field points there; such a field is declared `volatile struct NAME *`. `embeds` (left out when
empty; since 2026-10-22) lists the structures a structure holds as members (`struct NAME f_4;`): the
holder's `structs` row leaves out what lies inside them, a held structure's fields are its own. A
structure's fields
are those a C structure holds at their offsets without packing (naturally aligned, not overlapping,
the gaps bytes); a function's variables listed there are declared `volatile struct NAME *` and their
accesses written as members (`p->f_2c`, `p->f_4.f_8` inside a held structure; its address
`&p->f_4`), every other use cast to u32. `digest` is the readable C's
before names (dread.naming): a function whose C changed since gets no types until the next check.
Written by `dread readable --check` from every lifted function's readable C before it checks any;
the builds read it. Not edited by hand. A file from before `embeds` has none: its structures are
flat, as they were written; an `embeds` entry naming a structure the file lacks is left out.

`statics` (left out when empty; since 2026-10-23) lists the structures of static data at fixed
addresses (dread.statics): `{"0xADDRESS": [[offset, type], ...]}`, the fields laid out as
`structs`' are. Every function's access at such a field's address with its type is written as a
member of the structure's object (`g_20000030.f_4`, one macro `#define g_20000030 (*(volatile struct
g_20000030_t *)0x20000030u)` and the structure above the function), whatever the function's digest;
an access of another type stays the macro it was (a read of the field's width and the other
signedness is cast: `(s16)g_20000030.f_4`, since 2026-10-23). Made from every function's machine code
(and the lifted functions' readable C for the fields, and what callees reach through the addresses
it hands them). A file from before has none: readable C writes a macro per address and type, as
before.

`arrays` (left out when empty; since 2026-10-23) lists the arrays of static data (dread.statics
find_arrays): `{"0xADDRESS": {"size": element size, "count": elements, "fields": [[offset in the
element, type], ...]}}`, an element's fields laid out as `structs`' are and aligned in every element.
An access to an element's field is written as one (`g_200299f8[r3].f_4`, `g_20000c74[1]` for an
array whose elements are one field each), at a constant address inside the counted elements or at
the array's address plus an index times the element size; the macro is a pointer to the first
element (`#define g_200299f8 ((volatile struct g_200299f8_t *)0x200299f8u)`, `((volatile u32
*)0x20000c74u)`), the elements' structure as large as an element. The count is what the code reaches
at constant places (at least one), not the source's. Where readable C uses the address of a
structure, an array's element or a field of either as a value (an argument, a value assigned or
compared, a result), it is written as such (`(u32)&g_20029988`, `(u32)&g_200299f8[r3]`). A file from
before has none: indexed accesses stay as the lifter's arithmetic. Since 2026-10-23 an array starts
at the address the code made where its accesses' constants lie below it (`table[id - 1]`: the
literal, not the constant an element below it); an access whose constant lies below an array that
starts at the address its arithmetic adds is that array's element at the index minus the elements
below (`g_20000020[r0 - 1].f_4`). A file from before keeps its starts (an array then started at the
lowest constant, or was not found).

`params` (left out when empty; since 2026-10-23) lists, for every analysed function, the indices of
its parameters that it uses as a pointer (dread.shapes pointer_params): `{FUNCTION: [0, 2]}`. A
constant that is the address of static data is written `(u32)&g_x` as an argument only where the
callee is listed with that parameter (a callee not listed, or a file without `params`: as before),
and as an operand of `==` or `!=` or a value assigned to a variable only where it is used as an
address there (dread.naming apply_globals); never for an object where the image keeps no static
data (outside its bytes and its startup's ranges).

`variables` and `prototypes` (left out when empty; since 2026-10-23) hold the types dread.vartypes
gives readable C's variables and prototypes. `variables`: `{FUNCTION: {"digest", "types": {VARIABLE:
TYPE}, "calls": {CALLEE: {"INDEX": TYPE}}}}`, a variable's type a pointer (`volatile u8 *`, `const
volatile u16 *`, `volatile struct s_3 *`: a structure by its name in `structs`) or `s32`; `calls` (left
out when empty) the callees' parameters (by index) that the function's file declares in their externs
with a type, the others staying as the lifter declared them. `digest` as in `functions`: the variables
and the externs' parameters are typed only in the C they were made for. `prototypes`: `{FUNCTION:
[RESULT, PARAMETER, ...]}`, each a type or null (as the lifter declared it): the function's own
parameters' types (its variables', and the structure pointers of `functions`) and its result (`s32`
where its callers compare it signed; a caller's extern takes the result type whatever its digest).
Every expression is rewritten to compute what it did (pointer arithmetic in elements, casts where a
pointer meets a number); a file whose rewrite wrote pointer arithmetic or an s32 variable starts with
the line `/* dread: overflow wraps */` (dread.build: compiled with -fno-strict-overflow), one with typed
pointers with dread.build's null checks line. A file from before has neither part: readable C is as
before (a structure pointer's advance by its size is p + 1 all the same).

`keeps` and `returns` (since 2026-10-23; written whenever the check runs, also empty) list, for every
analysed function, the indices of the parameters it may keep and those whose value its result may be
(dread.frames kept_params): `{FUNCTION: [0, 2]}`. A parameter is kept where a value derived from it is
stored into memory or a global, handed to inline assembly, through a pointer, or to a callee that may
keep it (one not analysed keeps all); an opaque function (a naked entry, a variadic function regflow
cannot tell) is listed with every index from 0 to 15. dread.frames declares an object of the frame
whose address goes only to callees that keep nothing without volatile. A file from before has neither
part: every object of the frame is volatile, as before. `whole` (left out when empty; since
2026-10-23): `[FUNCTION, ...]`, the functions whose frame's region dread.frames writes as one volatile
object, as before that day, instead of an object at each address handed out: the check puts a
function there whose objects its cases rejected (a callee reaching past the object it was given), and
keeps it there when it is accepted so; the builds read it.

`takes` (since 2026-10-24; written whenever the check runs, also empty) lists the parameters that take
texts' or functions' addresses (dread.naming taken_kinds): `{FUNCTION: {"INDEX": "text" | "function"}}`,
from every lifted function's readable C: a parameter the calls give at least two distinct constants
besides 0, every one a function's entry as code takes its address (bit 0 set for Thumb code), or every
one the start of a text with more than half of them where a text begins (behind a NUL), and a
parameter the function calls through. Readable C writes a function's entry handed to such a parameter,
or to one `params` lists, as the function's address (`(u32)&prvTimerCallback`, with the macro
`#define prvTimerCallback FUNCTION_AT(0x6ed5u)` of dread.h above the function: the same number), and a
text's start as its number with the text in a note beside it (`0xd9cu /* "FR Timer" */`). A file from
before has no `takes`: only the parameters `params` lists, as pointers, give them.

### `wide_results.txt` (`wide_results`)

Callees whose generic stubs (dread.autocase: a case's `calls`) answer two words: one name per line,
`#` comments, anything behind the name ignored (a target's evidence). Such a stub answers its answer
in r0 and `(answer * 0x9E3779B1 + 0x7F4A7C15) & 0xFFFFFFFF` in r1 (a1, ...: the second result
register). The lifter takes a function's width from dread.regflow, not from here; the table is only
the stubs'. A function whose callers read registers beyond the C result that it sets (dread.lift's
more_results: r2, r3, r12 on ARM, a2-a7 and t0-t6 on RISC-V) needs no row: since 2026-10-23 its stub
answers those as well, the k-th of them (from 0) `(answer * 0x9E3779B1 + 0x7F4A7C15 * (k + 2)) &
0xFFFFFFFF`. Both are facts of the function stubbed in the generated cases' keys: since 2026-10-23
(below, "the key") a row added or removed, or registers more found or lost, give new keys to the
functions whose runs reach that function, the others keep theirs.

### `domains.tsv`, `stale_readers.tsv`, `function_starts.txt`

Described with the tools that use them, as those tools arrive in DREAD (no tool reads
`stale_readers.tsv`: the lifter finds the registers a callee reads of its caller itself,
dread.lift's leaves). The rows of `domains.tsv` are in the generated cases' keys since 2026-10-23: a
function's rows (`NAME:...`) in the facts of that function, an address's row in every key.

## The rebuilt image (`BUILD/recon/IMAGE/`)

Written by `dread recon` (and `dread overlay`): `payload.bin` (the image with the C linked in),
`recon.ld` and `recon.elf` (the link), and two reports of where each C function went.

### `report.tsv`

`function`, `address` (the original entry), `original_bytes`, `c_bytes`, `placement` (text for
people), then a `#` summary line. Not for programs; they read placement.json.

### `placement.json`

```json
{"functions": {"NAME": {"entry": 131596, "address": 134300, "original_bytes": 180, "c_bytes": 184,
                        "placement": "behind the image", "branch": true, "isa": "a32"}},
 "summary": {"replaced": 13, "functions": 50, "image_bytes": 3440, "room_left": 127632,
             "freed": 216, "reused": 148}}
```

`placement` is one of `in place`, `over its pools`, `behind a pool`, `freed space`, `free space`
(one of the image's free ranges, `[image] free`), `behind the image`, or `helper` (a C function the
table does not have; no `entry`). The summary's `free_left` is what the free ranges still hold. `branch` says
that the old entry holds a branch to `address`; the test runner enters such a function through it.

## Generated, kept and written cases (JSON)

Generated cases (cached in `BUILD/autocases/IMAGE/NAME.json` as `{"key", "seed", "cases"}`), the
image's `kept_cases/NAME.json` (cases that once showed a difference; `--keep-failing` adds to it) and
`extra_cases/NAME.json` (cases written to reach what the generator does not) share one format,
`{"cases": [CASE, ...]}`, with these fields. So do the cases of the short search dread.behaviour
makes for a function without any (`BUILD/behaviour/IMAGE/NAME.json`, `{"key", "cases", "seconds"}`:
the seconds the search was allowed; only for showing what a function does, never for tests):

| Field | Meaning |
|---|---|
| `args` | the argument registers |
| `mem` | `[[address, size, value], ...]` written before the call (peripheral registers too) |
| `returns` | the answers of the stubbed callees, in call order (later calls answer 0) |
| `calls` | `[[callee, register mask, stack words], ...]`: the generic stubs; written cases without it get auto()'s; recorded cases (`dread record`) carry the ones they were recorded with, and those are kept. A fourth element, a list of call numbers (from 1, counting the callee's calls in the run), makes the stub answer those calls only: at the others the callee runs as if no stub were there, and they take no answer from `returns` and count as no stub call (the frontier answers, since 2026-10-23; a DREAD from before reads three elements and stubs every call) |
| `frame` | the bytes of the function's frame a stub may fill behind a pointer to a local (as `calls`: from auto() unless the case carries it) |
| `effects` | `[[call number, address, size, value], ...]` written by that stub call before it returns |
| `fills` | `[[call number, which pointer, offset, size, value], ...]` set inside what a stub stored behind a pointer |
| `io` | `"fixed"`: peripheral reads return the `mem` values whatever was written |
| `poll` | `[[address, n, value], ...]`: from the n-th read of that register on it returns value |
| `later` | `[[address, size, n, value], ...]`: just before the n-th read that starts at address, memory there becomes value, once (the code's own writes stand after it): what other tasks or handlers wrote while a recorded call waited (`dread sysrecord`) |
| `stops` | `[address, ...]`: callees whose call ends the run (one that never returns, one a run hung in): the case is compared up to that call |
| `limit`, `stub_calls` | instructions (default 200,000: a budget of that many of the instruction set's shortest instructions, counted in bytes of code entered) and stub calls (default 256) one run may take |
| `hang` | true: not returning is the result (compared up to the limit) |
| `tight` | true: a stub's fill stops at the first stack word the function stored itself |
| `carried` | true: an input of an earlier generation, kept when the cases were made anew because it runs instructions the new ones do not, or that only new ones run whose outcome belongs to the original's frames (dread.autocase's carried; for reading only, the runner treats it like any case) |
| `answer` | true: a case the frontier answers made from another (dread.autocase's answers: a callee's answer at one call, a stubbed callee's answer, or the memory a load reads, set to what a branch's other way needs; for reading only) |
| `firm` | true: a fragile case (its outcome changes with the stack, or its input makes code touch a saved register: dread.fragile) with one input set small, added because it runs instructions only fragile cases ran (dread.autocase's firm_up; for reading only) |
| `why`, `by` | where a written case comes from and why (`dread mutate --kill`, `dread record`); nothing reads them but people |
| `kept` | in kept cases: `"stubbed"` or `"real callees"` |

## Test cases (the image's `cases` directory)

Python files that call the case API of `dread.cases` (`case`, `auto`, `unreachable`, `entry`,
`logging_stubs`, `words`), run in name order with those names defined. The module's docstring is the
reference: what a case is and what is compared.

### `BUILD/times/`

Written by every parallel run (`dread test`, `dread integration`): `NAME.json` keeps the seconds
per item of the last full run, for handing the slowest out first; `NAME.tsv` is the report of the
last run, `item` and `seconds`, slowest first, ending with a comment line with the total seconds of
work and the wall-clock time of the run. A generated-case cache entry
(`BUILD/autocases/IMAGE/NAME.json`) also says how many `seconds` its generation took, and with
`firm` the version of the firm-up its cases had (dread.autocase.FIRM: an entry without it, or with an
older one, gets the firm-up once, its cases kept; a `failed` entry holds the generation's error).
`answers` (since 2026-10-23) holds the frontier answers made from the entry's cases and the image's
kept and written cases of the function: `{"key", "cases", "seconds"}`, the key a digest of the
entry's key, its `firm`, dread.autocase.ANSWERS and those kept and written cases; under another key
they are made anew and the generated cases stay. Readers that take `cases` only see the cases without
the answers.

The entry's `key` (dread.autocase case_key) is a digest of the function's instruction bytes, the
generator's VERSION (with the architecture layer's generator_version where it is not 0), its auto()
options (with the calls, the frame and what the key names besides: the stack switches reached, the
sweep's cap, the loop's calls, the tail calls through a pointer) and the environment
(Generator.environment). Since 2026-10-23 the environment is scoped to the function's call tree:

- whole, the same in every key of the image: the image's bytes, the startup copies, the memory map
  (regions, devices, stack, sentinel, scratch, the image's limit), the layout of the code (the code
  ranges, every function's entry, size and instruction set, no names), the calling convention, the
  registers fixed in the whole program, the core, the stall cut and the learned pointers' slots where
  they apply, the domains table's rows for addresses, and what dread.regflow's case facts
  (RegFlow.case_facts) hold that is not a table by function name (the register names);
- by function, the facts of every function the function's runs can reach (dread.autocase reaches: its
  calls, tail calls, the fall behind its last instruction, the targets a run of an indirect jump's
  dispatch finds, transitively): its name and entry, its entries in regflow's case facts (kept, reads,
  sites, preserved, and any table by function name regflow adds there; not `overwritten`, which no case
  reads), the registers beyond a C result its stub answers, its `wide_results` row and its domains
  rows. Where a tree has a transfer no table and no run of its dispatch tells (a call through a
  function pointer), the facts of every function. They are digested once per strongly connected
  component of the call graph, bottom up.

So a change of one function's facts makes anew only the cases of the functions whose trees hold it,
and of those whose trees call through a pointer; a change of anything whole, every case of the image.
An entry made before 2026-10-23 has a key that matches no key made since: every image's cases were
made anew once, unchanged (the search is the same; only the key differs).

### `argued.tsv` (beside the cases directory)

Survivors of `dread mutate` that cannot be caught, with the argument why the change alters nothing:
tab-separated `function`, the source line as written (stripped), the change as `dread mutate` names
it (`>= -> >`, `4 -> 5`, `removed`, `a <-> b`), and the argument. A survivor that matches is listed
with its argument and not counted. The key includes the line's text, so an argument stops applying
when the line changes.

### `kept.tsv` (beside the cases directory)

Registers that `dread regflow` finds read by callers after a call, but whose difference at the
return cannot matter, with the argument why: tab-separated `function`, `register` (`r1`, `r12`,
`flags`, ...), and the argument. `dread test` and `dread integration` do not compare that register
at that function's return and list the argument under the function's result. Typical arguments: the
caller reads it only on a path that cannot happen, or only bits the next callee ignores.

### `tests.tsv`

Written beside the rebuilt image by a complete `dread test`: `function`, `cases`, `failed`, `missed`
(the instructions of the original no case ran, as hex addresses separated by spaces; empty when
the cases run all of it).

### `BUILD/pilot/IMAGE/names.tsv`, `names-score.tsv`, `names-pairwise.tsv`

Written by `dread pilot --task name`: one line per proposal for a function's name, appended as the
answers come (the last line of an address is its proposal; every earlier one stays). Columns, by
the header line (readers go by it):

| column | meaning |
|---|---|
| `address`, `now` | the function's entry and its name in the image (`FUN_...`) |
| `proposed`, `what` | the proposed name and the model's sentence about the function |
| `provider` | who answered (`haiku (claude-haiku-5-5)`) |
| `shown` | the form of the code the question showed: `readable`, `readable, unchecked`, `listing`; behind it `, behaviour` when the question showed the function's runs (`--behaviour`), `, subsystem` when it showed its group's name and prefix (`--subsystems`, since 2026-10-22), `, refined` for a `--refine` answer and `, review` for a name a review put there (`--task review`, since 2026-10-23; readers look for the marks among the comma-separated parts) |
| `confidence` | `--samples K`: the agreement of the vote (answers alike to the kept name, `dread.naming.similarity` 0.5 or more, over K: `0.60`); empty for a single question |
| `samples` | `--samples K`: the K answers in the order asked, `-` for a question without one |
| `pass` | the pass that made it: 1 the first (callees first); 2, 3 ... `--passes` (callers first on even passes); 2 for `--refine`; for a review's change the last pass before the review plus its round (so `--upto-pass` with the last naming pass gives the names before it) |
| `question` | a digest of the question's text: a later pass asks again only when it changed |
| `names_shown` | how many other functions' proposals the question showed (0: none) |
| `hidden`, `depends` | `--ablate`: the vote of the same questions with no other proposal shown, and whether it disagrees with the proposal (`yes`, `no`); with `names_shown` 0 the two questions were the same, so `yes` is the votes' own noise |

A file written before 2026-10-22 has the first six columns: its proposals have no confidence (a
`--gate` hides them), their pass is 1, or 2 where `shown` has `, refined`; one older still has no
`, behaviour` in `shown`, and reads alike. A new proposal gives such a file the new header and leaves
its lines as they were.

`dread pilot --score` writes `names-score.tsv` beside it (`names-score-passN.tsv` with
`--upto-pass N`): `address`, `true`, `proposed`, `exact`, `same_words`, `overlap`, `same_prefix`
(empty where a name has no prefix), `judged` (the judge's `same`, `close`, `different` with
`--judge`, else empty), the proposal's `confidence`, `depends`, `names_shown`, and since 2026-10-22
`style` (dread.naming.style_similarity, 0 to 1: the words whatever the naming style), `grounded` and
`grounded_why` (with `--grounded`: the verdict of a judge that read the function's source, `right`,
`partly`, `wrong`, and its reason, empty after `right`; both empty without the flag or for a function
without a source), `sentence` and `sentence_why` (the same for the proposal's sentence, judged apart;
empty with `--names-only`). A file of before 2026-10-22 ends at `names_shown`. `--score TRUTH --judge P --against OTHER` (another run's names.tsv) writes instead
`names-pairwise.tsv` (`names-pairwise-passN.tsv`), one line per function whose two proposals differ:
`address`, `true`, `this` (this run's), `other`, `closer` (`this`, `other`, `tie`; empty when the
judge gave no two verdicts). With `--grounded P` in place of `--judge` the lines are those of the
functions with a source, `closer` the one with the better verdict against the source (the same verdict
a tie), and two more columns give the verdicts: `this_grounded`, `other_grounded` (`right`, or
`partly: reason`, `wrong: reason`). Before it, on 2026-10-22, `--versus OTHER` wrote `names-versus.tsv`:
`address`, `true`, `proposed`, `other`, `closer` (`mine`, `theirs`, `equal`), each pair judged in one
order, chosen by the address; replaced by `--against`, nothing reads it.

### `BUILD/pilot/IMAGE/names-replicates.tsv`, `names-replicates-pairs.tsv`, `grounded-answers.jsonl`

Written by `dread pilot --task name --score TRUTH --grounded P --against RUN... --vs RUN...` (since
2026-10-23: two settings compared over replicate runs, dread pilot `compare_runs`; `-passN` before
`.tsv` with `--upto-pass N`). The runs are named `a1`, `a2`, ... (`--against`, in the order given)
and `b1`, `b2`, ... (`--vs`). `names-replicates.tsv` has a line per function judged in every run:
`address`, `true` (the true name), `group` (its group in the grouping counted: groups.tsv's number, or
the source file; empty for a function outside every group), then for each run its proposal and its
verdict against the source, `a1`, `a1_grounded`, `a2`, `a2_grounded`, ... (the verdict as in
`names-pairwise.tsv`: `right`, or `partly: reason`, `wrong: reason`). `names-replicates-pairs.tsv` has
a line per pairing of two runs: `first`, `second` (labels), `kind` (`across`: a run of each setting;
`same`: two runs of one), `differ` (the functions whose two names differ), `first_closer`,
`second_closer`, `ties` (by functions, the better verdict closer), `p` (the sign test over functions),
`groups_first`, `groups_second`, `groups_even`, `groups_p` (counted by groups, `group_sign`).

`grounded-answers.jsonl` keeps every answer of the grounded judge (`--grounded`, with `--score`,
`--against` and `--vs`) by its question, a JSON object a line: `key` (a SHA-256 digest of the
provider's kind and model, the system part, the question's text and the attempt: the n-th asking of
one question) and `answer` (the text as it came). A question it holds is answered from it and not
asked (dread.llm AnswerCache); `--no-judge-cache` neither reads nor writes it. Removing the file only
costs the answers again.

### `BUILD/pilot/IMAGE/groups.tsv`, `subsystems.tsv`, `names-subsystems.tsv`

Written by `dread pilot --task subsystems` and by `--task name --subsystems` (since 2026-10-22): the
groups `dread groups` finds from the code (dread/groups.py: no names used) and the names a model
proposed for them.

`groups.tsv` is the grouping the names were asked for, rewritten whole each time: `address`, `now`
(the function's entry and name), `group` (its group's number in this grouping: groups are numbered
by their first function's address), `members` (a digest of the group's functions' entries, the
group's key).

`subsystems.tsv` gets a line per group named, appended (the last line of a `members` key counts; a
group with a line is not asked again: remove the file to ask anew): `group` and `first` (its number
and its first function's address when asked), `functions` (how many), `members` (the key), `name`,
`prefix` (snake_case, ending in `_`), `what` (the model's sentence), `provider`, `confidence` and
`samples` (`--samples K`: the agreement of the vote over the prefixes, and the K prefixes in the
order asked, `-` for a question without an answer; empty for a single question), `question` (a digest
of the question's text). A group of a later grouping with other functions has another key: its old
line stays and is not used. `dread groups --json` and `dread gui`'s whole program take a group's name from
it (since 2026-10-22). A review (`--task review`) that renames a group appends a line for its key
(`question` then the digest of the group's question without the review's note; review.tsv says why).

`dread pilot --score TRUTH` writes `names-subsystems.tsv` beside them when the image has both:
`group`, `functions`, `name`, `prefix`, `file` (the source file most of its functions come from, by
the truth's debug information; empty without), `from_file` (how many come from it), `proposals` (its
functions with a proposal), `use_prefix` (how many of those begin with its prefix), `judged` (with
`--judge`: whether the name and prefix name that file's part, `same`, `close`, `different`).

### `BUILD/pilot/IMAGE/review.tsv`

Written by `dread pilot --task review` (dread/namereview.py, since 2026-10-23): one line per challenge
of a name, appended as the challenges of a round end, in the order of their scores (groups first). A
change it decides is also a line of names.tsv (a function's) or of subsystems.tsv (a group's), which
every reader takes; this table says why. Columns, by the header line:

| column | meaning |
|---|---|
| `review`, `round` | the review's number (1 more than the last in the file) and the round in it |
| `kind` | `function` or `group` |
| `address`, `now` | the function's entry and its name in the tables (`FUN_...`, or a person's name), or the group's first function and `group N` (its number in groups.tsv) |
| `group` | the members key of the function's group, or of the group (groups.tsv, subsystems.tsv) |
| `source` | `proposal` (names.tsv's, or a group's name), `person` (a name in the tables: never changed) |
| `score`, `reasons` | the reasons' summed weight, and the reasons, `kind weight: sentence` joined by `; ` (dread.namereview WEIGHTS) |
| `old`, `old_what` | the name before (a group's as `name (prefix)`) and its sentence |
| `candidate`, `candidate_what` | the candidate the vote over the samples kept (for a person's name that lost: the name the decision preferred), empty when no answer gave one |
| `agreement` | the candidate's vote: `3 of 5` |
| `votes` | the decision's votes, `new 2`, `new 1, same 1`, `old 1, general 1` (`none`: an answer without a verdict); empty when no decision was asked |
| `outcome` | `confirmed` (the candidate has the old name's words: no decision), `kept` (the decision did not reach the margin), `changed` (the candidate), `general` (a more general name both orders gave), `flagged` (a person's name the decision went against: left for a person), `no candidate` |
| `name`, `what` | the name and sentence after the challenge |
| `provider`, `decider` | who gave the candidates and who decided |
| `reason_keys` | digests of the reasons' sentences: a later challenge of the same name needs a reason not among them |

The conversations are in `review/NAME.md` beside it (`review/group-N.md` for a group), appended.

Readers (dread.namereview `standing`, since 2026-10-23: `dread relations --json`, `dread groups
--json`, `dread gui`) take a function's rows by `address` and a group's by `group`, and of them only
those whose `name` is the name the function or group goes by now: a later naming pass, or a person
renaming the function, leaves the earlier rows about another name. The last of them gives the mark:
`flagged`; `suspect` after `kept` or `no candidate`; `changed` after `changed` or `general`, and also
after a `confirmed` of a name an earlier review gave; else `confirmed`. `reasons` is split where a
reason's kind and weight begin (a sentence may hold `; `).

### `BUILD/pilot/IMAGE/device.tsv`

Written by `dread pilot --task device` (since 2026-10-22): what kind of device the image runs on, as
a model read it from the texts its code points to. A line a run, appended; the last line counts:
`device` (the device most answers agree with, dread pilot `choose_device`; `unknown` when the texts
did not say), `confidence` (the share of the answers whose words overlap it by half or more),
`samples` (every answer's device in the order asked, ` | ` between them, `-` for an answer that named
none), `provider`, `texts` (a digest of the texts shown), `question` (a digest of the question's
text). The conversation is kept in `device.md` beside it. The naming tasks (`--task name`,
`readable`, `globals`, `subsystems`) begin every question with the last line's device, unless the
target states one (`[llm] device`, docs/target.md), the device is `unknown`, or `--no-device` is
given.

### `BUILD/lift-check/IMAGE/verdicts.tsv`

Written by `dread lift --check`: one line per function it looked at, `function`, `verdict`
(`accepted`, `refused`: the lifter cannot translate it, `dropped`: lifted, but the cases or the
system run disagree or do not run all of it, the overlay cannot place it, or the whole image differs
with it, alone or together with the functions its reason names; `by hand`: src defines it), `reason`
(the lifter's or the test run's words), `missed` (for a dropped function, the instructions no case
ran). `dread gui` reads it; `dread rename` renames in it. Beside it `whole.txt`: the whole image's
outcome (`agrees` or its first difference) on the first line, then one line per set of functions
dropped for it (`dropped for it: A, B; with them alone: ...`, since 2026-10-22; where leaving out one
of a smallest set of three or more is enough, only that one goes: `dropped for it: B (leaving it out
alone is enough; the smallest set that differs: A, B, C); with them alone: ...`, since 2026-10-23,
and its reason in verdicts.tsv reads `the whole image differs with its lifted C; without it alone it
agrees (...)`). `dread uncovered` reads it (the `missed` column, and the reason's first words for how
the function was dropped). Since 2026-10-24 a dropped function's FAIL reason may go on after its case's
label with the refusal that held but did not explain every difference (`FAIL NAME [label]: refused for
X, but it differs Y: Z`, dread.runner), and the runner's REFUSED lines in the round files beside it end
with what their reasons explained (`(it differs only after the touch: ...)`); older files have
neither, and readers take the lines by their first words as before.

### `BUILD/sysrun/IMAGE/NAME.json` (the system run's record)

Written by `dread sysrun` (since 2026-10-23), one a scenario it runs (NAME the scenario's), and by
`dread lift --check`, one a scenario of its last whole image (NAME `SCENARIO.lift-check`): dread/sysrun.py
make_record. One JSON object, `{"version": 1, "name", "scenario", "by" (the tool), "image" (its
name), "limit" (shared-clock units), "built" (how many functions the original's clock did not count:
those the rebuilt image has from C), "first", "keep", "runs", "functions", "differ", "calls",
"unexplained"}`; keys sorted, no wall-clock time (the file's time says when).

- `runs`: the original's run first, then the rebuilt image's where there is one: `label` (`original`,
  `rebuilt`), `how` (how it ended, as dread sysrun prints it), `clock`, `entries` (its device log's
  length), `digest` (SHA-1 of the image's bytes it ran), `reports` (the models' reports),
  `interrupts` (`{number: times taken}`), `log` (the device log's entries `first` to `first + keep`,
  each as the run compares them: `["read", model, address, size, value, times in a row]`, `["write",
  model, address, size, value]`, `[model, what, value]` for what a model noted (`sent`, `pins`),
  `["interrupt", controller, number]`, `["syscall"]`; model `-` where no model owns the register), `at`
  (the pc of each entry's access: the instruction that made it; for an interrupt where the code it
  interrupted goes on; null where unknown) and `in` (the function there, an index into `functions`,
  -1 for none; the rebuilt image's C by its placement.json).
- `functions`: the names `in` refers to, both runs' (a helper of the rebuilt image's C by its own).
- `first`, `keep`: the window of the logs kept: at most `keep` entries (KEEP, 100,000), from the start,
  or, when the logs are longer and differ, around the first difference.
- `differ`: null when both runs agree (or there is one run), else `{"at": the first entry where the
  logs differ (null when only the reports or the endings do), "lines": dread.sysrun compare's lines}`.
- `calls`: who entered whom in the original's run: `[{"to", "entry", "how", "times", "from", "site",
  "exception"?}]`, one row a way into a function, sorted by entry, how and site; `how` is `call`,
  `jump` (direct: the listing shows them too), `pointer`, `pointer-jump` (through a register),
  `return` (a return that went to a function's entry: a task's first run), `falls` (the code in front
  ran into it), `exception` (with `exception`, the number the run took it as), `syscall`, `reset` or
  `unknown` (no instruction of the image there); `from` and `site` are the function and the address
  of the instruction the block in front ended with (null for the marks). `unexplained`: transfers
  no instruction explained (left out of `calls`; 0 when the record is right).

Readers (`dread gui`, `dread relations`, `dread groups --json`: dread.sysrun records, ran) use a record
only when its `version` is 1 and its original's `digest` is that of the image's bytes now; they name
the functions by `entry` and `site` as the tables name them now (`to` and `from` are the names when
it was written). `ran` merges the rows of every record by entry, how, site and exception, with
`times` summed and `scenarios` the records' names.

### Why cases leave code unrun (`dread uncovered --json FILE`)

`{"totals", "table", "forced", "functions"}`, since 2026-10-23. Addresses are hexadecimal strings
(`"0x1a2"`); every list is in a fixed order (functions, regions and ways by address, rows by missed
instructions, then regions, cause and source), so the same verdicts, image and cases give the same bytes.

- `totals`: `functions`, `regions`, `instructions` (missed), `kinds` (functions per kind, below),
  `stale` (functions whose cases were cached under another key than the generator makes now).
- `table`: one row per cause and source, `{cause, source, regions, instructions, functions,
  examples}`; `examples` holds `--examples` lines `FUNCTION START: TEXT`. `forced`: the same rows by
  cause and the main way's forced run (`returns`, `faults`, `limit`, `spins`, `stops`, `touches the
  image`, `not sent`, `no case got there`; regions without one are left out); empty with `--no-runs`.
- `functions`: `{NAME: {kind, missed, instructions, entry, reason, regions, cases, runs}}`. `kind`:
  `cases` (UNCOVERED by its cases), `loop cases and the system run` (a function that never returns),
  `no cases` (its generation failed: every instruction counts as missed), `cached cases` (named, not
  dropped for missed instructions: what its cached cases leave unrun, run on the original), `other`;
  `missed` and
  `instructions` are counts; `reason` the verdict's. `cases` (not with `--no-runs`): `cases` (how many
  were found), `stale`, `failed` (a failed generation's message, or why there are none), `stubbed` and
  `stops` (`{callee: cases}`), `polled` (registers), `hang` (cases), `loop`, `refused` (the lift
  check's REFUSED and FAIL lines), `kind`, `reason`. `runs`: `{runs, ends: {how a run ended: runs}}`.
- A region: `start`, `instructions`, `missed` (its addresses), `cause` (`branch`, `switch`, `call`,
  `straight`, `not entered`, `unreached`), `source` (the main way's source class, or null), `text`,
  `key` (`[cause, the table's source column]`), `forced` (the second table's column), `notes` (the
  search's words), `ways`.
- A way in: `at` (the instruction that ran), `to` (the first missed one), `cause`, `insn`, `text`; for a
  branch or switch `deciders` (the instructions that decide it), `inputs` (`[decider, register]`),
  `sources` (`[[class, text], ...]`, the main one first), `source`, `primary` (its text), `base` (a
  field's base class), `callee` and `device` where the main source is one; for a call `callee` (null:
  through a register). With runs: `reached` (runs that got to `at`), `values` (`{reached, registers:
  {register: [[value, runs], ...]}}`, the most frequent first, at most six and `["...", more]`), `ends`
  (a call's: `{how the runs that got there ended: runs}`), `forced` (`{end, ran}`: how the run sent the
  missed way ended, and how many of the region's instructions it ran).

### `BUILD/relations/IMAGE/NAME.json`

What `dread relations` read of one function, kept so that a second look costs a lift instead of
the readable C's clean-up: `{"key", "facts": {"from": "readable C" or "listing", "why": why not its
C (null when from it), "digest": its readable C's digest before names (dread.naming; what the
readable_types plan is made for) or null, "refs": [[address, type or null, indexed, read, write],
...]}}`. A ref is an access at a constant address (indexed false) or at a constant plus something
(indexed true, at the sum of the constants), with its access type and whether it reads or writes,
or an address the code only names (type null, neither read nor written). The key is a digest of
what the facts are made from: the function's lifted C (or why there is none), regflow's cache key,
the code that makes and reads readable C, the memory map, and a version of what references() makes
of them (2 since 2026-10-22: ARM's movw and movt make an address together, so clang's texts and
callbacks are found). A file whose key differs is made again; none is edited by hand, and a renamed
function's old file is left (its key no longer matches). `dread groups` reads them too.

## The relations graph (`dread relations --json`, `dread gui`'s `/api/graph`)

`{"center", "depth", "nodes": [...], "edges": [...], "missing", "left_out": {"functions", "nodes"},
"counts": {kind: number}}`. The GUI's answer has `pending` instead of `missing` (the functions
whose facts its worker still makes), `status`, and an `origin` on each function node.

- A node: `id` (`f:NAME`, `m:0xADDRESS`, `t:0xADDRESS`, `s:s_N`), `kind` (`function`, `global`,
  `register`, `rom`, `table`, `text`, `structure`), `name`, `address` (null for a structure),
  `size` (null where unknown), `naming` (`named`, `proposed`, `unnamed`; null for a text) and
  `proposed` (`{"name", "what"}`) where the pilot proposed one, and since 2026-10-23 `certainty`
  (none for a text): how sure its name is, dread.relations `certainty()`, one of `none`, `unvoted`,
  `unsure`, `likely`, `sure`, `named` (a proposal by its vote's agreement: `sure` where every answer
  agreed, `unsure` at or below the agreement whose vote alone makes `--task review` challenge it, 0.5;
  `unvoted` without a vote, which memory and structures always are).
  - A function also has `distance` and `side` (`center`, `caller`, `callee`, `both`, or `referred`
    for one whose address is taken here; a caller or callee through the system run's transfers is one
    too), `more` (`{"callers", "callees"}` beyond the graph, those transfers counted), `facts`
    (`readable C`, `listing`, `pending`, or null outside the depth), `why`, `lifted` and `readable`
    (the tables'); since 2026-10-23 `confidence` (names.tsv's, of the proposal it goes by, where a vote
    was taken) and `review` where review.tsv challenged it (dread.namereview `standing`: `mark` of the
    last row about the name it goes by now, `flagged`, `suspect` for one kept or without a candidate,
    `changed`, `confirmed`, or null when every row is about another name; `last` and `changed`, those
    rows as `challenge()` makes them: `review`, `round`, `score`, `mark`, `reasons` [{`kind`, `weight`,
    `text`}], and the row's other columns where set; `stale`, the rows about another name;
    `challenges`, all of its rows). Older readers ignore the new fields. Since 2026-10-23
    `entered_by` where the system run's records saw the core enter it: `[{"how": "exception",
    "syscall" or "reset", "exception"?, "times", "scenarios"}]`.
  - Memory has `types` (the access types) and `default` (the default name); a table also has `words`
    (how many of its words were read).
  - A text has `text`.
  - A structure has `plan` (s_N) and `fields` ([[offset, type, field name], ...]; a structure it
    holds is a field of type `struct NAME`).
- An edge: `from`, `to`, `kind`:
  - `calls`, with `sites`;
  - `reads`, `writes` or `uses`, with `types` and `indexed`;
  - `refers-to-text` and `refers-to-function`, from a function or a table;
  - `uses-structure`, with `variables`;
  - `points-to`, from structure to structure, with `offsets`;
  - `holds`, from a structure to one it holds as a member, with `offsets`;
  - since 2026-10-23, from the system run's records (BUILD/sysrun, dread.sysrun ran): `calls-pointer`
    (a call or jump through a register) and `enters` (a return that went to its entry, the code in
    front running into it), from function to function, with `how` (the records' hows: `pointer`,
    `pointer-jump`; `return`, `falls`), `times` (in all the records), `sites` and `scenarios` (the
    records' names).

Nodes come in a fixed order (the functions by distance, side and address, then what each touches),
so the same tables give the same record.

The GUI's other answers (`/api/functions`, `/api/overview`'s `names`, `/api/function`'s `name`,
`/api/program-search`, `/api/status`'s `names`) are described in dread/gui.py's docstring; they are
read by the page only.

### `BUILD/groups/IMAGE.json`

Written by `dread groups` (since 2026-10-22; dread/groups.py load): the groups and the evidence they
were found from, so that a second look reads them instead of every function's facts. `{"version": 2,
"key", "resolution", "evidence", "groups": [[function names in address order], ...] (numbered by their
place, which is the order of their first functions' addresses), "touched": {name: [[kind, key], ...]}}`,
the evidence `things()` gives a function: `global` (an address), `device` (the block's number: its
address over DEVICE_BLOCK), `text` and `table` (an address), `taken` (another function's entry).
`evidence` says where it was read from: `listing` (the machine code, dread.relations flow_references,
the default) or `readable` (the readable C, dread.relations Facts; `dread groups --evidence readable`).
The key is a digest of every Python module of DREAD, regflow's key, the target's dread.toml, each table
of the image (absent ones too), the resolution, the weights and the evidence's source: any change of
these makes the groups anew; a file with another key or version is not used (version 1, before the
evidence had a choice, was always the readable C's: such a file is found anew). One record an image:
the last one found. `dread groups --fresh` finds them anew whatever is kept.

## The whole-program graph (`dread groups --json`, `dread gui`'s `/api/program`)

`{"groups": [...], "between": [...], "functions": [...], "calls": [...], "refers": [...], "shared": [...],
"ran": [...], "counts": {"functions", "groups", "calls", "refers", "between", "shared", "ran"}}`
(dread/groups.py program_graph, since 2026-10-22; `shared` and `ran` since 2026-10-23). The GUI's
answer has `groups_from` (`kept`: read from BUILD/groups; `dread groups`: found now) and `status`, and
an `origin` on each function; while the groups are being found it is
`{"waiting": "callers" or "groups", "seconds", "status"}`, and `{"error", "status"}` when dread groups
failed.

- A group: `group` (its number), `label` and `from` (what named it: `pilot`, the pilot's subsystem name
  for exactly these functions, by their `members` key in subsystems.tsv; `prefix`, the name prefix at
  least a third of its functions share when no other group's is the same; `function`, the name of its
  only function; `number`, `group N`), `name`, `prefix`, `what` and `confidence` (the pilot's, else
  null; `prefix` is the shared prefix where the pilot gave none), `common` (`{"prefix", "functions"}`
  or null), `functions` (how many), `bytes`, `first` and `last` (entries), `members` (the key) and
  `naming` (`{"named", "proposed", "unnamed"}`: how many of its functions each); since 2026-10-23
  `certainty` (`{level: how many of its functions}`, every level of the relations graph's
  `certainty`), `marks` (`{mark: how many of its functions}`, the review's marks they carry),
  `name_certainty` (the certainty of the pilot's name for it by the vote over its prefix, null when it
  is not the pilot's) and `review` (the review's `standing` of that name, as a function's, or null).
- Between two groups `a` < `b`: `calls` (`[a to b, b to a]`: pairs of a caller and a callee),
  `refers` (`[a to b, b to a]`: functions whose address the other group's code makes), `ran` (since
  2026-10-23, `[a to b, b to a]`: pairs joined by the system run's transfers no call shows) and `shared`
  (`{kind: how many}` of the things both groups touch, of the kinds the grouping shares, none that more
  than MAX_SHARED functions touch). A pair with none of these is not listed.
- A function: `name`, `entry`, `size`, `group`, `naming` (as the relations graph's) and `proposed` (the
  pilot's name) where there is one; since 2026-10-23 `certainty`, and `confidence`, `review` and
  `entered_by` (without `scenarios`) where there are, as the relations graph's.
- `calls`: `[caller, callee, call sites]`, `refers`: `[function, function whose address it makes]`,
  each function by its number in `functions` (address order).
- `ran` (since 2026-10-23): `[function, function it entered, how, times]` of the system run's records'
  transfers no call shows (`pointer`, `pointer-jump`, `return`, `falls`: the relations graph's
  `calls-pointer` and `enters`), by number; `counts` has `ran`, how many. A record without it (older,
  or no system run recorded) has none.
- `shared` (since 2026-10-23): `[kind, key, [functions]]`, each thing the grouping shares (`global`,
  `device`, `text`, `table`, keyed as BUILD/groups' evidence) that functions of two groups or more
  touch, at most MAX_SHARED of them, with those functions by number: what `between`'s `shared` counts,
  down to the functions (dread gui joins an open group's functions to them). `counts` has `shared`, how
  many. A record without it (older) has only the counts between groups.

Everything is in a fixed order (groups by number, functions by address, pairs by their numbers), so
the same tables give the same record.

### `BUILD/readable-check/IMAGE/verdicts.tsv`

Written by `dread readable --check`: one line per function of the lifted table (or per name it was
given), `function`, `verdict` (`readable`: built from readable C; `lifted`: it stays lifted; `not
lifted`: a name not in the lifted table), `reason` (why it stays lifted, in the gate's words: the
system run accepted it, it no longer lifts, its lifted C is not the dialect dread.cparse reads, the
overlay's refusal or lack of room, its readable C does not compile or calls what the original does
not, the FAIL or UNCOVERED line of its cases, the whole image differs with it; a FAIL line may name a
refusal that did not explain every difference since 2026-10-24, as in the lift check's). Since
2026-10-24 `its readable C does not compile` is followed by the compiler's first error, as the round's
output line has it too: `: NAME.c:LINE:COLUMN: error: MESSAGE | CODE` (dread.build compiler_error: the
file by name, the code the compiler quotes, cut as every reason is, to 300 characters here and 200 in
the output); a reason without it (older, or a compiler that named no error) is the words alone.
`dread bench`'s readable step counts the reasons by category (dread.bench readable_reason, by their
start).

### Figures of readable C (`dread readable --figures FILE`) and `BUILD/work/readable.tsv`

`dread readable --figures FILE` writes JSON, `{function: {"readable": {...}, "lifted": {...},
"kept_lifted": bool}}`, for the readable table's functions (or those named): `readable` the figures
of its readable C as built (dread.cmetrics READABLE: `statements`, `depth`, `gotos`, `labels`,
`loops`, `casts`, `raw`, `frame_raw`, `members`, `frame_members`, `fixed`, `fixed_members`,
`variables`, `typed`, and since 2026-10-23 `fixed_indexed`, `fixed_addresses`, `pointers` and
`signed`; what each counts is in dread.cmetrics' docstring), `lifted` the first five of its lifted C,
`kept_lifted` whether its readable C is its lifted C (fixed registers: no stage ran). A function
that does not lift or is not the dialect is left out. `dread bench`'s readable step reads it and
writes `BUILD/work/readable.tsv` in a bench build: one line per readable function, `function` and
the columns of dread.bench TABLE_COLUMNS (its figures, its lifted C's statements and gotos, how it
was matched to its source function and that function's figures, the ratios, the functions inlined
into it; since 2026-10-23 `pointers` and `signed` at the end), `-` where there is none. Columns may
be added at the end; a reader takes them by the
header's names.

### Printed readable C (`dread readable [--plain] [--out DIR]`) and the header `readable_types.h`

Since 2026-10-23 what `dread readable` prints is not the file the builds compile. The compiled form
(`BUILD/recon/IMAGE/lifted/NAME.c` for a function of the readable table, `BUILD/readable-check/...`)
defines in each function's file every structure it uses; the printed form leaves the image's
structures to one header and includes it:

- `readable_types.h`, written beside the files by `--out DIR` (removed there when the image has no
  structures) and printed by `--header`: a guard (`READABLE_TYPES_H`), `#include "dread.h"`, a
  declaration `struct NAME;` for every structure a pointer field names, then every structure of the
  `readable_types` table, those behind pointers (`structs`, a held one before its holder) and those of
  static data (`statics` and `arrays`' elements, tag `NAME_t`; none for an array of scalars), named by
  the `readable_names` table, each definition the same text the compiled form has. Made from the
  tables when it is written; no tool reads it.
- `NAME.c` (or the output of `dread readable NAME`): the compiled form with the structures' definitions
  and declarations taken out, `#include "readable_types.h"` after `#include "dread.h"` where there
  were any, one comment a structure naming the members the function uses (`/* struct s_9: u32 f_0 at
  0x0, struct s_24 *f_10 at 0x10 */`), and the first comment saying it is readable C (`/* NAME at
  0xADDR: readable C from its lifted C (dread readable) */`). Its frame's structure (`stackN_t`), its
  extern declarations and its macros (`#define g_...`) stay in it. Compiled with the header beside it
  (and dread.h), it is the compiled form's code.
- With `--plain`, the reading form: each line behind a margin with an address in hex (blank where the
  line has none), the instructions' comments and their notes left out, and dread.build's lines for the
  compiler (`/* dread: ... */`). A statement's address is its first instruction (dread.cparse
  first_addresses: the lowest of its own and of those whose statements the clean-up folded into it
  alone); a do-while's is on its `while` line. Not C to compile as it stands (its margin).
