# Vortex Disk Controller ROM Decryption

## Aim

The Vortex disk controller (circa 1986) contains a Z80 ROM that is hardware-encrypted. The controller includes decryption logic that decodes each opcode byte as it is fetched from ROM, meaning the raw ROM image cannot be disassembled directly — the bytes on the chip are not valid Z80 instructions until they pass through the hardware decryption.

The goal is to implement the equivalent of this hardware decryption in software within z80dasm, so that the ROM contents are decoded during the disassembly process — before each opcode is interpreted. This allows the encrypted ROM image to be disassembled into readable Z80 assembly without requiring a separate preprocessing step.

## Background

- The ROM has been released into the public domain by Vortex.
- No source code or commented listing exists for the ROM.
- The decryption is performed per-byte at opcode fetch time by hardware on the controller board.

## Requirements

- Decode ROM bytes during the memory read/fetch stage of disassembly, before opcode interpretation.
- The decryption must be applied as part of the normal disassembly flow, not as a separate pre-pass on the ROM file.
- The decoding routine should match the behaviour of the original Vortex controller hardware.

## The ~M1 Signal and Decryption Logic

The Vortex hardware uses the Z80 CPU's **~M1** line to determine whether a byte needs decryption:

- **M1 cycle (opcode fetch):** The byte is read as-is — no decryption applied.
- **Non-M1 cycle (memory read):** The byte must be decrypted before use.

In other words: **opcodes are stored in the clear; operands and data are encrypted.**

The disassembler must track an **M1 flag** that reflects which cycle the CPU would be in at each point during instruction decoding.

## References

