# Amstrad CPC RST Instructions

## Naming Convention

On the Amstrad CPC the restart instructions are referred to as **RST 0 .. RST 7** rather than the standard Z80 mnemonics RST #00 .. RST #38. The table below lists both forms.

| CPC name | Z80 mnemonic | Opcode | CPC function name |
|----------|-------------|--------|-------------------|
| RST 0 | RST #00 | `0xC7` | RESET |
| RST 1 | RST #08 | `0xCF` | LOW JUMP |
| RST 2 | RST #10 | `0xD7` | SIDE CALL |
| RST 3 | RST #18 | `0xDF` | FAR CALL |
| RST 4 | RST #20 | `0xE7` | RAM LAM |
| RST 5 | RST #28 | `0xEF` | FIRM JUMP |
| RST 6 | RST #30 | `0xF7` | USER RESTART |
| RST 7 | RST #38 | `0xFF` | INTERRUPT |

---

## RST 0 — RESET (`0xC7`)

Resets the system as if the machine has just been powered on.

**Instruction size:** 1 byte (no following data).

---

## RST 1 — LOW JUMP (`0xCF`)

Jumps to a routine in the lower 16K of memory. Enables or disables the lower ROM as specified by the operand, jumps to the target address, and restores the original ROM state on return.

**Instruction size:** 3 bytes total.

```
Byte 0:  0xCF        RST 1 opcode
Byte 1:  lo          Low byte  } 16-bit "low address" (little-endian)
Byte 2:  hi          High byte }
```

### Low Address Format (16-bit, little-endian)

```
Bit 15..14  ROM state bits
Bit 13..0   Jump target address (range #0000..#3FFF)
```

| Bits 15..14 | ROM enable state before jump |
|-------------|------------------------------|
| `00` | Lower ROM enabled, upper ROM disabled |
| `01` | Lower ROM enabled, upper ROM enabled |
| `10` | Lower ROM disabled, upper ROM disabled |
| `11` | Lower ROM disabled, upper ROM enabled |

The firmware jumpblock uses LOW JUMPs extensively, requesting lower ROM to be enabled so that it is disabled at all other times.

---

## RST 2 — SIDE CALL (`0xD7`)

Calls a routine in an associated ROM. Allows calls between up to four associated ROMs without knowing their absolute ROM select addresses, provided the ROMs are installed adjacent to each other in order. Restores both ROM state and ROM select on return.

**Instruction size:** 3 bytes total.

```
Byte 0:  0xD7        RST 2 opcode
Byte 1:  lo          Low byte  } 16-bit "side address" (little-endian)
Byte 2:  hi          High byte }
```

### Side Address Format (16-bit, little-endian)

```
Bit 15..14  ROM selector (0..3, relative to current ROM)
Bit 13..0   Offset added to #C000 to give the actual routine address
```

The upper ROM is enabled and the lower ROM is disabled before the call. Both ROM state and ROM select are restored on return.

---

## RST 3 — FAR CALL (`0xDF`)

Calls a routine anywhere in memory (RAM or any ROM). The two bytes following the opcode are a **pointer** to a three-byte "far address" object, not the address itself. This indirection allows the ROM select byte to be patched at run time.

**Instruction size:** 3 bytes total.

```
Byte 0:  0xDF        RST 3 opcode
Byte 1:  lo          Low byte  } 16-bit pointer to a far address object
Byte 2:  hi          High byte }   (little-endian)
```

### Far Address Object (3 bytes, at the pointed-to location)

```
Byte 0:  lo          Low byte  } Actual address of routine to call
Byte 1:  hi          High byte }   (little-endian)
Byte 2:  sel         ROM select/state byte
```

### ROM Select/State Byte (`sel`)

| `sel` value | Effect |
|-------------|--------|
| 0 .. 251 | Select upper ROM at this ROM select address; enable upper ROM, disable lower ROM |
| 252 | Enable upper ROM, enable lower ROM |
| 253 | Enable upper ROM, disable lower ROM |
| 254 | Disable upper ROM, enable lower ROM |
| 255 | Disable upper ROM, disable lower ROM |

Both ROM state and ROM select are restored on return.

---

## RST 4 — RAM LAM (`0xE7`)

Reads the byte from RAM at the address held in the **HL** register. Both ROMs are disabled before the read so that RAM hidden beneath a ROM can be accessed. ROM state is restored afterwards.

**Instruction size:** 1 byte (no following data).

> Writing to a memory location always changes RAM regardless of ROM enable state; this instruction is only needed for reads.

