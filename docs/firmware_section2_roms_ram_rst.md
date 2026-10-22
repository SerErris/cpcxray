# CPC Firmware — ROMs, RAM and the Restart Instructions

Source: *SOFT 968 — The Amstrad 464/664/6128 Firmware Guide*, Section 2 (© 1986 Locomotive
Software Ltd and Amstrad Consumer Electronics plc). This file reproduces the content of Section 2
as background for cpcxray, with OCR errors corrected and the memory-map figure converted to a
table. Section references ("see section 10") point to the original guide.

The closing section "Notes for cpcxray" is not part of the guide; it relates the content to
`docs/design.md`.

---

## 2 ROMs, RAM and the Restart Instructions

The system has 32K of ROM and 64K of RAM in the Z80's 64K address space. To allow this the ROM can
be enabled or disabled as required. Additional expansion ROMs can be selected, giving up to 4128K
of program area.

All the Z80 restart instructions, except for one, have been reserved for system use. RST 1 to
RST 5 are used to extend the instruction set by implementing special call and jump instructions
that enable and disable ROMs. RST 6 is available to the user.

## 2.1 Memory Map

The memory map is complicated by the fact that into the Z80's address space of 64K bytes have been
squeezed 64K bytes of RAM, 32K bytes of ROM and provision for ROM expansion of up to 252 × 16K
(nearly 4M) bytes. The address space is divided as follows:

**RAM**

| Address range | Use |
|---|---|
| #C000–#FFFF | Default screen memory |
| #B100–#BFFF | Stack, firmware data and jumpblock |
| #AC00–#B0FF | Foreground data (upper) |
| #????–#ABFF | Background data (upper) — 504 bytes for AMSDOS |
| #????–#???? | Memory pool |
| #????–#???? | Background data (lower) |
| #0040–#???? | Foreground data (lower) |
| #0000–#003F | Firmware area |

**ROM**

| Address range | ROM |
|---|---|
| #C000–#FFFF | Upper ROMs (bank switched) |
| #0000–#3FFF | Lower ROM |

The boundaries marked #???? are not fixed:

- The sizes of the two background areas depend on the background ROMs fitted to the machine
  (see section 10).
- The upper foreground data area need not have its lower bound at #AC00, but this is the default
  setting (as used by BASIC).
- The lower foreground data area need only be reserved if it is needed (it is not used by BASIC
  and is set to zero length).
- The memory pool left between the background data areas is also for the foreground program to
  use (see section 10).

The 32K of on-board ROM is split into two sections which are handled separately. Henceforth these
are discussed as if they were separate ROMs. The firmware resides in the lower ROM. BASIC resides
in the upper ROM. This upper ROM is bank switched, so that up to 252 expansion ROMs (see
section 10) can replace it in the memory map.

## 2.2 ROM Selection

There are two mechanisms for switching ROMs in and out of the address space.

### a. ROM state

The upper and lower ROMs may be enabled and disabled separately.

- When the upper ROM is enabled, data read from addresses #C000–#FFFF is fetched from the ROM.
- When the lower ROM is enabled, data read from addresses #0000–#3FFF is fetched from the ROM.
- When the ROMs are disabled, data is fetched from RAM.

The ROM state does not affect writing, which always changes the contents of RAM.

### b. ROM select

Expansion ROMs are supported by switching the upper ROM area between ROMs. Expansion ROMs are
addressed by a separate ROM select address byte implemented in I/O space. ROM select addresses
are in the range 0–251, providing for up to 252 expansion ROMs.

When the machine is first turned on it selects ROM 0. This usually selects the on-board ROM, but
an expansion ROM may be fitted at this address, which pre-empts the on-board ROM. See section 10
for the use of expansion ROMs.

## 2.3 The Restart Instructions

The Kernel supports the store map in a number of ways. In particular, a variety of facilities are
provided to handle subroutine addresses extended to include ROM select and/or ROM state
information. Some of the restart instructions augment the existing Z80 instruction set; the
other restarts are reserved.

The firmware area between #0000 and #003F is set up so that the restarts operate whatever the
current ROM state is. The user should not alter the contents of this area except as indicated in
section 18. A fuller description of the restarts' operation is in section 18.

