# CPC firmware and AMSDOS variables (464 / 664 / 6128)

Known RAM variables of the firmware (OS) and of AMSDOS, the source of the variable names the
debugger shows (`tools/extract_memory_variables.py` generates
`src/cpcxray/debugger/variable_tables.py` from this file). BASIC's variables are not listed.

## Sources

- TD: *The Amstrad CPC Firmware Guide*, Bob Taylor and Thomas Defoe, 1992 (unofficial;
  `docs/Taylor_Defoe_Amstrad_firmware.pdf`), "Use of memory by the Operating System": 6128 and
  464 addresses. Most rows of section 1 come from here.
- Bread80: the TD firmware section transcribed and extended (`Includes/MemoryFirmware.txt` of
  https://github.com/Bread80/CPC6128-Firmware-Source); rows marked "Bread80" add its findings.
- CPCWiki, *AMSDOS Memory Map* (`docs/AMSDOS Memory Map - CPCWiki.html`): sections 2 and 3.
- The ROMs: the 664 column is not documented anywhere. It was derived by aligning the code of
  `os6128.rom` and `os664.rom` (same instructions, compare the RAM addresses they use):
  227 addresses matched, all equal except the kernel's ROM variables - the 664 lacks the 6128's
  RAM bank number at #B8D5, so #B8D6-#B8F9 are one lower on the 664 (its ROM table also has
  16 entries: KL ROM WALK scans ROMs 15-0 on both). The same alignment against `os464.rom`
  confirms the TD 464 column (it corrected #B1F0: TD/Bread80 print #BB54 for #B554).
  `tests/machine/test_memory_variables.py` repeats both checks and reads known values after a
  boot.

## Conventions

- Addresses in `#` hex; `-` = the machine does not have the variable.
- Size in bytes; `14/15` = 14 on the 664/6128, 15 on the 464. `area` marks the variables of
  one firmware pack: it covers the rows up to the next area, so bytes without a known meaning
  still resolve to e.g. `CAS_WORKSPACE+1`.
- An address inside a variable is shown as `NAME+offset`; the smallest variable that contains
  it wins (e.g. `TXT_CURSOR_COLUMN` before `TXT_CURRENT_WINDOW+1`).
- Labels are upper-case snake case like the firmware jumpblock names, with the pack prefix
  (KL, KM, TXT, GRA, SCR, CAS, SOUND, MC, MATHS, EDIT, AMSDOS); they never repeat a jumpblock
  label.

## 1. Firmware variables

| 6128 | 664 | 464 | Size | Label | Description |
|---|---|---|---|---|---|
| #B100 | #B100 | #B8E4 | area | MATHS_WORKSPACE | maths pack variables |
| #B100 | #B100 | #B8E4 | 4 | MATHS_RND_SEED | random number state (initial &89656C07) |
| #B104 | #B104 | #B8E8 | 5 | MATHS_REAL_STORE_1 | internal real store 1 |
| #B109 | #B109 | #B8ED | 5 | MATHS_REAL_STORE_2 | internal real store 2 |
| #B10E | #B10E | #B8F2 | 5 | MATHS_REAL_STORE_3 | internal real store 3 |
| #B113 | #B113 | #B8F7 | 1 | MATHS_ANGLE_MODE | DEG/RAD flag (&00 = RAD, &FF = DEG) |
| #B114 | #B114 | #B8DC | area | EDIT_WORKSPACE | line editor (TEXT INPUT) variables |
| #B115 | #B115 | #B8DD | 1 | EDIT_INSERT_MODE | insert/overwrite mode flag |
| #B116 | #B116 | #B8DE | 1 | EDIT_COPY_CURSOR_X | copy cursor x, relative to the window origin |
| #B117 | #B117 | #B8DF | 1 | EDIT_COPY_CURSOR_Y | copy cursor y, relative to the window origin |
| #B118 | #B118 | #B800 | area | CAS_WORKSPACE | cassette manager variables |
| #B118 | #B118 | #B800 | 1 | CAS_MESSAGES_OFF | cassette messages (&00 = enabled, other = disabled) |
| #B11A | #B11A | #B802 | 1 | CAS_IN_STATE | input file state (&00 closed, &02 IN file, &03 opened, &05 IN char) |
| #B11B | #B11B | #B803 | 2 | CAS_IN_BUFFER | address of the 2K buffer for directories |
| #B11D | #B11D | #B805 | 2 | CAS_IN_LOAD_BUFFER | address of the 2K buffer for loading blocks (often as CAS_IN_BUFFER) |
| #B11F | #B11F | #B807 | 64 | CAS_IN_HEADER | header of the input file |
| #B11F | #B11F | #B807 | 16 | CAS_IN_FILENAME | filename, padded with NULs |
| #B12F | #B12F | #B817 | 1 | CAS_IN_BLOCK_NUMBER | number of the block being loaded or next to load |
| #B130 | #B130 | #B818 | 1 | CAS_IN_LAST_BLOCK | last block flag (&FF = last block) |
| #B131 | #B131 | #B819 | 1 | CAS_IN_FILE_TYPE | file type (&00 BASIC, &01 protected BASIC, &02 binary, &08 screen, &16 ASCII) |
| #B132 | #B132 | #B81A | 2 | CAS_IN_BLOCK_LENGTH | length of this block |
| #B134 | #B134 | #B81C | 2 | CAS_IN_LOAD_ADDRESS | address to load this or the next block at |
| #B136 | #B136 | #B81E | 1 | CAS_IN_FIRST_BLOCK | first block flag (&FF = first block) |
| #B137 | #B137 | #B81F | 2 | CAS_IN_FILE_LENGTH | total length of the file |
| #B139 | #B139 | #B821 | 2 | CAS_IN_EXECUTION_ADDRESS | execution address of a binary file |
| #B15F | #B15F | #B847 | 1 | CAS_OUT_STATE | output file state (as CAS_IN_STATE) |
| #B160 | #B160 | #B848 | 2 | CAS_OUT_NEXT_BLOCK | address the next block is saved from (OPENOUT: the buffer) |
| #B162 | #B162 | #B84A | 2 | CAS_OUT_SAVED_BLOCK | start of the last block saved (OPENOUT: the buffer) |
| #B164 | #B164 | #B84C | 64 | CAS_OUT_HEADER | header of the output file (as CAS_IN_HEADER) |
| #B164 | #B164 | #B84C | 16 | CAS_OUT_FILENAME | filename |
| #B174 | #B174 | #B85C | 1 | CAS_OUT_BLOCK_NUMBER | number of the block being saved or next to save |
| #B175 | #B175 | #B85D | 1 | CAS_OUT_LAST_BLOCK | last block flag (&FF = last block) |
| #B176 | #B176 | #B85E | 1 | CAS_OUT_FILE_TYPE | file type (as CAS_IN_FILE_TYPE) |
| #B177 | #B177 | #B85F | 2 | CAS_OUT_LENGTH_SAVED | length saved so far |
| #B179 | #B179 | #B861 | 2 | CAS_OUT_START_ADDRESS | start of the area to save (OPENOUT: the buffer) |
| #B17B | #B17B | #B863 | 1 | CAS_OUT_FIRST_BLOCK | first block flag (&FF = first block) |
| #B17C | #B17C | #B864 | 2 | CAS_OUT_FILE_LENGTH | total length of the file to save |
| #B17E | #B17E | #B866 | 2 | CAS_OUT_EXECUTION_ADDRESS | execution address of a binary file (&0000 if none) |
| #B1A4 | #B1A4 | #B88C | 64 | CAS_HEADER_WORK | used to construct the input file header |
| #B1E5 | #B1E5 | #B8CD | 1 | CAS_SYNC_BYTE | synchronisation byte |
| #B1E9 | #B1E9 | #B8D1 | 1 | CAS_PRECOMPENSATION | write precompensation (default &06; SPEED WRITE 1: &0C; 4 us units) |
| #B1EA | #B1EA | #B8D2 | 1 | CAS_HALF_ZERO | half a zero bit duration (default &53; SPEED WRITE 1: &29; 4 us units) |
| #B1EB | #B1EB | #B8D3 | 2 | CAS_WORKSPACE_END | last word of the cassette variables (meaning unknown) |
| #B1ED | #B1ED | #B550 | area | SOUND_WORKSPACE | sound manager variables |
| #B1F8 | #B1F8 | #B55C | 63 | SOUND_CHANNEL_A | channel A (1) data |
| #B212 | #B212 | #B576 | 1 | SOUND_A_QUEUED | number of sounds still queued on channel A |
| #B213 | #B213 | #B577 | 1 | SOUND_A_QUEUED_START | number of sounds originally queued on channel A |
| #B217 | #B217 | #B57B | 8 | SOUND_A_QUEUE_1 | 1st (or 5th) queued sound: status, envelopes, pitch, noise, volume, duration |
| #B21F | #B21F | #B583 | 8 | SOUND_A_QUEUE_2 | 2nd queued sound (as SOUND_A_QUEUE_1) |
| #B227 | #B227 | #B58B | 8 | SOUND_A_QUEUE_3 | 3rd queued sound |
| #B22F | #B22F | #B593 | 8 | SOUND_A_QUEUE_4 | 4th queued sound |
| #B237 | #B237 | #B59B | 63 | SOUND_CHANNEL_B | channel B (2) data (as SOUND_CHANNEL_A) |
| #B256 | #B256 | #B5BA | 8 | SOUND_B_QUEUE_1 | 1st (or 5th) queued sound |
| #B25E | #B25E | #B5C2 | 8 | SOUND_B_QUEUE_2 | 2nd queued sound |
| #B266 | #B266 | #B5CA | 8 | SOUND_B_QUEUE_3 | 3rd queued sound |
| #B26E | #B26E | #B5D2 | 8 | SOUND_B_QUEUE_4 | 4th queued sound |
| #B276 | #B276 | #B5DA | 63 | SOUND_CHANNEL_C | channel C (4) data (as SOUND_CHANNEL_A) |
| #B295 | #B295 | #B5F9 | 8 | SOUND_C_QUEUE_1 | 1st (or 5th) queued sound |
| #B29D | #B29D | #B601 | 8 | SOUND_C_QUEUE_2 | 2nd queued sound |
| #B2A5 | #B2A5 | #B609 | 8 | SOUND_C_QUEUE_3 | 3rd queued sound |
| #B2AD | #B2AD | #B611 | 8 | SOUND_C_QUEUE_4 | 4th queued sound |
| #B2B6 | #B2B6 | #B61A | 240 | SOUND_ENV_TABLE | ENV parameter blocks 1-15, 16 bytes each |
| #B2B6 | #B2B6 | #B61A | 16 | SOUND_ENV_1 | ENV 1 |
| #B2C6 | #B2C6 | #B62A | 16 | SOUND_ENV_2 | ENV 2 |
| #B2D6 | #B2D6 | #B63A | 16 | SOUND_ENV_3 | ENV 3 |
| #B2E6 | #B2E6 | #B64A | 16 | SOUND_ENV_4 | ENV 4 |
| #B2F6 | #B2F6 | #B65A | 16 | SOUND_ENV_5 | ENV 5 |
| #B306 | #B306 | #B66A | 16 | SOUND_ENV_6 | ENV 6 |
| #B316 | #B316 | #B67A | 16 | SOUND_ENV_7 | ENV 7 |
| #B326 | #B326 | #B68A | 16 | SOUND_ENV_8 | ENV 8 |
| #B336 | #B336 | #B69A | 16 | SOUND_ENV_9 | ENV 9 |
| #B346 | #B346 | #B6AA | 16 | SOUND_ENV_10 | ENV 10 |
| #B356 | #B356 | #B6BA | 16 | SOUND_ENV_11 | ENV 11 |
| #B366 | #B366 | #B6CA | 16 | SOUND_ENV_12 | ENV 12 |
| #B376 | #B376 | #B6DA | 16 | SOUND_ENV_13 | ENV 13 |
| #B386 | #B386 | #B6EA | 16 | SOUND_ENV_14 | ENV 14 |
| #B396 | #B396 | #B6FA | 16 | SOUND_ENV_15 | ENV 15 |
| #B3A6 | #B3A6 | #B70A | 240 | SOUND_ENT_TABLE | ENT parameter blocks 1-15, 16 bytes each |
| #B3A6 | #B3A6 | #B70A | 16 | SOUND_ENT_1 | ENT 1 |
| #B3B6 | #B3B6 | #B71A | 16 | SOUND_ENT_2 | ENT 2 |
| #B3C6 | #B3C6 | #B72A | 16 | SOUND_ENT_3 | ENT 3 |
| #B3D6 | #B3D6 | #B73A | 16 | SOUND_ENT_4 | ENT 4 |
| #B3E6 | #B3E6 | #B74A | 16 | SOUND_ENT_5 | ENT 5 |
| #B3F6 | #B3F6 | #B75A | 16 | SOUND_ENT_6 | ENT 6 |
| #B406 | #B406 | #B76A | 16 | SOUND_ENT_7 | ENT 7 |
| #B416 | #B416 | #B77A | 16 | SOUND_ENT_8 | ENT 8 |
| #B426 | #B426 | #B78A | 16 | SOUND_ENT_9 | ENT 9 |
| #B436 | #B436 | #B79A | 16 | SOUND_ENT_10 | ENT 10 |
| #B446 | #B446 | #B7AA | 16 | SOUND_ENT_11 | ENT 11 |
| #B456 | #B456 | #B7BA | 16 | SOUND_ENT_12 | ENT 12 |
| #B466 | #B466 | #B7CA | 16 | SOUND_ENT_13 | ENT 13 |
| #B476 | #B476 | #B7DA | 16 | SOUND_ENT_14 | ENT 14 |
| #B486 | #B486 | #B7EA | 16 | SOUND_ENT_15 | ENT 15 |
| #B496 | #B496 | #B34C | area | KM_WORKSPACE | key manager variables |
| #B496 | #B496 | #B34C | 80 | KM_NORMAL_TABLE | key translation table, no shift (80 keys) |
| #B4E6 | #B4E6 | #B39C | 80 | KM_SHIFT_TABLE | key translation table with SHIFT |
| #B536 | #B536 | #B3EC | 80 | KM_CONTROL_TABLE | key translation table with CONTROL |
| #B586 | #B586 | #B43C | 10 | KM_REPEAT_TABLE | key repeat table, one bit per key (set = repeats) |
| #B590 | #B590 | #B446 | 152 | KM_EXPANSION_AREA | expansion strings of keys &80-&9F (DEF KEY / KEY): a length byte and the characters each |
| #B628 | #B628 | #B4DE | 2 | KM_EXPANSION_STATE | expansion being read: index and key (read and written as a pair; Bread80) |
| #B62A | #B62A | #B4E0 | 1 | KM_RETURNED_CHAR | character put back by KM CHAR RETURN (&FF = none; Bread80) |
| #B62B | #B62B | #B4E1 | 2 | KM_EXPANSION_START | address of the expansion area |
| #B62D | #B62D | #B4E3 | 2 | KM_EXPANSION_END | address after the end of the expansion area |
| #B631 | #B631 | #B4E7 | 1 | KM_SHIFT_LOCK | shift lock (&00 off, &FF on) |
| #B632 | #B632 | #B4E8 | 1 | KM_CAPS_LOCK | caps lock (&00 off, &FF on) |
| #B633 | #B633 | #B4E9 | 1 | KM_REPEAT_PERIOD | key repeat period (SPEED KEY; default &02, 1/50 s) |
| #B634 | #B634 | #B4EA | 1 | KM_REPEAT_DELAY | key repeat start delay (SPEED KEY; default &1E, 1/50 s) |
| #B635 | #B635 | #B4EB | 10 | KM_KEY_STATE | keyboard matrix lines 0-9 as scanned, one bit per key |
| #B63F | #B63F | #B4F5 | 10 | KM_KEY_STATE_COMPLEMENT | complement of KM_KEY_STATE |
| #B657 | #B657 | #B50D | 7 | KM_EVENT_BLOCK | keyboard event block (chain, count, class, routine, ROM) |
| #B65E | #B65E | #B514 | 40 | KM_KEY_BUFFER | last keys pressed, 20 entries of 2 bytes (line + SHIFT/CONTROL bits, column) |
| #B686 | #B686 | #B53C | 1 | KM_BUFFER_FREE | free entries in the key buffer + 1 |
| #B687 | #B687 | #B53D | 1 | KM_BUFFER_HEAD | index of the last key put into the key buffer |
| #B688 | #B688 | #B53E | 1 | KM_BUFFER_USED | keys in the key buffer + 1 |
| #B689 | #B689 | #B53F | 1 | KM_BUFFER_TAIL | index of the last key read from the key buffer |
| #B68A | #B68A | #B540 | 1 | KM_BUFFER_KEYS | number of keys in the key buffer |
| #B68B | #B68B | #B541 | 2 | KM_NORMAL_TABLE_ADDRESS | address of the normal key table |
| #B68D | #B68D | #B543 | 2 | KM_SHIFT_TABLE_ADDRESS | address of the shifted key table |
| #B68F | #B68F | #B545 | 2 | KM_CONTROL_TABLE_ADDRESS | address of the control key table |
| #B691 | #B691 | #B547 | 2 | KM_REPEAT_TABLE_ADDRESS | address of the key repeat table |
| #B693 | #B693 | #B328 | area | GRA_WORKSPACE | graphics VDU variables |
| #B693 | #B693 | #B328 | 2 | GRA_ORIGIN_X | origin x |
| #B695 | #B695 | #B32A | 2 | GRA_ORIGIN_Y | origin y |
| #B697 | #B697 | #B32C | 2 | GRA_CURSOR_X | graphics cursor x (user coordinates) |
| #B699 | #B699 | #B32E | 2 | GRA_CURSOR_Y | graphics cursor y (user coordinates) |
| #B69B | #B69B | #B330 | 2 | GRA_WINDOW_X1 | graphics window: x of one edge (base coordinates) |
| #B69D | #B69D | #B332 | 2 | GRA_WINDOW_X2 | graphics window: x of the other edge |
| #B69F | #B69F | #B334 | 2 | GRA_WINDOW_Y1 | graphics window: y of one edge |
| #B6A1 | #B6A1 | #B336 | 2 | GRA_WINDOW_Y2 | graphics window: y of the other edge |
| #B6A3 | #B6A3 | #B338 | 1 | GRA_PEN | graphics pen (encoded) |
| #B6A4 | #B6A4 | #B339 | 1 | GRA_PAPER | graphics paper (encoded) |
| #B6A5 | #B6A5 | #B33A | 13 | GRA_LINE_WORK | work area of the line drawing routines (layout differs on the 464) |
| #B6B2 | #B6B2 | - | 1 | GRA_FIRST_POINT | plot the first point of a line (<>0 = plot; GRA SET FIRST) |
| #B6B3 | #B6B3 | - | 1 | GRA_LINE_MASK | line mask (GRA SET LINE MASK) |
| #B6B5 | #B6B5 | #B20C | area | TXT_WORKSPACE | text VDU variables |
| #B6B5 | #B6B5 | #B20C | 1 | TXT_STREAM | current stream (window) number |
| #B6B6 | #B6B6 | #B20D | 14/15 | TXT_STREAM_0 | saved parameters of stream 0 (layout as TXT_CURRENT_WINDOW) |
| #B6C4 | #B6C4 | #B21C | 14/15 | TXT_STREAM_1 | saved parameters of stream 1 (layout as TXT_CURRENT_WINDOW) |
| #B6D2 | #B6D2 | #B22B | 14/15 | TXT_STREAM_2 | saved parameters of stream 2 (layout as TXT_CURRENT_WINDOW) |
| #B6E0 | #B6E0 | #B23A | 14/15 | TXT_STREAM_3 | saved parameters of stream 3 (layout as TXT_CURRENT_WINDOW) |
| #B6EE | #B6EE | #B249 | 14/15 | TXT_STREAM_4 | saved parameters of stream 4 (layout as TXT_CURRENT_WINDOW) |
| #B6FC | #B6FC | #B258 | 14/15 | TXT_STREAM_5 | saved parameters of stream 5 (layout as TXT_CURRENT_WINDOW) |
| #B70A | #B70A | #B267 | 14/15 | TXT_STREAM_6 | saved parameters of stream 6 (layout as TXT_CURRENT_WINDOW) |
| #B718 | #B718 | #B276 | 14/15 | TXT_STREAM_7 | saved parameters of stream 7 (layout as TXT_CURRENT_WINDOW) |
| #B726 | #B726 | #B285 | 14/15 | TXT_CURRENT_WINDOW | parameters of the current stream |
| #B726 | #B726 | #B285 | 1 | TXT_CURSOR_ROW | cursor line on the whole screen (0 = top) |
| #B727 | #B727 | #B286 | 1 | TXT_CURSOR_COLUMN | cursor column on the whole screen (0 = left) |
| #B729 | #B729 | #B288 | 1 | TXT_WINDOW_TOP | window top line on the whole screen |
| #B72A | #B72A | #B289 | 1 | TXT_WINDOW_LEFT | window left column on the whole screen |
| #B72B | #B72B | #B28A | 1 | TXT_WINDOW_BOTTOM | window bottom line on the whole screen |
| #B72C | #B72C | #B28B | 1 | TXT_WINDOW_RIGHT | window right column on the whole screen |
| #B72D | #B72D | #B28C | 1 | TXT_SCROLL_COUNT | scroll count |
| #B72E | #B72E | #B28D | 1 | TXT_CURSOR_FLAGS | cursor flags (&01 disable, &02 off, &FD on, &FE enable) |
| #B72F | #B72F | #B28F | 1 | TXT_PEN | text pen (encoded) |
| #B730 | #B730 | #B290 | 1 | TXT_PAPER | text paper (encoded) |
| #B731 | #B731 | #B291 | 2 | TXT_BACKGROUND_ROUTINE | address of the background routine (opaque or transparent) |
| #B733 | #B733 | #B293 | 1 | TXT_GRAPHICS_CHARACTERS | write characters at the graphics cursor (<>0 = on) |
| #B734 | #B734 | #B294 | 1 | TXT_UDG_FIRST | first character of the user defined matrix table |
| #B735 | #B735 | #B295 | 1 | TXT_UDG_PRESENT | user defined matrix table present (&00 no, &FF yes) |
| #B736 | #B736 | #B296 | 2 | TXT_UDG_TABLE | address of the user defined matrix table |
| #B763 | #B763 | #B2C3 | 96 | TXT_CONTROL_TABLE | control code table, 32 entries: parameter count (+&80: run at reset) and routine address |
| #B7C3 | #B7C3 | #B1C8 | area | SCR_WORKSPACE | screen pack variables |
| #B7C3 | #B7C3 | #B1C8 | 1 | SCR_MODE | screen mode |
| #B7C4 | #B7C4 | #B1C9 | 2 | SCR_OFFSET | screen offset |
| #B7C6 | #B7C6 | #B1CB | 1 | SCR_BASE | screen base, high byte |
| #B7C7 | #B7C7 | #B1CC | 3 | SCR_WRITE_INDIRECTION | jump to the graphics write mode routine |
| - | - | #B1CF | 8 | SCR_BIT_MASKS | bytes with one bit set, b7 down to b0 |
| #B7D2 | #B7D2 | #B1D7 | 1 | SCR_FLASH_PERIOD_1 | first flash period (SPEED INK; default &0A, 1/50 s) |
| #B7D3 | #B7D3 | #B1D8 | 1 | SCR_FLASH_PERIOD_2 | second flash period (SPEED INK; default &0A, 1/50 s) |
| #B7D4 | #B7D4 | #B1D9 | 17 | SCR_INKS_1 | first inks of the border and pens 0-15 (hardware colour numbers) |
| #B7E5 | #B7E5 | #B1EA | 17 | SCR_INKS_2 | second inks of the border and pens 0-15 |
| #B7F6 | #B7F6 | #B1FB | 1 | SCR_INK_SET | ink set shown (&00 first, &FF second; Bread80) |
| #B7F7 | #B7F7 | #B1FC | 1 | SCR_INKS_CHANGED | &FF when an ink was changed, cleared when the palette is set (Bread80) |
| #B7F8 | #B7F8 | #B1FD | 1 | SCR_FLASH_COUNTER | frames until the ink sets swap (Bread80) |
| #B7F9 | #B7F9 | #B1FE | 9 | SCR_FLYBACK_EVENT | frame flyback event block of the ink swap (Bread80) |
| #B802 | #B802 | - | 1 | SCR_SAVED_GRA_PEN | graphics pen saved during SCR VERTICAL / SCR HORIZONTAL (Bread80) |
| #B803 | #B803 | - | 1 | SCR_SAVED_LINE_MASK | line mask saved during SCR VERTICAL / SCR HORIZONTAL (Bread80) |
| #B804 | #B804 | - | area | MC_WORKSPACE | machine pack variables |
| #B804 | #B804 | - | 1 | MC_TRANSLATIONS | number of printer translations (normally 10) |
| #B805 | #B805 | - | 40 | MC_TRANSLATION_TABLE | printer translation table, 20 entries: screen code, printer code |
| #B82D | #B82D | #B100 | area | KL_WORKSPACE | kernel variables |
| #B832 | #B832 | #B105 | 2 | KL_SAVED_SP | stack pointer saved during interrupt handling |
| #B834 | #B834 | #B107 | 128 | KL_INTERRUPT_STACK | stack used during interrupt handling (from &B8B3 down; Taylor/Defoe gives &70 bytes) |
| #B8B4 | #B8B4 | #B187 | 4 | KL_TIME | elapsed time in 1/300 s (KL TIME PLEASE), low byte first |
| #B8BD | #B8BD | #B190 | 2 | KL_TICKER_CHAIN | address of the first ticker block |
| #B8BF | #B8BF | #B192 | 1 | KL_SCAN_FLAG | keyboard scan flag (&00 not needed, &01 needed) |
| #B8C0 | #B8C0 | #B193 | 2 | KL_EVENT_CHAIN | address of the first event block (pending events) |
| #B8C3 | #B8C3 | #B196 | 16 | KL_RSX_NAME | name of the last RSX command (bit 7 set on its last character) |
| #B8D3 | #B8D3 | #B1A6 | 2 | KL_ROM_CHAIN | address of the first ROM/RSX chaining block |
| #B8D5 | - | - | 1 | KL_RAM_BANK | RAM configuration selected by KL BANK SWITCH |
| #B8D6 | #B8D5 | #B1A8 | 1 | KL_UPPER_ROM | upper ROM status (e.g. its select number) |
| #B8D7 | #B8D6 | #B1A9 | 2 | KL_FOREGROUND_ENTRY | entry point of the foreground ROM (e.g. &C006 for BASIC) |
| #B8D9 | #B8D8 | #B1AB | 1 | KL_FOREGROUND_ROM | select number of the foreground ROM (0 = BASIC) |
| #B8DA | #B8D9 | #B1AA | 32/16 | KL_ROM_IY_TABLE | data area (IY) of each background ROM, ROM n at +2n (464: ROMs 1-7) |

## 2. AMSDOS, fixed block (all machines with AMSDOS)

AMSDOS keeps these at #BE40-#BE7F on every machine. They are valid only while AMSDOS is
installed (the debugger checks that its workspace block is in the firmware's ROM chain).

| Address | Size | Label | Description |
|---|---|---|---|
| #BE40 | area | AMSDOS_FIXED | AMSDOS variables at a fixed address (on a 464 without AMSDOS: stack) |
| #BE40 | 2 | AMSDOS_DPH_POINTER | address of the disk parameter headers of drives A and B (&A910) |
| #BE42 | 2 | AMSDOS_XDPB_POINTER | address of the extended disk parameter block of drive A (&A890) |
| #BE44 | 2 | AMSDOS_MOTOR_ON_TIME | motor spin-up delay in 1/50 s (&0032) |
| #BE46 | 2 | AMSDOS_MOTOR_OFF_TIME | motor spin-down delay in 1/50 s (&00FA) |
| #BE48 | 1 | AMSDOS_FORMAT_DELAY | delay for formatting (&AF) |
| #BE49 | 1 | AMSDOS_DELAY | delay in milliseconds, purpose unknown (&0F) |
| #BE4A | 1 | AMSDOS_SETTLE_TIME | track settle time in milliseconds (&0C) |
| #BE4B | 1 | AMSDOS_RESULT_COUNT | bytes received in the FDC result phase |
| #BE4C | 7 | AMSDOS_RESULT | FDC result phase bytes |
| #BE53 | 3 | AMSDOS_POSITION_1 | drive (HS/US), track, record |
| #BE56 | 3 | AMSDOS_POSITION_2 | drive (HS/US), track, sector |
| #BE59 | 1 | AMSDOS_RECORDS_PER_TRACK | records per track, or block mask + 1 (uncertain) |
| #BE5A | 3 | AMSDOS_POSITION_3 | drive (HS/US), track, record |
| #BE5E | 1 | AMSDOS_READ_WRITE_FLAG | read/write sector flag |
| #BE5F | 1 | AMSDOS_MOTOR_FLAG | motor on/off flag |
| #BE60 | 2 | AMSDOS_RECORD_BUFFER_POINTER | address of the 128-byte directory record buffer (&A930) |
| #BE62 | 2 | AMSDOS_SECTOR_BUFFER_POINTER | address of the 512-byte sector buffer (&A9B0) |
| #BE64 | 2 | AMSDOS_SAVED_SP | temporary stack pointer (&BFxx) |
| #BE66 | 1 | AMSDOS_RETRIES | number of retries (&10) |
| #BE67 | 6 | AMSDOS_TICKER_BLOCK | ticker block (chain, count, reload) |
| #BE6D | 7 | AMSDOS_EVENT_BLOCK | event block (chain, count, class, routine, ROM) |
| #BE74 | 1 | AMSDOS_TARGET | track or sector number wanted |
| #BE75 | 1 | AMSDOS_FDC_COMMAND | FDC command number |
| #BE76 | 2 | AMSDOS_SECTOR_BUFFER_POINTER_2 | address of the 512-byte sector buffer (&A9B0) |
| #BE78 | 1 | AMSDOS_MESSAGES_OFF | error messages (&00 enabled, other disabled) |
| #BE7D | 2 | AMSDOS_MEMORY_POOL | address of AMSDOS's workspace (&A700): the base of the next table |
| #BE7F | 1 | AMSDOS_HOOK | hook (&C9) |

## 3. AMSDOS workspace (relative to its base)

AMSDOS reserves this area at its ROM initialisation, normally at #A6FC-#ABFF; it moves down
when ROMs with higher numbers reserve RAM first. The addresses below are for the usual base
#A700; the debugger reads the actual base from AMSDOS_MEMORY_POOL (#BE7D) and applies the same
offsets. The RSX block lies 4 bytes below the base.

| Address (base #A700) | Size | Label | Description |
|---|---|---|---|
| #A6FC | area | AMSDOS_WORKSPACE | AMSDOS's workspace, reserved by its ROM initialisation |
| #A6FC | 4 | AMSDOS_RSX_BLOCK | RSX chaining block: next block (2), ROM select number, &00 |
| #A700 | 1 | AMSDOS_DEFAULT_DRIVE | default drive (&00 A, &01 B) |
| #A701 | 1 | AMSDOS_DEFAULT_USER | default user number |
| #A702 | 1 | AMSDOS_ACTIVE_DRIVE | active drive (&00 A, &01 B) |
| #A703 | 2 | AMSDOS_ACTIVE_DPH | address of the disk parameter header of the active drive |
| #A705 | 1 | AMSDOS_DEFAULT_DRIVE_OPEN | a file is open on the default drive |
| #A706 | 2 | AMSDOS_STACK_POINTER | stack pointer |
| #A708 | 36 | AMSDOS_IN_FCB | extended file control block of the input file (OPENIN) |
| #A708 | 1 | AMSDOS_IN_FCB_DRIVE | drive (&00 A, &01 B, &FF not open) |
| #A709 | 1 | AMSDOS_IN_FCB_USER | user number |
| #A70A | 8 | AMSDOS_IN_FCB_NAME | filename, padded with spaces |
| #A712 | 3 | AMSDOS_IN_FCB_EXTENSION | extension (bit 7: read-only, system) |
| #A715 | 1 | AMSDOS_IN_FCB_EXTENT | extent number (&00 = first directory entry of the file) |
| #A718 | 1 | AMSDOS_IN_FCB_RECORDS | records in the current extent |
| #A719 | 16 | AMSDOS_IN_FCB_BLOCKS | block numbers of the current extent |
| #A729 | 3 | AMSDOS_IN_FCB_RECORD_POSITION | number of records accessed so far |
| #A72C | 36 | AMSDOS_OUT_FCB | extended file control block of the output file (OPENOUT) |
| #A72C | 1 | AMSDOS_OUT_FCB_DRIVE | drive (&00 A, &01 B, &FF not open) |
| #A72D | 1 | AMSDOS_OUT_FCB_USER | user number |
| #A72E | 8 | AMSDOS_OUT_FCB_NAME | filename, padded with spaces |
| #A736 | 3 | AMSDOS_OUT_FCB_EXTENSION | extension (bit 7: read-only, system) |
| #A739 | 1 | AMSDOS_OUT_FCB_EXTENT | extent number (&00 = first directory entry of the file) |
| #A73C | 1 | AMSDOS_OUT_FCB_RECORDS | records in the current extent |
| #A73D | 16 | AMSDOS_OUT_FCB_BLOCKS | block numbers of the current extent |
| #A74D | 3 | AMSDOS_OUT_FCB_RECORD_POSITION | number of records accessed so far |
| #A750 | 74 | AMSDOS_IN_HEADER | file header of the input file |
| #A750 | 1 | AMSDOS_IN_HEADER_ACCESS_MODE | access mode (1 = character, 2 = direct) |
| #A751 | 2 | AMSDOS_IN_HEADER_BUFFER | address of the 2K buffer |
| #A753 | 2 | AMSDOS_IN_HEADER_CHAR_POINTER | address of the current character in the 2K buffer |
| #A755 | 16 | AMSDOS_IN_HEADER_NAME | user number and filename, padded with NULs |
| #A765 | 1 | AMSDOS_IN_HEADER_BLOCK_NUMBER | block number |
| #A766 | 1 | AMSDOS_IN_HEADER_LAST_BLOCK | last block flag |
| #A767 | 1 | AMSDOS_IN_HEADER_FILE_TYPE | file type (&00 BASIC, &01 protected BASIC, &02 binary) |
| #A768 | 2 | AMSDOS_IN_HEADER_DATA_LENGTH | data length |
| #A76A | 2 | AMSDOS_IN_HEADER_LOAD_ADDRESS | load address |
| #A76C | 1 | AMSDOS_IN_HEADER_FIRST_BLOCK | first block flag (&FF) |
| #A76D | 2 | AMSDOS_IN_HEADER_FILE_LENGTH | file length without the 128-byte header |
| #A76F | 2 | AMSDOS_IN_HEADER_ENTRY | entry address of a binary file |
| #A795 | 3 | AMSDOS_IN_HEADER_POSITION | file position in characters (24 bits), or the length of a non-ASCII file |
| #A798 | 2 | AMSDOS_IN_HEADER_CHECKSUM | checksum of bytes &05-&47 |
| #A79A | 74 | AMSDOS_OUT_HEADER | file header of the output file |
| #A79A | 1 | AMSDOS_OUT_HEADER_ACCESS_MODE | access mode (1 = character, 2 = direct) |
| #A79B | 2 | AMSDOS_OUT_HEADER_BUFFER | address of the 2K buffer |
| #A79D | 2 | AMSDOS_OUT_HEADER_CHAR_POINTER | address of the current character in the 2K buffer |
| #A79F | 16 | AMSDOS_OUT_HEADER_NAME | user number and filename, padded with NULs |
| #A7AF | 1 | AMSDOS_OUT_HEADER_BLOCK_NUMBER | block number |
| #A7B0 | 1 | AMSDOS_OUT_HEADER_LAST_BLOCK | last block flag |
| #A7B1 | 1 | AMSDOS_OUT_HEADER_FILE_TYPE | file type (&00 BASIC, &01 protected BASIC, &02 binary) |
| #A7B2 | 2 | AMSDOS_OUT_HEADER_DATA_LENGTH | data length |
| #A7B4 | 2 | AMSDOS_OUT_HEADER_LOAD_ADDRESS | load address |
| #A7B6 | 1 | AMSDOS_OUT_HEADER_FIRST_BLOCK | first block flag (&FF) |
| #A7B7 | 2 | AMSDOS_OUT_HEADER_FILE_LENGTH | file length without the 128-byte header |
| #A7B9 | 2 | AMSDOS_OUT_HEADER_ENTRY | entry address of a binary file |
| #A7DF | 3 | AMSDOS_OUT_HEADER_POSITION | file position in characters (24 bits), or the length of a non-ASCII file |
| #A7E2 | 2 | AMSDOS_OUT_HEADER_CHECKSUM | checksum of bytes &05-&47 |
| #A7E4 | 128 | AMSDOS_RECORD_BUFFER | temporary record and filename buffer |
| #A864 | 39 | AMSDOS_CAS_VECTORS | the 13 cassette jumpblock entries AMSDOS replaced (#BC77-#BC9D) |
| #A88B | 3 | AMSDOS_FAR_ADDRESS | far address used by the patched cassette entries (RST 3) |
| #A890 | 64 | AMSDOS_XDPB_A | extended disk parameter block of drive A, with checksums and allocation table |
| #A8D0 | 64 | AMSDOS_XDPB_B | extended disk parameter block of drive B |
| #A910 | 16 | AMSDOS_DPH_A | disk parameter header of drive A |
| #A920 | 16 | AMSDOS_DPH_B | disk parameter header of drive B |
| #A930 | 128 | AMSDOS_DIRECTORY_BUFFER | directory record buffer |
| #A9B0 | 512 | AMSDOS_SECTOR_BUFFER | sector buffer |
| #ABB0 | 80 | AMSDOS_RESERVED | reserved |