---

## RST 5 — FIRM JUMP (`0xEF`)

Enables the lower ROM and jumps to the specified address. The lower ROM is disabled when the routine returns. The upper ROM state is left unchanged throughout.

**Instruction size:** 3 bytes total.

```
Byte 0:  0xEF        RST 5 opcode
Byte 1:  lo          Low byte  } 16-bit jump target address (little-endian)
Byte 2:  hi          High byte }
```

---

## RST 6 — USER RESTART (`0xF7`)

Available for user programs. It may be used to extend the instruction set (in the same style as the other restarts) or for any other purpose such as a debugger breakpoint.

**Instruction size:** 1 byte minimum (user-defined; see note below).

RAM locations **#0030..#0037** may be patched to gain control:

- If the lower ROM is **enabled** when RST 6 executes: the firmware saves the current ROM state to location `#002B`, disables the lower ROM, then jumps to `#0030` in RAM.
- If the lower ROM is **disabled**: the restart calls `#0030` as a normal Z80 RST call.

Because this RST is user-defined, the number of bytes following the opcode depends entirely on how the user has implemented it.

---

## RST 7 — INTERRUPT (`0xFF`)

Reserved for the interrupt handler. Must not be executed by a program.

**Instruction size:** 1 byte (no following data).

---

## Disassembler Output Format

The disassembler emits RST instructions with inline comments identifying the function name and decoding the operand. The exact format depends on the RST variant.

### RST 0 — RESET / RST 4 — RAM LAM / RST 6 — USER RESTART / RST 7 — INTERRUPT

Single-byte, no following data. The function name is appended as a comment:

```
rst #00			; RESET
rst #20			; RAM LAM
rst #30			; USER RESTART
rst #38			; INTERRUPT
```

### RST 1 — LOW JUMP

Two operand bytes follow, emitted as a raw `defw`. The comment decodes the ROM state bits and jump target:

```
rst #08			; LOW JUMP
defw 0XXXXh		;   to #XXXX [LR=X, UR=X]
```

Where `LR` and `UR` are 0 or 1 (bits 15 and 14 of the operand), and `#XXXX` is bits 13..0.

### RST 2 — SIDE CALL

Two operand bytes follow, emitted as a raw `defw`. The comment decodes the ROM slot and effective call address:

```
rst #10			; SIDE CALL
defw 0XXXXh		;   to #XXXX [slot X]
```

Where `slot` is bits 15..14 (0..3) and `#XXXX` is `#C000 + bits 13..0`.

### RST 3 — FAR CALL

Two operand bytes follow as a `defw`, with label substitution if a symbol exists. The disassembler peeks at the far address object pointed to and decodes both the call target and ROM select byte into the comment:

```
rst #18			; FAR CALL
defw label		;   to TARGET [ROM state]
```

ROM state comment format (from `decode_rom_sel()`):

| `sel` value | Comment |
|-------------|---------|
| 0 .. 251 | `ROM N` |
| 252 | `UR=on, LR=on` |
| 253 | `UR=on, LR=off` |
| 254 | `UR=off, LR=on` |
| 255 | `UR=off, LR=off` |

If the pointer is outside the ROM image range, the comment reads `(out of range)`.

### RST 5 — FIRM JUMP

Two operand bytes follow as a `defw`, with label substitution if a symbol exists. The comment shows the resolved jump target:

```
rst #28			; FIRM JUMP
defw label		;   to #XXXX
```

---

## Summary Table

| RST | Opcode | Function | Bytes | Following data |
|-----|--------|----------|-------|----------------|
| RST 0 | `0xC7` | RESET | 1 | — |
| RST 1 | `0xCF` | LOW JUMP | 3 | 16-bit low address: bits 15..14 = ROM state, bits 13..0 = target (#0000..#3FFF) |
| RST 2 | `0xD7` | SIDE CALL | 3 | 16-bit side address: bits 15..14 = ROM selector (0..3), bits 13..0 + #C000 = routine address |
| RST 3 | `0xDF` | FAR CALL | 3 | 16-bit **pointer** to a 3-byte far address object (2-byte target address + 1-byte ROM select) |
| RST 4 | `0xE7` | RAM LAM | 1 | — (operand is HL register) |
| RST 5 | `0xEF` | FIRM JUMP | 3 | 16-bit jump target address |
| RST 6 | `0xF7` | USER RESTART | 1+ | User-defined |
| RST 7 | `0xFF` | INTERRUPT | 1 | — |