### a. The extended instruction set

**LOW JUMP (RST 1)**

RST 1 jumps to a routine in the lower 16K of memory. The two bytes following the restart are
assumed to be a *low address*, so RST 1 can be considered a three-byte instruction, rather like a
JP instruction.

The top 2 bits of the low address define the ROM enable/disable state required; the bottom
14 bits give the actual address (#0000–#3FFF) to jump to once the ROM state is set up. When the
routine returns, the ROM state is restored to its original setting.

The firmware jumpblock, through which firmware routines should be called, makes extensive use of
LOW JUMPs. These LOW JUMPs request the lower ROM to be enabled, so that the lower ROM may be
disabled except when the firmware is active.

**SIDE CALL (RST 2)**

RST 2 calls a routine in an associated ROM. It has a very specialised use: a foreground program
(see section 10) may require more than 16K of ROM. The side call mechanism allows calls between
two, three or four associated ROMs without reference to their actual ROM select addresses,
provided the ROMs are installed next to each other and in order.

The two bytes following the restart give the *side address* of the routine to call, so RST 2 can
be considered a three-byte instruction, rather like a CALL instruction. The top 2 bits of the side
address specify which of the four ROMs to select; the bottom 14 bits, added to #C000, give the
actual routine address. The upper ROM is enabled and the lower ROM disabled. Both the ROM state and
the ROM select are restored to their original settings when the routine returns.

**FAR CALL (RST 3)**

RST 3 calls a routine anywhere in memory, in RAM or in any ROM. The two bytes following the
restart are assumed to be the address of a *far address*. The far address is a three-byte object:

| Bytes | Content |
|---|---|
| 0–1 | Actual address of the routine to call |
| 2 | ROM select/state required |

The ROM select/state byte may take these values:

| Value | Effect |
|---|---|
| 0–251 | Select the upper ROM at this ROM select address. Enable the upper ROM, disable the lower ROM. |
| 252 | No change of ROM select. Enable upper ROM, enable lower ROM. |
| 253 | No change of ROM select. Enable upper ROM, disable lower ROM. |
| 254 | No change of ROM select. Disable upper ROM, enable lower ROM. |
| 255 | No change of ROM select. Disable upper ROM, disable lower ROM. |

The far address is not itself contained in the "instruction" but is pointed at. This is because
the ROM select address depends on the order in which the user has installed expansion ROMs and
must be established at run time.

Both the ROM state and the ROM select are restored to their original settings when the routine
returns.

**RAM LAM (RST 4)**

RST 4 reads the byte from RAM at the address given by HL. It disables both ROMs before reading and
restores the state afterwards. This "instruction" saves the user from having to put a read routine
into the central 32K of RAM to access RAM hidden under a ROM.

Writing to a memory location always changes the contents of RAM, whatever the ROM enable state.

**FIRM JUMP (RST 5)**

RST 5 turns on the lower ROM and jumps to a routine. The two bytes following the restart are the
address to jump to, so RST 5 can be considered a three-byte instruction, rather like a JP
instruction. The lower ROM is enabled before jumping to the routine and disabled when the routine
returns. The state of the upper ROM is left unchanged throughout.

### b. The other restarts

**RESET (RST 0)**

RST 0 resets the system as if the machine had just been turned on.

**USER RESTART (RST 6)**

RST 6 is available to the user. It could be used to extend the instruction set in the same way as
the other restarts, or for another purpose such as a breakpoint instruction in a debugger.

Locations #0030–#0037 in RAM may be patched to gain control of the restart:

- If the lower ROM is enabled when the restart is executed, the code in ROM at this address saves
  the current ROM state in location #002B, disables the lower ROM and jumps to location #0030 in
  RAM.
- If the lower ROM is disabled, the restart calls #0030 as normal for this Z80 restart
  instruction.

**INTERRUPT (RST 7)**

RST 7 is reserved for interrupts (see section 11). It must not be executed by a program.

## 2.4 RAM and the Firmware

The ROM state should be transparent to the user.

- If the current foreground program (see section 10) is in ROM, the normal ROM state is upper ROM
  enabled, lower ROM disabled.
- If the current foreground program is in RAM, the normal state is both ROMs disabled.

These states give the foreground program free access to the memory pool. When a firmware routine
is called, the lower ROM is enabled and the upper ROM is usually disabled. This gives the firmware
free access to the default screen memory (but not to all of the memory pool). When the firmware
routine returns, the ROM state is automatically restored.

The cases where the ROM state matters are:

### a. Stack

The hardware stack should never be below #4000, otherwise serious confusion occurs when the lower
ROM is enabled and the stack is used, for example when interrupts occur or the firmware is called.

Similarly, it is inadvisable to set the stack above #C000 unless it is certain that the upper ROM
is never enabled while the stack is in use.

The system provides a stack area immediately below #C000 which is over 256 bytes long. This should
be adequate for most purposes.

### b. Communication with the firmware

Most firmware routines take their parameters in registers. Some, however, use data areas in memory
to pass information.

- Most firmware routines that use data areas in memory read them directly, without RAM LAMs or
  the equivalent. These routines are affected by the ROM state and the ROM select: they read data
  from a ROM if the ROM is enabled and the routine is given a suitable address. (The jumpblock
  disables the upper ROM when the firmware is called.)
- Other firmware routines that use data areas in memory always read from RAM. They are unaffected
  by the ROM state and the ROM select. Their descriptions say so; other routines may be assumed to
  be affected by the ROM state.

In particular, the various data blocks used by the Kernel must lie in the central 32K of RAM for
the Kernel to be able to use them.

### c. Communication between upper ROMs

Programs in upper ROMs may call routines in other ROMs, using the various Kernel facilities. There
is no provision in the firmware, however, for a program in one ROM to access constants in another.

### Jumpblocks

The majority of firmware routines are called via the firmware jumpblock, which starts at #BB00 in
the firmware RAM area. The Kernel routines associated with the memory map are called via one of two
other jumpblock areas: the LOW area between #0000 and #003F, and the HIGH area starting at #B900.
All of these routines and jumpblocks are copied out of the lower ROM into the firmware RAM area
when the Kernel is initialised. Thus they work independently of the ROM state.

## 2.5 Bank Switching (CPC6128)

The ULA in the CPC6128 includes circuitry for bank switching 128K of RAM into the 64K memory map
described in section 2.1. The bank-switched RAM replaces the RAM in the memory map and behaves
exactly like it; in particular, it is hidden when a ROM is enabled.

The 128K of bank-switched RAM is split into eight 16K blocks, numbered 0–7. There are eight memory
organisations, also numbered 0–7, each of which switches a different set of four blocks into the
memory map at #0000–#3FFF, #4000–#7FFF, #8000–#BFFF and #C000–#FFFF. The user selects an
organisation by calling KL BANK SELECT.

| Organisation | Block at #0000 | Block at #4000 | Block at #8000 | Block at #C000 |
|---|---|---|---|---|
| 0 | 0 | 1 | 2 | 3 |
| 1 | 0 | 1 | 2 | 7 |
| 2 | 4 | 5 | 6 | 7 |
| 3 | 0 | 3 | 2 | 7 |
| 4 | 0 | 4 | 2 | 3 |
| 5 | 0 | 5 | 2 | 3 |
| 6 | 0 | 6 | 2 | 3 |
| 7 | 0 | 7 | 2 | 3 |

During EMS the CPC6128 selects organisation 0, which is the organisation normally associated with
the firmware. Blocks 0 and 2 contain firmware variables, firmware jumpblocks and the stack; all of
these must be in their correct places for the firmware to run.

The documentation of a number of firmware routines specifies that data blocks passed to them
should be in the central 32K of memory. In most cases it does not matter which blocks are switched
in there. However, the Kernel accesses data blocks passed to it (e.g. ticker blocks or RSX command
tables) at various times (e.g. during interrupts or event processing), and it has no control over
the bank switching at such times. It is up to the user to ensure that the Kernel is only passed data
blocks that remain accessible. The simplest solution is to locate all Kernel data blocks in block 2
(#8000–#BFFF).

- **Organisations 4–7** are the firmware organisation with a new block switched in at #4000. They
  can be used to access programs or data stored in blocks 4–7.
- **Organisations 1–2** are used by CP/M Plus and are not really suitable for general use. In
  particular, if organisation 2 is selected, a program must be patched into RAM at #0038 to catch
  interrupts and to bank switch back to a more normal organisation (e.g. organisation 1) to run
  the standard interrupt code.
- **Organisation 3** is also used by CP/M Plus, but it is of interest because it has the RAM
  usually used for the screen located at #4000, where it can be accessed without disabling the
  upper ROM.

Bank switching has no effect on the CRTC. Screen base addresses #0000, #4000, #8000 and #C000
correspond to the screen being in blocks 0, 1, 2 and 3 respectively; the screen cannot be located
in blocks 4–7. The firmware routines that access screen memory are, however, affected by bank
switching:

- If a base address of #C000 is set in organisation 3, the firmware must be told (using SCR SET
  POSITION) that the screen memory can be accessed at #4000.
- If a base address of #4000 is set in organisation 3, the firmware cannot access the screen
  memory at all, since block 1 is not in the memory map.

(See section 6.4 of the guide for the screen memory map.)

Organisations 4–7 can be used to build a complete screen in one go: use SCR SET POSITION to make
the firmware write to the memory at #4000 without sending a new base address to the screen
hardware; when the screen is finished, quickly copy the block into the block actually used by the
CRTC (with KL LDIR, for example). A title screen could be set up this way, banked out of the way,
and later switched back in and copied when needed.

---

## Notes for cpcxray (not part of the firmware guide)

- **ROM state and ROM select** correspond to the Gate Array RMR register (bit 2 lower ROM disable,
  bit 3 upper ROM disable) and the ROM select latch at &DFxx (`docs/design.md` §5.2, FR-MCH-02/03).
  The guide allows ROM select values 0–251; cpcxray implements slots 0–15 (D-09) and treats all
  other values as unpopulated (D-10).
- **Writes always go to RAM** whatever the ROM state (FR-MCH-06).
- **RST 1 bits 15–14:** the guide does not say which bit controls which ROM. cpcxray follows the
  464 OS ROM source (`docs/design.md` §5.6): bit 15 = upper ROM disable, bit 14 = lower ROM
  disable, 1 = disabled.
- **RST operands and far addresses** are read by the firmware handlers with ordinary data reads.
  For a scrambled ROM they are therefore always decoded (`docs/design.md` §5.5, §6.15).
- **LOW and HIGH Kernel jumpblocks** (#0000–#003F and from #B900) are copied from the lower ROM to
  RAM at Kernel initialisation, e.g. the RST 1 handler from ROM &0413 to RAM &B982. These are
  relocated blocks of the OS ROM; cpcxray does not analyse them, because the standard ROMs are not
  analysis targets (D-16).
- **Bank switching (§2.5)** is later scope (FR-MCH-10, D-14). The table above defines the per-16K
  page tables. On the hardware, an organisation is selected through Gate Array function 11
  (OUT &7Fxx with value &C0 + organisation); this is hardware knowledge, not stated in Section 2,
  and is to be verified when 6128 support is implemented.

---

## Compact spec (machine-oriented)

Dense restatement of everything above for low-token ingestion. Normative where it matches the
sections above; `[cpcxray]` marks project decisions, `[verify]` marks unconfirmed hardware facts.
Notation: `#`/`&` = hex; ranges inclusive; `RD` = memory read; `WR` = memory write; `LR`/`UR` =
lower/upper ROM; `en`=enabled `dis`=disabled.

```
MEM
 addr #0000-#3FFF RD: LR en ? LROM[addr] : RAM[addr]
 addr #4000-#BFFF RD: RAM[addr]
 addr #C000-#FFFF RD: UR en ? UROM[sel][addr-#C000] : RAM[addr]
 any addr WR: RAM[addr] (ROM state/select never affect WR)
 LROM=firmware 16K; UROM[0]=BASIC 16K (onboard); UROM[sel] sel=0..251 (guide) [cpcxray: 0..15, else ->0]
 power-on: sel=0; expansion ROM at sel 0 pre-empts onboard BASIC
RAM LAYOUT (default, BASIC)
 #0000-#003F firmware area (LOW kernel jumpblock + RST vectors; copied from LROM at kernel init)
 #0040-?     lower foreground data (len 0 under BASIC)
 ?-?         lower background data (size = sum of background ROM needs)
 ?-?         memory pool (foreground program)
 ?-#ABFF     upper background data (AMSDOS: 504 bytes)
 #AC00-#B0FF upper foreground data (lower bound default #AC00, movable)
 #B100-#BFFF stack (>256 bytes, top just below #C000), firmware data, jumpblocks
   #B900.. HIGH kernel jumpblock; #BB00.. main firmware jumpblock (copied from LROM)
 #C000-#FFFF default screen
ROM STATE NORMS
 foreground in ROM: UR en, LR dis
 foreground in RAM: UR dis, LR dis
 inside firmware call: LR en, UR usually dis (jumpblock entries are RST1 with UR dis); restored on return
 SP must be >= #4000; SP > #C000 only if UR never en while stack used
 kernel data blocks (tickers, RSX tables, ...) must be in #4000-#BFFF
 most fw routines read memory params directly (affected by ROM state/select); routines stating "RAM" read RAM always
 no fw provision for one UROM to read constants of another UROM
RST (opcode, size, inline bytes after opcode are RD non-M1 data, little-endian)
 RST0 #C7 1  RESET: full reset as power-on
 RST1 #CF 3  LOW JUMP  w=inline word; target=w&#3FFF; jump; ROM state set from w bits15-14; restore state on return
             [cpcxray, from OS ROM source] bit15=1 -> UR dis; bit14=1 -> LR dis
             w>>14: 0=LR en UR en | 1=LR dis UR en | 2=LR en UR dis | 3=LR dis UR dis
             firmware jumpblock uses RST1 with LR en, UR dis (bit15=1,bit14=0)
 RST2 #D7 3  SIDE CALL w=inline word; rom = base of the 4 associated adjacent ROMs (ascending) + (w>>14)
             [verify: base = foreground ROM select number]; addr=#C000+(w&#3FFF);
             UR en, LR dis; call; restore state+select on return
 RST3 #DF 3  FAR CALL  p=inline word = pointer to far address F (3 bytes, RD data): F[0..1]=routine addr, F[2]=s
             s 0..251: select UROM s, UR en, LR dis
             s 252: select unchanged, UR en,  LR en
             s 253: select unchanged, UR en,  LR dis
             s 254: select unchanged, UR dis, LR en
             s 255: select unchanged, UR dis, LR dis
             call F addr; restore state+select on return; F indirect so s can be patched at run time
 RST4 #E7 1  RAM LAM: read RAM[HL] with LR dis UR dis (result in A [verify, sec. 18]); restore state
 RST5 #EF 3  FIRM JUMP w=inline word=target; LR en; jump; LR dis on return; UR unchanged throughout
 RST6 #F7 1+ USER: if LR en: save ROM state -> RAM[#002B], LR dis, jp RAM #0030; else plain call #0030
             user patches RAM #0030-#0037; inline byte count user-defined
 RST7 #FF 1  INTERRUPT (IM1 handler); never executed by programs
 RST vectors #0000-#003F work in any ROM state (identical code in LROM and RAM copy)
6128 BANKING (organisation o=0..7 -> 16K block per page; blocks 0..7 of 128K RAM)
 o: #0000 #4000 #8000 #C000
 0:  0     1     2     3     (firmware default, selected at EMS)
 1:  0     1     2     7     (CP/M Plus)
 2:  4     5     6     7     (CP/M Plus; needs own IM1 handler at RAM #0038)
 3:  0     3     2     7     (CP/M Plus; screen block 3 visible at #4000)
 4:  0     4     2     3
 5:  0     5     2     3
 6:  0     6     2     3
 7:  0     7     2     3
 banked RAM replaces RAM in map; still hidden by enabled ROMs; WR goes to banked RAM
 blocks 0,2 hold fw vars, jumpblocks, stack -> must stay mapped for firmware; put kernel data blocks in block 2
 select via fw: KL BANK SELECT; [verify] hw: OUT &7Fxx, value #C0|o (GA function 11)
 CRTC ignores banking: screen base #0000/#4000/#8000/#C000 = block 0/1/2/3; blocks 4-7 never displayable
 fw screen access follows banking: tell fw via SCR SET POSITION where screen is mapped
```