- [Z80 Instruction Timing](https://floooh.github.io/2021/12/06/z80-instruction-timing.html) — detailed breakdown of every instruction set's memory cycles and M1 usage.

## Z80 Instruction Fetch Behaviour

### Terminology

- **Opcode:** The first byte of an instruction (always fetched during an M1 cycle).
- **Memory read:** Any subsequent byte of the instruction (operands, addresses, data) — fetched during non-M1 cycles.

### Non-Prefixed (Main) Instructions

```
Byte 1: Opcode fetch     → M1 cycle   → NOT decrypted
Byte 2: Memory read      → non-M1     → DECRYPTED
Byte 3: Memory read      → non-M1     → DECRYPTED
```

The opcode byte determines how many additional memory-read bytes follow (0, 1, or 2).

### Prefixed Instructions (CB, ED)

The Z80 has three instruction subsets, each occupying 256 opcode slots:

1. **Main set** — unprefixed, largely overlaps with the Intel 8080 set.
2. **CB set** — bit manipulation instructions, full 256 entries.
3. **ED set** — extended instructions, only 59 of 256 slots used.

The DD and FD prefixes are not counted as separate subsets — they modify the main set by replacing HL with IX or IY.

Prefix bytes (CB, DD, ED, FD) execute as regular 4-cycle instructions, but:
- No interrupts are handled at the end of the prefix cycle.
- The following byte is fetched as another M1 cycle (a new opcode fetch).
- ED cancels any active DD/FD effect.

This means prefixed instructions have the following fetch pattern:

```
Byte 1: Prefix fetch     → M1 cycle   → NOT decrypted
Byte 2: Opcode fetch     → M1 cycle   → NOT decrypted
Byte 3: Memory read      → non-M1     → DECRYPTED
Byte 4: Memory read      → non-M1     → DECRYPTED
```

### Summary Rule

| Byte type | M1? | Decrypt? |
|-----------|-----|----------|
| Non-prefix opcode (1st byte) | Yes | No |
| Prefix byte (CB/DD/ED/FD) | Yes | No |
| Opcode after prefix | Yes | No |
| All subsequent bytes (operands/data) | No | Yes |

## Integration with z80dasm

The decoding routine itself is a pure function of address and data byte — it has no knowledge of M1. The M1 logic must be handled externally during the opcode decoding process.

### How z80dasm Reads Data

**Buffer:** `int t[6]` (dz80.c:51) — a sliding window of 6 bytes read from the input file.

**`shiftin()`** (dz80.c:59-79) shifts the buffer left by one position and reads the next byte from the file into `t[5]`. After the initial fill (`for(i=0;i<T_SIZE;i++) shiftin()`), `t[0]` always holds the current opcode byte.

**Disassembly loop** (dz80.c:1133-1147, repeated for all 3 passes):
```
do {
    for(i=0; i<T_SIZE; i++) shiftin();    /* pre-fill buffer */
    while(1) {
        pci = disassemble();               /* decode instruction */
        if(pci == 0) break;
        for(i=0; i<pci; i++) shiftin();   /* advance by instruction length */
        pc += pci;
    }
} while(blk_iterate());
```

**`disassemble()`** (dz80.c:983-1002) dispatches based on block type. For code blocks it calls `diz80_code()` → `diz80()`.

### How diz80() Decodes Instructions

The main decoder `diz80()` (dz80.c:207-791) works directly on the buffer `t[]`:

- **`t[0]`** is always the first opcode byte (M1 — no decode needed).
- **Prefix CB** (line 219): reads `t[1]` as the second opcode byte (M1 — no decode). Returns 2.
- **Prefix ED** (line 242): reads `t[1]` as second opcode byte (M1 — no decode). May read `t[2]`, `t[3]` as operands (need decode). Returns 2-4.
- **Prefix DD/FD** (line 381): reads `t[1]` as second opcode byte (M1 — no decode). May read `t[2]`, `t[3]`, `t[4]` as operands (need decode). Returns 2-4.
  - **DD/FD CB** (line 514): reads `t[1]`=CB (M1), `t[2]`=displacement (need decode), `t[3]`=operation. Note: `t[3]` here is the bit-op code after the CB prefix — see implementation notes below. Returns 4.
- **Main set** (line 732): uses `comtab[t[0]]` lookup table. May read `t[1]` (1-byte operand, need decode) or `t[1]`+`t[2]` (2-byte operand, both need decode).

**Word reads:** `ckrange()` (dz80.c:88-135) reads 16-bit values as `t[skip] + 256 * t[skip+1]`. Both bytes are operands and need decoding. A `decode_word` wrapper calling `decode_byte` twice (with address and address+1) would apply here.

### Where to Apply Decoding

The decoding should be applied inside `diz80()` at the point where operand bytes are consumed from the buffer. The M1 determination is implicit in the structure of the decoder:

| Buffer access | Context | M1? | Decode? |
|--------------|---------|-----|---------|
| `t[0]` | Always the first opcode | Yes | No |
| `t[1]` after CB/ED/DD/FD prefix | Second opcode byte | Yes | No |
| `t[1]` in main set (non-prefix) | Operand byte | No | **Yes** |
| `t[2]` after ED prefix | Operand | No | **Yes** |
| `t[2]`, `t[3]` after DD/FD prefix | Displacement + operand | No | **Yes** |
| `t[2]` in DD/FD CB | Displacement | No | **Yes** |
| `t[3]` in DD/FD CB | Bit-op code after CB | See note | **See note** |
| `t[1]`, `t[2]` in main set (type 2/12) | 16-bit operand | No | **Yes** |

**DD/FD CB note:** The instruction format is `DD CB dd op` — the Z80 fetches the displacement `dd` as a memory read (non-M1, needs decode) and the final operation byte `op` also as a memory read (non-M1, needs decode). Unlike regular CB-prefixed instructions, the byte after CB in DD/FD CB sequences is NOT fetched with M1.

### Data and Pointer Blocks

Data blocks are never executed by the CPU — there is no M1 cycle. Every byte in a data or pointer block needs decoding.

| Block type | Handler | Bytes read | Decode? |
|-----------|---------|------------|---------|
| `bytedata` | `diz80_bytedata()` (dz80.c:910) | `t[0]` | **Yes** — all bytes |
| `worddata` | `diz80_worddata()` (dz80.c:917) | `t[0]`, `t[1]` | **Yes** — both bytes |
| `pointers` | `diz80_pointers()` (dz80.c:928) | `t[0]`, `t[1]` | **Yes** — both bytes |
| `cpc_faraddr` | `diz80_cpc_faraddr()` (dz80.c:947) | `t[0]`, `t[1]`, `t[0]` (next call) | **Yes** — all three bytes |

These are dispatched from `disassemble()` (dz80.c:983) based on `shiftin_blk->type`. The decode check should be applied in each of these handlers (or in `disassemble()` before dispatch for non-code block types).

### Implementation Approach: Decode at Point of Use (Option C)

The decode is applied by wrapping each operand byte with a `decode_byte(address, byte)` call at the exact point where it is consumed. This means:

- The decode call sits right next to the code that uses the value.
- Each call is self-documenting — you see which byte and at what address.
- No restructuring of the existing code flow.
- No duplicated logic.
- Easy to audit — grep for `decode_byte` to find every decode point.

All calls are guarded by the `a_decode_vortex` flag. When the flag is off, bytes pass through unchanged (the function is never called, or returns the byte as-is).

#### The decode_byte function

Added to a new file `src/decode.c` (with `src/decode.h`):

```c
/* decode.h */
#ifndef DECODE_H
#define DECODE_H

#include <stdint.h>

uint8_t decode_byte(uint16_t address, uint8_t databyte);

#endif /* DECODE_H */

/* decode.c */
#include "decode.h"
#include "dz80.h"   /* for a_decode_vortex */

uint8_t decode_byte(uint16_t address, uint8_t databyte)
{
    uint8_t mask = 0;

    if (!a_decode_vortex)
        return databyte;

    if (address & 0x04)   /* A2 set → flip D5 */
        mask = 0x20;
    if (address & 0x10)   /* A4 set → flip D3 */
        mask |= 0x08;

    return databyte ^ mask;
}
```

The `a_decode_vortex` check is inside the function itself, so callers never need to guard the call. When decoding is disabled, the byte passes through unchanged.

#### Code blocks: changes to diz80() (dz80.c)

Each operand byte access is wrapped with `decode_byte()`. The caller computes the address as `pc + offset`. The first opcode byte `t[0]` and prefix follow-up bytes (M1 fetches) are never decoded.

**Main set (non-prefixed), lines 732-791:**
```c
/* case 1: 8-bit immediate operand */
col_print("0%02xh", decode_byte(pc + 1, t[1]));

/* case 2: 16-bit operand (via ckrange) — see ckrange section below */

/* case 3: relative jump or 8-bit immediate */
/* JR/DJNZ: ckrange_rel handles t[1] — see ckrange section below */
/* non-jump: */
col_print("0%02xh", decode_byte(pc + 1, t[1]));

/* case 11: 8-bit operand with suffix */
col_print("0%02xh%s", decode_byte(pc + 1, t[1]), comtab[t[0]].com2);

/* case 12: 16-bit address with suffix — handled via ckrange */
```

**CB prefix (lines 219-241):**
```c
/* t[0] = 0xCB, t[1] = opcode — both M1, no decode needed */
a = t[1];  /* no decode — this is an M1 fetch */
```

**ED prefix (lines 242-380):**
```c
/* t[0] = 0xED, t[1] = opcode — both M1, no decode needed */
a = t[1];  /* no decode — this is an M1 fetch */

/* t[2], t[3] if present are operands — need decode */
/* these go through ckrange() — see ckrange section below */
```

**DD/FD prefix (lines 381-731):**
```c
/* t[0] = DD/FD, t[1] = opcode — both M1, no decode needed */

/* t[2] = displacement or operand — needs decode */
/* t[3], t[4] if present — need decode */
/* example: indexed memory operations */
col_print("(%s+0%02xh)", stri, decode_byte(pc + 2, t[2]));
```

**DD/FD CB (lines 514-645):**
```c
/* t[0] = DD/FD (M1), t[1] = CB (M1) — no decode for either */
/* t[2] = displacement — needs decode */
/* t[3] = bit-op code — needs decode (non-M1 in this sequence) */
decode_byte(pc + 2, t[2])  /* displacement */
decode_byte(pc + 3, t[3])  /* operation byte */
```

#### Helper functions: ckrange and ckrange_rel

These functions read operand bytes from `t[]` and resolve them to symbols or values. The decode must be applied inside these functions, since they are the point of use for the operand bytes:

**`ckrange()` (dz80.c:88-135) — 16-bit operand:**
```c
/* currently: val = t[skip] + 256 * t[skip+1]; */
/* becomes:  */
val = decode_byte(pc + skip, t[skip]) + 256 * decode_byte(pc + skip + 1, t[skip+1]);
```

**`ckrange_rel()` (dz80.c:141-182) — relative jump operand:**

This function reads `t[skip]` in several places. Additionally, the Zilog-syntax fallback path (lines 166-177) uses hardcoded `t[1]` instead of `t[skip]` — this is a pre-existing quirk in the code, but both `t[skip]` and `t[1]` access operand bytes and need decoding:

```c
/* lines 147-153: uses t[skip] */
/* becomes: decode_byte(pc + skip, t[skip]) everywhere t[skip] appears */

/* lines 166-177: uses hardcoded t[1] */
/* becomes: decode_byte(pc + 1, t[1]) everywhere t[1] appears */
```

Note: `ckrange` and `ckrange_rel` use a `skip` parameter as offset into `t[]`, which also corresponds to the byte offset from `pc`. So `pc + skip` gives the correct address.

#### tosymtab() (table.c:31-154)

**This is a critical decode point.** The `tosymtab()` function is called during pass 1 to extract jump/call target addresses and register them in the symbol table. It duplicates the instruction decoding logic and reads operand bytes from `ia[]` (which is the `t[]` buffer passed as a parameter).

If operand bytes are not decoded here, **all symbol addresses will be wrong** — labels will point to incorrect locations throughout the disassembly.

The function accesses:
- `ia[0]` — opcode byte (M1, no decode needed)
- `ia[1]` — second byte: M1 for ED/DD/FD prefixes, operand otherwise (**conditional decode**)
- `ia[2]`, `ia[3]` — operand bytes (**always need decode**)

```c
/* Non-prefixed instructions: ia[1] and ia[2] are operands */
/* e.g. line 45: argval = ia[1] + 256 * ia[2]; */
argval = decode_byte(prc + 1, ia[1]) + 256 * decode_byte(prc + 2, ia[2]);

/* ED-prefixed: ia[1] is M1 (no decode), ia[2] and ia[3] are operands */
/* e.g. line 66: argval = ia[2] + 256 * ia[3]; */
argval = decode_byte(prc + 2, ia[2]) + 256 * decode_byte(prc + 3, ia[3]);

/* DD/FD-prefixed: ia[1] is M1 (no decode), ia[2] and ia[3] are operands */
/* e.g. line 78: argval = ia[2] + 256 * ia[3]; */
argval = decode_byte(prc + 2, ia[2]) + 256 * decode_byte(prc + 3, ia[3]);

/* JR/DJNZ: ia[1] is the relative offset operand */
/* e.g. line 100: argval = prc + ia[1] + 2; */
argval = prc + decode_byte(prc + 1, ia[1]) + 2;

/* RST: ia[1] and ia[2] are operands */
/* e.g. line 132: argval = ia[1] + 256 * ia[2]; */
argval = decode_byte(prc + 1, ia[1]) + 256 * decode_byte(prc + 2, ia[2]);
```

Note: `tosymtab()` uses `prc` (passed as parameter) instead of the global `pc`. Both hold the same value — the current instruction address.

#### rst3_peek_far_addr() (dz80.c:796-814)

This function reads 3 bytes directly from the file (not from `t[]`) using `fgetc()` at a computed file offset. The bytes are data (a far address pointer) and need decoding:

```c
/* currently: */
*call_addr = b0 + 256 * b1;
*rom_sel   = b2;

/* becomes: */
*call_addr = decode_byte(ptr, b0) + 256 * decode_byte(ptr + 1, b1);
*rom_sel   = decode_byte(ptr + 2, b2);
```

#### Data and pointer blocks

Every byte in data/pointer blocks needs decoding — there are no M1 cycles.

**`diz80_bytedata()` (dz80.c:910-915):**
```c
col_print("defb 0%02xh", decode_byte(pc, t[0]));
```

**`diz80_worddata()` (dz80.c:917-926):**
```c
col_print("defw 0%04xh", decode_byte(pc, t[0]) + 256 * decode_byte(pc + 1, t[1]));
```

**`diz80_pointers()` (dz80.c:928-945):**
```c
/* ckrange(0, bstr) reads t[0] and t[1] — decoded inside ckrange */
/* symbol_newref also uses t[0]+256*t[1] — needs decoded values */
symbol_newref(decode_byte(pc, t[0]) + 256 * decode_byte(pc + 1, t[1]), pc, cstdfw);
```

**`diz80_cpc_faraddr()` (dz80.c:947-968):**

This function is called twice for a 3-byte far address object. The first call emits the 16-bit target (returning 2), the second emits the ROM select byte (returning 1). All bytes are pure data:

```c
/* first call — 16-bit target: */
col_print("defb 0%02xh", decode_byte(pc, t[0]));           /* fallback single byte */
col_print("defw 0%04xh", decode_byte(pc, t[0]) + 256 * decode_byte(pc + 1, t[1]));  /* word */

/* second call — ROM select byte (pc has advanced by 2): */
col_print("defb 0%02xh", decode_byte(pc, t[0]));           /* single byte again */
```

#### Summary of all decode points

| Location | Call | Reason |
|----------|------|--------|
| `diz80()` main set cases 1, 3, 11 | `decode_byte(pc + 1, t[1])` | 8-bit operand |
| `ckrange()` | `decode_byte(pc + skip, t[skip])` | 16-bit operand (cases 2, 12, ED, DD/FD, pointers) |
| `ckrange_rel()` | `decode_byte(pc + skip, t[skip])`, `decode_byte(pc + 1, t[1])` | Relative jump offset (both `t[skip]` and hardcoded `t[1]` paths) |
| `diz80()` DD/FD displacement | `decode_byte(pc + 2, t[2])` | Index displacement |
| `diz80()` DD/FD CB | `decode_byte(pc + 2, t[2])`, `decode_byte(pc + 3, t[3])` | Displacement + bit-op |
| `tosymtab()` (table.c) | `decode_byte(prc + N, ia[N])` | Symbol address extraction — all operand bytes (ia[1]-ia[3] depending on instruction) |
| `rst3_peek_far_addr()` | `decode_byte(ptr + N, bN)` | Reads 3 bytes directly from file via fgetc() |
| `diz80_rst()` | `decode_byte(pc + 1, t[1])`, `decode_byte(pc + 2, t[2])` | RST 16-bit operand |
| `defb()` | `decode_byte(pc + i, t[i])` | Fallback raw byte output |
| `diz80_bytedata()` | `decode_byte(pc, t[0])` | Data byte |
| `diz80_worddata()` | `decode_byte(pc, t[0])`, `decode_byte(pc + 1, t[1])` | Data word |
| `diz80_pointers()` | via `ckrange()` + `symbol_newref()` | Pointer word |
| `diz80_cpc_faraddr()` | `decode_byte(pc, t[0])`, `decode_byte(pc + 1, t[1])` | Far address (3 bytes over 2 calls) |

### Enabling via Command Line

The existing command-line parser (cmdline.c:162-272) uses `getopt_long()` with both short and long options.

A new general-purpose `--flags` option is added for boolean on/off features. Each flag is a simple name; passing it enables it. Multiple flags can be provided as comma-separated values or by repeating the option.

Usage: `z80dasm --flags=decode-vortex rom.bin`

```c
/* in cmdline.c — new global */
int a_decode_vortex = 0;

/* in longopts[] */
{ "flags", required_argument, NULL, 'f' },

/* in opts[] — add 'f:' */
const char opts[] = "alg:tu1S:s:r:o:hVvzb:cm:f:";

/* in switch(c) */
case 'f':
    if(!strcmp(optarg, "decode-vortex")) {
        a_decode_vortex = 1;
    } else {
        fprintf(stderr, "Error: unknown flag "
                "'%s' (valid: decode-vortex)\n", optarg);
        exit(1);
    }
    break;
```

The `--flags` option is designed to be extended with additional boolean flags in the future without requiring new long option names each time. New flags are simply added as further `strcmp` checks in the case handler.

The `a_decode_vortex` flag is declared `extern` in `dz80.h` so it is accessible from `diz80()`, `ckrange()`, `ckrange_rel()`, and the data block handlers. `decode_byte()` checks this flag internally and returns the byte unchanged when decoding is not enabled.

### Rules (from Z80 hardware behaviour)

- **M1 active (opcode/prefix fetch):** do not decode.
- **M1 not active (operand/data read):** call `decode_byte`.
- Prefix bytes (CB, DD, ED, FD) cause the following byte to also be fetched as M1 — so both the prefix and its following opcode byte are not decoded.

### Additional Decode Points

#### diz80_rst() (dz80.c:828-908)

The RST handler reads a 16-bit operand from `t[1]` + `t[2]` (line 859: `operand = t[1] + 256 * t[2]`). RST is a single-byte opcode (M1), so both operand bytes need decoding:

```c
/* currently: operand = t[1] + 256 * t[2]; */
/* becomes: */
operand = decode_byte(pc + 1, t[1]) + 256 * decode_byte(pc + 2, t[2]);
```

The `ckrange(1, bstr)` call at line 867 also reads these bytes — already covered by decoding inside `ckrange()`.

#### defb() (dz80.c:190-196)

The `defb()` function outputs raw bytes when an instruction can't be decoded (e.g. truncated at end of block). These bytes are data, not opcodes, so they need decoding:

```c
/* currently: col_print("%c0%02xh", i?',':' ', t[i]); */
/* becomes: */
col_print("%c0%02xh", i?',':' ', decode_byte(pc + i, t[i]));
```

#### The --source hex dump (dz80.c:1263-1284)

When `--source` (`-t`) is enabled, pass 3 prints the raw hex bytes and ASCII of each instruction in a comment. A decision is needed:

- **Show original (encrypted) bytes:** The hex dump reflects what's actually in the ROM file. Useful for verification against the physical ROM.
- **Show decoded bytes:** The hex dump matches the disassembly output. More intuitive when reading.

Recommendation: show the **original (encrypted) bytes** — the hex dump is meant to show the file content, and the decoded values are already visible in the assembly output. This requires no changes to the `--source` code.

### Build System

The new `src/decode.c` and `src/decode.h` files must be added to `CMakeLists.txt`:

```cmake
set(SOURCES
    ...
    src/decode.c
    src/decode.h
    ...
)
```

### Header Includes

Files that call `decode_byte()` need to include the new header:

```c
#include "decode.h"
```

This applies to:
- `dz80.c` — contains `diz80()`, `ckrange()`, `ckrange_rel()`, `defb()`, `diz80_rst()`, and the data block handlers.

The `a_decode_vortex` extern declaration must be added to `dz80.h`:

```c
extern int a_decode_vortex;
```

### Help Text

The `syntax()` function in `cmdline.c` must be updated to document the new `--flags` option:

```c
printf("  -f  --flags=FLAG      Enable feature flags (valid: decode-vortex)\n");
```

### Three-Pass Consideration

The decode must be applied in **all three passes**, not just pass 3. The three passes are:

1. **Pass 1:** Calculates instruction addresses and registers jump targets in the symbol table. If operand bytes are not decoded, jump target addresses will be wrong.
2. **Pass 2:** Validates that symbol references align with instruction boundaries. Uses the same decoded addresses from pass 1.
3. **Pass 3:** Generates the final assembly output with resolved labels.

Since `decode_byte()` is called at the point of use inside `diz80()`, `ckrange()`, etc., and these functions are called identically in all three passes, the decode is automatically applied consistently across all passes. No pass-specific logic is needed.

## Decoding Routine

The hardware decryption is an XOR operation that conditionally flips two data bits based on two address-line bits. Only the low byte of the address is relevant.

### Bit Mapping

| Address bit | Controls | Effect when address bit is set |
|-------------|----------|-------------------------------|
| A2 (bit 2, mask 0x04) | D5 (bit 5, mask 0x20) | XOR D5 with 1 (flip bit 5) |
| A4 (bit 4, mask 0x10) | D3 (bit 3, mask 0x08) | XOR D3 with 1 (flip bit 3) |

### Algorithm

1. Take the low byte of the current address.
2. Build a mask:
   - If address bit A2 is set (address & 0x04), set mask bit D5 (0x20).
   - If address bit A4 is set (address & 0x10), set mask bit D3 (0x08).
3. XOR the data byte with the mask.
4. The result is the decoded byte.

### The Four Possible Transformations

Since only two address bits matter, there are exactly four cases:

| A4 | A2 | XOR mask | Bits flipped |
|----|----|----------|--------------|
| 0  | 0  | 0x00     | None         |
| 0  | 1  | 0x20     | D5           |
| 1  | 0  | 0x08     | D3           |
| 1  | 1  | 0x28     | D3, D5       |

The transformation repeats every 32 bytes (0x20) since A4 is the highest address bit used, and cycles through the four states in a pattern determined by the low 5 bits of the address.

### Properties

- The operation is its own inverse: applying it twice returns the original byte (XOR is self-reversing).
- At addresses where both A2 and A4 are clear (e.g. 0x00, 0x01, 0x02, 0x03), the mask is 0x00 and the byte passes through unchanged.
- Only bits D3 and D5 of the data are ever affected; all other bits are always passed through as-is.
- The decryption depends only on the address and the data byte — there is no state carried between bytes.

### Reference Implementation (TypeScript)

```typescript
function decode_byte(address: number, databyte: number): number {
    var mask: number = 0;
    var sadr: number = (address & 0xFF);  // low byte of address only

    if (sadr & 0x04) {   // A2 set?
        mask = 0x20;     // flip D5
    }
    if (sadr & 0x10) {   // A4 set?
        mask += 0x08;    // flip D3
    }

    return (databyte ^ mask);
}
```

### Equivalent C Implementation

```c
uint8_t decode_byte(uint16_t address, uint8_t databyte) {
    uint8_t mask = 0;

    if (address & 0x04)   /* A2 set → flip D5 */
        mask = 0x20;
    if (address & 0x10)   /* A4 set → flip D3 */
        mask |= 0x08;

    return databyte ^ mask;
}
```

Note: The full implementation (in the Integration section above) also includes the `a_decode_vortex` guard. This version shows only the core algorithm.
