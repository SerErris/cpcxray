# CPC Firmware Jumpblocks

Source: *SOFT 968 — The Amstrad 464/664/6128 Firmware Guide*, sections 14–18 (© 1986 Locomotive
Software Ltd and Amstrad Consumer Electronics plc). Entry numbers, addresses and names are taken
from the entry headers of sections 15–18; the one-line descriptions are the guide's summary line of
each entry. Full entry/exit conditions are in the guide.

## Firmware versions and availability

| Machine | Firmware | Notes |
|---|---|---|
| CPC 464 | V1.0 | |
| CPC 664 | V1.1 | |
| CPC 6128 | V1.2 | Identical to V1.1 apart from bank-switching support; the guide calls both "V1.1". |

The guide states that all firmware calls are **upwards compatible**: every call available in V1.0
is available in V1.1 and V1.2, and every call available in V1.1 is available in V1.2. Consequently
**no entry is unique to the 464 or to the 664**:

| Availability | Meaning | Entries |
|---|---|---|
| `all` | 464, 664 and 6128 | main 0–189, all indirections, high and low Kernel jumpblock |
| `664/6128` | V1.1 and V1.2 only ("not available on V1.0 firmware") | main 190–200 (#BD3A–#BD58) |
| `6128` | V1.2 only ("only available on the CPC6128") | main 201 KL BANK SWITCH (#BD5B) |

Notes column:

- `AMSDOS patch` — marked with `*` in the guide (entries 125–137, #BC77–#BC9B): AMSDOS redirects
  these cassette entries to disc when it initialises; the external commands TAPE and DISC switch
  between the two versions.
- `V1.0 differs` — the entry exists on all machines, but section 15/16 documents different
  behaviour on V1.0 (e.g. error numbers returned in A only on V1.1, background write modes,
  ROM-number ranges). Check the guide before relying on V1.1 behaviour on a 464.

## 1. Main firmware jumpblock (#BB00–#BD5D)

Entry address = #BB00 + 3 × entry number. Each entry is initialised at EMS (and by JUMP RESTORE)
to a LOW JUMP (RST 1 + low address) that enables the lower ROM and disables the upper ROM. The
firmware itself does not call through this jumpblock, so patching an entry changes the behaviour
of callers (e.g. BASIC), not of the firmware.

| # | Address | Name | Pack | Avail | Notes | Description |
|---|---|---|---|---|---|---|
| 0 | #BB00 | KM INITIALISE | Key Manager | all |  | Initialize the Key Manager |
| 1 | #BB03 | KM RESET | Key Manager | all |  | Reset the Key Manager. |
| 2 | #BB06 | KM WAIT CHAR | Key Manager | all |  | Wait for next character from the keyboard. |
| 3 | #BB09 | KM READ CHAR | Key Manager | all |  | Test if a character is available from the keyboard. |
| 4 | #BB0C | KM CHAR RETURN | Key Manager | all |  | Return a single character to the keyboard for next time. |
| 5 | #BB0F | KM SET EXPAND | Key Manager | all |  | Set an expansion string. |
| 6 | #BB12 | KM GET EXPAND | Key Manager | all |  | Get a character from an expansion string. |
| 7 | #BB15 | KM EXP BUFFER | Key Manager | all |  | Allocate a buffer for expansion strings. |
| 8 | #BB18 | KM WAIT KEY | Key Manager | all |  | Wait for next key from the keyboard. |
| 9 | #BB1B | KM READ KEY | Key Manager | all |  | Test if a key is available from the keyboard. |
| 10 | #BB1E | KM TEST KEY | Key Manager | all |  | Test if a key is pressed. |
| 11 | #BB21 | KM GET STATE | Key Manager | all |  | Fetch Caps Lock and Shift Lock states. |
| 12 | #BB24 | KM GET JOYSTICK | Key Manager | all |  | Fetch current state of the joystick(s). |
| 13 | #BB27 | KM SET TRANSLATE | Key Manager | all |  | Set entry in normal key translation table. |
| 14 | #BB2A | KM GET TRANSLATE | Key Manager | all |  | Get entry from normal key translation table. |
| 15 | #BB2D | KM SET SHIFT | Key Manager | all |  | Set entry in shifted key translation table. |
| 16 | #BB30 | KM GET SHIFT | Key Manager | all |  | Get entry from shifted key translation table. |
| 17 | #BB33 | KM SET CONTROL | Key Manager | all |  | Set entry in control key translation table. |
| 18 | #BB36 | KM GET CONTROL | Key Manager | all |  | Get entry from control key translation table. |
| 19 | #BB39 | KM SET REPEAT | Key Manager | all |  | Set whether a key may repeat. |
| 20 | #BB3C | KM GET REPEAT | Key Manager | all |  | Ask if a key is allowed to repeat. |
| 21 | #BB3F | KM SET DELAY | Key Manager | all |  | Set start up delay and repeat speed. |
| 22 | #BB42 | KM GET DELAY | Key Manager | all |  | Get start up delay and repeat speed. |
| 23 | #BB45 | KM ARM BREAK | Key Manager | all |  | Allow break events to be generated. |
| 24 | #BB48 | KM DISARM BREAK | Key Manager | all |  | Prevent break events from being generated. |
| 25 | #BB4B | KM BREAK EVENT | Key Manager | all |  | Generate a break event (if armed). |
| 26 | #BB4E | TXT INITIALISE | Text VDU | all |  | Initialize the Text VDU. |
| 27 | #BB51 | TXT RESET | Text VDU | all |  | Reset the Text VDU. |
| 28 | #BB54 | TXT VDU ENABLE | Text VDU | all |  | Allow characters to be placed on the screen. |
| 29 | #BB57 | TXT VDU DISABLE | Text VDU | all | V1.0 differs | Prevent characters being placed on the screen. |
| 30 | #BB5A | TXT OUTPUT | Text VDU | all |  | Output a character or control code to the Text VDU. |
| 31 | #BB5D | TXT WR CHAR | Text VDU | all |  | Write a character to the screen. |
| 32 | #BB60 | TXT RD CHAR | Text VDU | all | V1.0 differs | Read a character from the screen |
| 33 | #BB63 | TXT SET GRAPHIC | Text VDU | all |  | Turn on or off the Graphics VDU write character option. |
| 34 | #BB66 | TXT WIN ENABLE | Text VDU | all |  | Set the size of the current text window. |
| 35 | #BB69 | TXT GET WINDOW | Text VDU | all |  | Get the size of the current window. |
| 36 | #BB6C | TXT CLEAR WINDOW | Text VDU | all |  | Clear current window. |
| 37 | #BB6F | TXT SET COLUMN | Text VDU | all |  | Set cursor horizontal position. |
| 38 | #BB72 | TXT SET ROW | Text VDU | all |  | Set cursor vertical position. |
| 39 | #BB75 | TXT SET CURSOR | Text VDU | all |  | Set cursor position. |
| 40 | #BB78 | TXT GET CURSOR | Text VDU | all |  | Ask current cursor position. |
| 41 | #BB7B | TXT CUR ENABLE | Text VDU | all |  | Allow cursor display - user. |
| 42 | #BB7E | TXT CUR DISABLE | Text VDU | all |  | Disallow cursor display - user. |
| 43 | #BB81 | TXT CUR ON | Text VDU | all |  | Allow cursor display - system. |
| 44 | #BB84 | TXT CUR OFF | Text VDU | all |  | Disallow cursor display - system. |
| 45 | #BB87 | TXT VALIDATE | Text VDU | all |  | Check if a cursor position is within the window. |
| 46 | #BB8A | TXT PLACE CURSOR | Text VDU | all |  | Put a cursor blob on the screen. |
| 47 | #BB8D | TXT REMOVE CURSOR | Text VDU | all |  | Take a cursor blob off the screen. |
| 48 | #BB90 | TXT SET PEN | Text VDU | all |  | Set ink for writing characters. |
| 49 | #BB93 | TXT GET PEN | Text VDU | all |  | Get ink for writing characters. |
| 50 | #BB96 | TXT SET PAPER | Text VDU | all |  | Set ink for writing text background. |
| 51 | #BB99 | TXT GET PAPER | Text VDU | all |  | Get ink for writing background. |
| 52 | #BB9C | TXT INVERSE | Text VDU | all | V1.0 differs | Swap current pen and paper inks over. |
| 53 | #BB9F | TXT SET BACK | Text VDU | all |  | Allow or disallow background being written. |
| 54 | #BBA2 | TXT GET BACK | Text VDU | all |  | Ask if background is being written. |
| 55 | #BBA5 | TXT GET MATRIX | Text VDU | all |  | Get the address of a character matrix. |
| 56 | #BBA8 | TXT SET MATRIX | Text VDU | all |  | Set a character matrix. |
| 57 | #BBAB | TXT SET M TABLE | Text VDU | all |  | Set the user defined matrix table address. |
| 58 | #BBAE | TXT GET M TABLE | Text VDU | all |  | Get user defined matrix table address. |
| 59 | #BBB1 | TXT GET CONTROLS | Text VDU | all |  | Fetch address of control code table. |
| 60 | #BBB4 | TXT STR SELECT | Text VDU | all |  | Select a Text VDU stream. |
| 61 | #BBB7 | TXT SWAP STREAMS | Text VDU | all |  | Swap the states of two streams. |
| 62 | #BBBA | GRA INITIALISE | Graphics VDU | all |  | Initialize the Graphics VDU. |
| 63 | #BBBD | GRA RESET | Graphics VDU | all |  | Reset the Graphics VDU. |
| 64 | #BBC0 | GRA MOVE ABSOLUTE | Graphics VDU | all |  | Move to an absolute position. |
| 65 | #BBC3 | GRA MOVE RELATIVE | Graphics VDU | all |  | Move relative to current position. |
| 66 | #BBC6 | GRA ASK CURSOR | Graphics VDU | all |  | Get the current position. |
| 67 | #BBC9 | GRA SET ORIGIN | Graphics VDU | all |  | Set the origin of the user coordinates. |
| 68 | #BBCC | GRA GET ORIGIN | Graphics VDU | all |  | Get the origin of the user coordinates. |
| 69 | #BBCF | GRA WIN WIDTH | Graphics VDU | all |  | Set the right and left edges of the graphics window. |
| 70 | #BBD2 | GRA WIN HEIGHT | Graphics VDU | all |  | Set the top and bottom edges of the graphics window. |
| 71 | #BBD5 | GRA GET W WIDTH | Graphics VDU | all |  | Get the left and right edges of the graphics window. |
| 72 | #BBD8 | GRA GET W HEIGHT | Graphics VDU | all |  | Get the top and bottom edges of the graphics window. |
| 73 | #BBDB | GRA CLEAR WINDOW | Graphics VDU | all |  | Clear the graphic window. |
| 74 | #BBDE | GRA SET PEN | Graphics VDU | all |  | Set the graphics plotting ink. |
| 75 | #BBE1 | GRA GET PEN | Graphics VDU | all |  | Get the current graphics plotting ink |
| 76 | #BBE4 | GRA SET PAPER | Graphics VDU | all |  | Set the graphics background ink. |
| 77 | #BBE7 | GRA GET PAPER | Graphics VDU | all |  | Get the current graphics background ink. |
| 78 | #BBEA | GRA PLOT ABSOLUTE | Graphics VDU | all |  | Plot a point at an absolute position. |
| 79 | #BBED | GRA PLOT RELATIVE | Graphics VDU | all |  | Plot a point relative to the current position |
| 80 | #BBF0 | GRA TEST ABSOLUTE | Graphics VDU | all |  | Test a point at an absolute position. |
| 81 | #BBF3 | GRA TEST RELATIVE | Graphics VDU | all |  | Test a point relative to the current position. |
| 82 | #BBF6 | GRA LINE ABSOLUTE | Graphics VDU | all | V1.0 differs | Draw a line to an absolute position. |
| 83 | #BBF9 | GRA LINE RELATIVE | Graphics VDU | all | V1.0 differs | Draw a line relative to the current position. |
| 84 | #BBFC | GRA WR CHAR | Graphics VDU | all | V1.0 differs | Put a character on the screen at the current graphics |
| 85 | #BBFF | SCR INITIALISE | Screen Pack | all |  | Initialize the Screen Pack. |
| 86 | #BC02 | SCR RESET | Screen Pack | all |  | Reset the Screen Pack. |
| 87 | #BC05 | SCR SET OFFSET | Screen Pack | all |  | Set the offset of the start of the screen. |
| 88 | #BC08 | SCR SET BASE | Screen Pack | all |  | Set the area of RAM to use for the screen memory. |
| 89 | #BC0B | SCR GET LOCATION | Screen Pack | all |  | Fetch current base and offset settings. |
| 90 | #BC0E | SCR SET MODE | Screen Pack | all |  | Set screen into a new mode. |
| 91 | #BC11 | SCR GET MODE | Screen Pack | all |  | Ask the current screen mode. |
| 92 | #BC14 | SCR CLEAR | Screen Pack | all |  | Clear the screen (to ink zero). |
| 93 | #BC17 | SCR CHAR LIMITS | Screen Pack | all |  | Ask the size of the screen in characters. |
| 94 | #BC1A | SCR CHAR POSITION | Screen Pack | all |  | Convert physical coordinates to a screen position. |
| 95 | #BC1D | SCR DOT POSITION | Screen Pack | all |  | Convert base coordinates to a screen position. |
| 96 | #BC20 | SCR NEXT BYTE | Screen Pack | all |  | Step a screen address right one byte. |
| 97 | #BC23 | SCR PREV BYTE | Screen Pack | all |  | Step a screen address left one byte. |
| 98 | #BC26 | SCR NEXT LINE | Screen Pack | all |  | Step a screen address down one line. |
| 99 | #BC29 | SCR PREV LINE | Screen Pack | all |  | Step a screen address up one line. |
| 100 | #BC2C | SCR INK ENCODE | Screen Pack | all |  | Encode an ink to cover all pixels in a byte. |
| 101 | #BC2F | SCR INK DECODE | Screen Pack | all |  | Decode an encoded ink. |
| 102 | #BC32 | SCR SET INK | Screen Pack | all |  | Set the colours in which to display an ink. |
| 103 | #BC35 | SCR GET INK | Screen Pack | all |  | Ask the colours an ink is currently displayed in. |
| 104 | #BC38 | SCR SET BORDER | Screen Pack | all |  | Set the colours in which to display the border. |
| 105 | #BC3B | SCR GET BORDER | Screen Pack | all |  | Ask the colours the border is currently displayed in. |
| 106 | #BC3E | SCR SET FLASHING | Screen Pack | all |  | Set the flash periods. |
| 107 | #BC41 | SCR GET FLASHING | Screen Pack | all |  | Ask the current flash periods. |
| 108 | #BC44 | SCR FILL BOX | Screen Pack | all |  | Fill a character area of the screen with an ink. |
| 109 | #BC47 | SCR FLOOD BOX | Screen Pack | all |  | Fill a byte area of the screen. |
| 110 | #BC4A | SCR CHAR INVERT | Screen Pack | all |  | Invert a character position. |
| 111 | #BC4D | SCR HW ROLL | Screen Pack | all |  | Move the whole screen up or down eight pixel lines (one |
| 112 | #BC50 | SCR SW ROLL | Screen Pack | all |  | Move an area of the screen up or down eight pixel lines |
| 113 | #BC53 | SCR UNPACK | Screen Pack | all |  | Expand a character matrix for the current screen mode. |
| 114 | #BC56 | SCR REPACK | Screen Pack | all |  | Compress a character matrix to the standard form. |
| 115 | #BC59 | SCR ACCESS | Screen Pack | all |  | Set the screen write mode for the Graphics VDU. |
| 116 | #BC5C | SCR PIXELS | Screen Pack | all |  | Write a pixel to the screen ignoring the Graphics VDU |
| 117 | #BC5F | SCR HORIZONTAL | Screen Pack | all |  | Plot a purely horizontal line. |
| 118 | #BC62 | SCR VERTICAL | Screen Pack | all |  | Plot a purely vertical line. |
| 119 | #BC65 | CAS INITIALISE | Cassette Manager | all | V1.0 differs | Initialize the Cassette Manager. |
| 120 | #BC68 | CAS SET SPEED | Cassette Manager | all |  | Set the write speed. |
| 121 | #BC6B | CAS NOISY | Cassette Manager | all |  | Enable or disable prompt messages. |
| 122 | #BC6E | CAS START MOTOR | Cassette Manager | all |  | Start the cassette motor. |
| 123 | #BC71 | CAS STOP MOTOR | Cassette Manager | all |  | Stop the cassette motor. |
| 124 | #BC74 | CAS RESTORE MOTOR | Cassette Manager | all |  | Restore previous state of cassette motor. |
| 125 | #BC77 | CAS IN OPEN | Cassette Manager | all | AMSDOS patch, V1.0 differs | Open a file for input. |
| 126 | #BC7A | CAS IN CLOSE | Cassette Manager | all | AMSDOS patch, V1.0 differs | Close the input file properly. |
| 127 | #BC7D | CAS IN ABANDON | Cassette Manager | all | AMSDOS patch | Close the input file immediately. |
| 128 | #BC80 | CAS IN CHAR | Cassette Manager | all | AMSDOS patch, V1.0 differs | Read a character from the input file. |
| 129 | #BC83 | CAS IN DIRECT | Cassette Manager | all | AMSDOS patch, V1.0 differs | Read the input file into store. |
| 130 | #BC86 | CAS RETURN | Cassette Manager | all | AMSDOS patch | Put the last character read back. |
| 131 | #BC89 | CAS TEST EOF | Cassette Manager | all | AMSDOS patch, V1.0 differs | Have we reached the end of the input file yet? |
| 132 | #BC8C | CAS OUT OPEN | Cassette Manager | all | AMSDOS patch, V1.0 differs | Open a file for output. |
| 133 | #BC8F | CAS OUT CLOSE | Cassette Manager | all | AMSDOS patch, V1.0 differs | Close the output file properly. |
| 134 | #BC92 | CAS OUT ABANDON | Cassette Manager | all | AMSDOS patch | Close the output file immediately. |
| 135 | #BC95 | CAS OUT CHAR | Cassette Manager | all | AMSDOS patch, V1.0 differs | Write a character to the output file. |
| 136 | #BC98 | CAS OUT DIRECT | Cassette Manager | all | AMSDOS patch, V1.0 differs | Write the output file directly from store. |
| 137 | #BC9B | CAS CATALOG | Cassette Manager | all | AMSDOS patch, V1.0 differs | Generate a catalogue from the tape. |
| 138 | #BC9E | CAS WRITE | Cassette Manager | all |  | Write a record to tape. |
| 139 | #BCA1 | CAS READ | Cassette Manager | all |  | Read a record from tape. |
| 140 | #BCA4 | CAS CHECK | Cassette Manager | all |  | Compare a record on tape with the contents of store. |
| 141 | #BCA7 | SOUND RESET | Sound Manager | all |  | Reset the Sound Manager. |
| 142 | #BCAA | SOUND QUEUE | Sound Manager | all |  | Add a sound to a sound queue. |
| 143 | #BCAD | SOUND CHECK | Sound Manager | all |  | Ask if there is space in a sound queue. |
| 144 | #BCB0 | SOUND ARM EVENT | Sound Manager | all |  | Set up an event to be run when a sound queue becomes empty. |
| 145 | #BCB3 | SOUND RELEASE | Sound Manager | all |  | Allow sounds which are individually held to start. |
| 146 | #BCB6 | SOUND HOLD | Sound Manager | all |  | Stop all sounds in midflight. |
| 147 | #BCB9 | SOUND CONTINUE | Sound Manager | all |  | Restart sounds after they have all been held. |
| 148 | #BCBC | SOUND AMPL ENVELOPE | Sound Manager | all |  | Set up an amplitude envelope. |
| 149 | #BCBF | SOUND TONE ENVELOPE | Sound Manager | all |  | Set up a tone envelope. |
| 150 | #BCC2 | SOUND A ADDRESS | Sound Manager | all |  | Get the address of an amplitude envelope. |
| 151 | #BCC5 | SOUND T ADDRESS | Sound Manager | all |  | Get the address of a tone envelope. |
| 152 | #BCC8 | KL CHOKE OFF | Kernel | all |  | Reset the Kernel - clears all event queues etc. |
| 153 | #BCCB | KL ROM WALK | Kernel | all | V1.0 differs | Find and initialize all background ROMs. |
| 154 | #BCCE | KL INIT BACK | Kernel | all | V1.0 differs | Initialize a particular background ROM. |
| 155 | #BCD1 | KL LOG EXT | Kernel | all |  | Introduce an RSX to the Firmware. |
| 156 | #BCD4 | KL FIND COMMAND | Kernel | all | V1.0 differs | Search for an RSX, background ROM or foreground ROM |
| 157 | #BCD7 | KL NEW FRAME FLY | Kernel | all |  | Initialize and put a block onto the frame flyback list. |
| 158 | #BCDA | KL ADD FRAME FLY | Kernel | all |  | Put a block onto the frame flyback list. |
| 159 | #BCDD | KL DEL FRAME FLY | Kernel | all |  | Remove a block from the frame flyback list. |
| 160 | #BCE0 | KL NEW FAST TICKER | Kernel | all |  | Initialize and put a block onto the fast ticker list. |
| 161 | #BCE3 | KL ADD FAST TICKER | Kernel | all |  | Put a block onto the fast ticker list. |
| 162 | #BCE6 | KL DEL FAST TICKER | Kernel | all |  | Remove a block from the fast ticker list. |
| 163 | #BCE9 | KL ADD TICKER | Kernel | all |  | Put a block onto the tick list. |
| 164 | #BCEC | KL DEL TICKER | Kernel | all |  | Remove block from the tick list. |
| 165 | #BCEF | KL INIT EVENT | Kernel | all |  | Initialize an event block. |
| 166 | #BCF2 | KL EVENT | Kernel | all |  | ‘Kick’ an event block. |
| 167 | #BCF5 | KL SYNC RESET | Kernel | all |  | Clear synchronous event queue. |
| 168 | #BCF8 | KL DEL SYNCHRONOUS | Kernel | all |  | Remove a synchronous event from the event queue. |
| 169 | #BCFB | KL NEXT SYNC | Kernel | all |  | Get next event from the queue. |
| 170 | #BCFE | KL DO SYNC | Kernel | all |  | Perform an event routine. |
| 171 | #BD01 | KL DONE SYNC | Kernel | all |  | Finish processing an event. |
| 172 | #BD04 | KL EVENT DISABLE | Kernel | all |  | Disable normal synchronous events. |
| 173 | #BD07 | KL EVENT ENABLE | Kernel | all |  | Enable normal synchronous events. |
| 174 | #BD0A | KL DISARM EVENT | Kernel | all |  | Prevent an event from occurring. |
| 175 | #BD0D | KL TIME PLEASE | Kernel | all |  | Ask the elapsed time. |
| 176 | #BD10 | KL TIME SET | Kernel | all |  | Set the elapsed time. |
| 177 | #BD13 | MC BOOT PROGRAM | Machine Pack | all |  | Load and run a program. |
| 178 | #BD16 | MC START PROGRAM | Machine Pack | all |  | Run a foreground program. |
| 179 | #BD19 | MC WAIT FLYBACK | Machine Pack | all |  | Wait for frame flyback. |
| 180 | #BD1C | MC SET MODE | Machine Pack | all |  | Set the screen mode. |
| 181 | #BD1F | MC SCREEN OFFSET | Machine Pack | all |  | Set the screen offset. |
| 182 | #BD22 | MC CLEAR INKS | Machine Pack | all |  | Set all inks to one colour. |
| 183 | #BD25 | MC SET INKS | Machine Pack | all |  | Set colours of all the inks. |
| 184 | #BD28 | MC RESET PRINTER | Machine Pack | all |  | Reset the printer indirection. |
| 185 | #BD2B | MC PRINT CHAR | Machine Pack | all |  | Try to send a character to the Centronics port. |
| 186 | #BD2E | MC BUSY PRINTER | Machine Pack | all |  | Test if the Centronics port is busy. |
| 187 | #BD31 | MC SEND PRINTER | Machine Pack | all |  | Send a character to the Centronics port. |
| 188 | #BD34 | MC SOUND REGISTER | Machine Pack | all |  | Send data to a sound chip register. |
| 189 | #BD37 | JUMP RESTORE | Jumper | all |  | Restore the standard jumpblock. |
| 190 | #BD3A | KM SET LOCKS | Key Manager | 664/6128 |  | Set the shift and caps lock states. |
| 191 | #BD3D | KM FLUSH | Key Manager | 664/6128 |  | Flush the keyboard buffers. |
| 192 | #BD40 | TXT ASK STATE | Text VDU | 664/6128 |  | Get the state of the Text VDU. |
| 193 | #BD43 | GRA DEFAULT | Graphics VDU | 664/6128 |  | Set default Graphics VDU modes. |
| 194 | #BD46 | GRA SET BACK | Graphics VDU | 664/6128 |  | Set whether background is to be written. |
| 195 | #BD49 | GRA SET FIRST | Graphics VDU | 664/6128 |  | Set whether the first point of a line is to be plotted. |
| 196 | #BD4C | GRA SET LINE MASK | Graphics VDU | 664/6128 |  | Set the line mask for plotting pixels of lines. |
| 197 | #BD4F | GRA FROM USER | Graphics VDU | 664/6128 |  | Convert user coordinates to base coordinates. |
| 198 | #BD52 | GRA FILL | Graphics VDU | 664/6128 |  | Fill an area of the screen. |
| 199 | #BD55 | SCR SET POSITION | Screen Pack | 664/6128 |  | Set the location of the screen memory. |
| 200 | #BD58 | MC PRINT TRANSLATION | Machine Pack | 664/6128 |  | Set the printer translation table. |
| 201 | #BD5B | KL BANK SWITCH | Kernel | 6128 |  | Select a memory organization. |

## 2. Firmware indirections (#BDCD–#BDF6)

Jumps (#C3) taken by the firmware at key points so that the user can substitute routines. They are
not intended to be called by the user. Set up by each pack's reset/initialise routine and at EMS.

| # | Address | Name | Avail | Notes | Description |
|---|---|---|---|---|---|
| 0 | #BDCD | TXT DRAW CURSOR | all |  | Place the cursor blob on the screen (if enabled). |
| 1 | #BDD0 | TXT UNDRAW CURSOR | all |  | Remove the cursor blob from the screen (if enabled). |
| 2 | #BDD3 | TXT WRITE CHAR | all |  | Write a character onto the screen. |
| 3 | #BDD6 | TXT UNWRITE | all |  | Read a character from the screen. |
| 4 | #BDD9 | TXT OUT ACTION | all | V1.0 differs | Output a character or control code. |
| 5 | #BDDC | GRA PLOT | all |  | Plot a point. |
| 6 | #BDDF | GRA TEST | all |  | Test a point. |
| 7 | #BDE2 | GRA LINE | all | V1.0 differs | Draw a line. |
| 8 | #BDE5 | SCR READ | all |  | Read a pixel from the screen. |
| 9 | #BDE8 | SCR WRITE | all |  | Write pixel(s) using the current graphics write mode. |
| 10 | #BDEB | SCR MODE CLEAR | all | V1.0 differs | Clear the screen to ink 0. |
| 11 | #BDEE | KM TEST BREAK | all |  | Test for break (or reset). |
| 12 | #BDF1 | MC WAIT PRINTER | all |  | Print a character or time out. |
| 13 | #BDF4 | KM SCAN KEYS | all |  | Scan the keyboard. |

## 3. High Kernel jumpblock (#B900–)

RAM-resident routines for ROM state and ROM selection (copied out of the lower ROM at power-up).
Some entries are the start of routines rather than jumps; the block must not be altered. There
are no entries 12 and 13 (#B924, #B927).

| # | Address | Name | Avail | Description |
|---|---|---|---|---|
| 0 | #B900 | KL U ROM ENABLE | all | Enable the upper ROM. |
| 1 | #B903 | KL U ROM DISABLE | all | Disable the upper ROM. |
| 2 | #B906 | KL L ROM ENABLE | all | Enable the lower ROM. |
| 3 | #B909 | KL L ROM DISABLE | all | Disable the lower ROM. |
| 4 | #B90C | KL ROM RESTORE | all | Restore the previous ROM state. |
| 5 | #B90F | KL ROM SELECT | all | Select a particular upper ROM. |
| 6 | #B912 | KL CURR SELECTION | all | Ask which upper ROM is currently selected. |
| 7 | #B915 | KL PROBE ROM | all | Ask class and version of a ROM. |
| 8 | #B918 | KL ROM DESELECT | all | Restore previous upper ROM selection. |
| 9 | #B91B | KL LDIR | all | Move store (LDIR) with ROMs turned off. |
| 10 | #B91E | KL LDDR | all | Move store (LDDR) with ROMs turned off. |
| 11 | #B921 | KL POLL SYNCHRONOUS | all | Check if an event with higher priority than the current event is pending. |
| 14 | #B92A | KL SCAN NEEDED | all | Ensure keyboard is scanned at next opportunity. |

## 4. Low Kernel jumpblock (#0000–#003F)

Routines at published addresses, several of them also reachable as restart instructions. They are
present both in the lower ROM and in the RAM copy, so they work whatever the lower ROM state. The
user may patch only the USER RESTART area (#0030–#0037) and the EXT INTERRUPT area (#003B–); a
program patching anything else (e.g. INTERRUPT ENTRY at #0038, RESET ENTRY #0000–#0007) must
restore it or keep the lower ROM enabled when other programs run.

| Address | Restart | Name | Avail | Description |
|---|---|---|---|---|
| #0000 | RST 0 | RESET ENTRY | all | Completely reset the machine as if powered up. |
| #0008 | RST 1 | LOW JUMP | all | Jump to lower ROM or RAM; takes inline 'low address' to jump to. |
| #000B |  | KL LOW PCHL | all | Jump to lower ROM or RAM. |
| #000E |  | PCBC INSTRUCTION | all | Jump to address in BC. |
| #0010 | RST 2 | SIDE CALL | all | Call to a sideways ROM, takes inline ‘side address’ to call. |
| #0013 |  | KL SIDE PCHL | all | Call to a sideways ROM; HL contains 'side address' to call. |
| #0016 |  | PCDE INSTRUCTION | all | Jump to address in DE. |
| #0018 | RST 3 | FAR CALL | all | Call subroutine in RAM or any ROM; takes inline address of 'far address' to call. |
| #001B |  | KL FAR PCHL | all | Call subroutine in RAM or any ROM. |
| #001E |  | PCHL INSTRUCTION | all | Jump to address in HL. |
| #0020 | RST 4 | RAM LAM | all | LD A,(HL) with all ROMs disabled. |
| #0023 |  | KL FAR ICALL | all | Call subroutine in RAM or any ROM; HL points at 'far address' to call. |
| #0028 | RST 5 | FIRM JUMP | all | Jump to lower ROM, takes inline address to jump to. |
| #0030 | RST 6 | USER RESTART | all | Undedicated RST instruction. |
| #0038 | RST 7 | INTERRUPT ENTRY | all | Hardware interrupt entry point. |
| #003B |  | EXT INTERRUPT | all | External interrupt routine. |

Two details from the low Kernel entries (section 18) that complete `docs/firmware_section2_roms_ram_rst.md`:

- **SIDE CALL / KL SIDE PCHL:** the 2-bit "Off" field (bits 15–14 of the side address) is added
  to the ROM select address of the **main foreground ROM** to give the ROM to select.
- **RAM LAM:** equivalent to `LD A,(HL)` with all ROMs disabled.

## 5. Notes for cpcxray (not part of the firmware guide)

- These names and addresses are the source for the firmware symbols preloaded into the
  disassembler (`docs/design.md` D-26). The addresses of the jumpblock entries are the same on
  all machines; the ROM routines they jump to are not, because the lower ROMs differ (V1.0,
  V1.1, V1.2).
- On a 464 (V1.0), entries 190–201 do not exist; a call to #BD3A–#BD5B from analysed code means
  the code was written for a 664/6128 (or is not a firmware call).
- Entries 125–137 are redirected to AMSDOS when AMSDOS is fitted, so on a disc system a call to
  them ends up in the AMSDOS ROM, not in the lower ROM.
- KM SCAN KEYS (#BDF4) is listed for all machines above, but is 664/6128 only (the BIOS Function
  Summary says so, and on a booted 464 #BDF4 holds no jump).
- The maths jumpblock and TEXT INPUT, which SOFT 968 does not document, are in section 7.

## 6. Compact list (machine-oriented)

Format: `addr kind num name avail [notes]`; kind: M=main, I=indirection, H=high Kernel,
L=low Kernel; avail: A=all, 6=664/6128, 8=6128 only; notes: P=AMSDOS patch, D=V1.0 differs.

```
0000 L - RESET ENTRY A
0008 L - LOW JUMP A
000B L - KL LOW PCHL A
000E L - PCBC INSTRUCTION A
0010 L - SIDE CALL A
0013 L - KL SIDE PCHL A
0016 L - PCDE INSTRUCTION A
0018 L - FAR CALL A
001B L - KL FAR PCHL A
001E L - PCHL INSTRUCTION A
0020 L - RAM LAM A
0023 L - KL FAR ICALL A
0028 L - FIRM JUMP A
0030 L - USER RESTART A
0038 L - INTERRUPT ENTRY A
003B L - EXT INTERRUPT A
B900 H 0 KL U ROM ENABLE A
B903 H 1 KL U ROM DISABLE A
B906 H 2 KL L ROM ENABLE A
B909 H 3 KL L ROM DISABLE A
B90C H 4 KL ROM RESTORE A
B90F H 5 KL ROM SELECT A
B912 H 6 KL CURR SELECTION A
B915 H 7 KL PROBE ROM A
B918 H 8 KL ROM DESELECT A
B91B H 9 KL LDIR A
B91E H 10 KL LDDR A
B921 H 11 KL POLL SYNCHRONOUS A
B92A H 14 KL SCAN NEEDED A
BB00 M 0 KM INITIALISE A
BB03 M 1 KM RESET A
BB06 M 2 KM WAIT CHAR A
BB09 M 3 KM READ CHAR A
BB0C M 4 KM CHAR RETURN A
BB0F M 5 KM SET EXPAND A
BB12 M 6 KM GET EXPAND A
BB15 M 7 KM EXP BUFFER A
BB18 M 8 KM WAIT KEY A
BB1B M 9 KM READ KEY A
BB1E M 10 KM TEST KEY A
BB21 M 11 KM GET STATE A
BB24 M 12 KM GET JOYSTICK A
BB27 M 13 KM SET TRANSLATE A
BB2A M 14 KM GET TRANSLATE A
BB2D M 15 KM SET SHIFT A
BB30 M 16 KM GET SHIFT A
BB33 M 17 KM SET CONTROL A
BB36 M 18 KM GET CONTROL A
BB39 M 19 KM SET REPEAT A
BB3C M 20 KM GET REPEAT A
BB3F M 21 KM SET DELAY A
BB42 M 22 KM GET DELAY A
BB45 M 23 KM ARM BREAK A
BB48 M 24 KM DISARM BREAK A
BB4B M 25 KM BREAK EVENT A
BB4E M 26 TXT INITIALISE A
BB51 M 27 TXT RESET A
BB54 M 28 TXT VDU ENABLE A
BB57 M 29 TXT VDU DISABLE A D
BB5A M 30 TXT OUTPUT A
BB5D M 31 TXT WR CHAR A
BB60 M 32 TXT RD CHAR A D
BB63 M 33 TXT SET GRAPHIC A
BB66 M 34 TXT WIN ENABLE A
BB69 M 35 TXT GET WINDOW A
BB6C M 36 TXT CLEAR WINDOW A
BB6F M 37 TXT SET COLUMN A
BB72 M 38 TXT SET ROW A
BB75 M 39 TXT SET CURSOR A
BB78 M 40 TXT GET CURSOR A
BB7B M 41 TXT CUR ENABLE A
BB7E M 42 TXT CUR DISABLE A
BB81 M 43 TXT CUR ON A
BB84 M 44 TXT CUR OFF A
BB87 M 45 TXT VALIDATE A
BB8A M 46 TXT PLACE CURSOR A
BB8D M 47 TXT REMOVE CURSOR A
BB90 M 48 TXT SET PEN A
BB93 M 49 TXT GET PEN A
BB96 M 50 TXT SET PAPER A
BB99 M 51 TXT GET PAPER A
BB9C M 52 TXT INVERSE A D
BB9F M 53 TXT SET BACK A
BBA2 M 54 TXT GET BACK A
BBA5 M 55 TXT GET MATRIX A
BBA8 M 56 TXT SET MATRIX A
BBAB M 57 TXT SET M TABLE A
BBAE M 58 TXT GET M TABLE A
BBB1 M 59 TXT GET CONTROLS A
BBB4 M 60 TXT STR SELECT A
BBB7 M 61 TXT SWAP STREAMS A
BBBA M 62 GRA INITIALISE A
BBBD M 63 GRA RESET A
BBC0 M 64 GRA MOVE ABSOLUTE A
BBC3 M 65 GRA MOVE RELATIVE A
BBC6 M 66 GRA ASK CURSOR A
BBC9 M 67 GRA SET ORIGIN A
BBCC M 68 GRA GET ORIGIN A
BBCF M 69 GRA WIN WIDTH A
BBD2 M 70 GRA WIN HEIGHT A
BBD5 M 71 GRA GET W WIDTH A
BBD8 M 72 GRA GET W HEIGHT A
BBDB M 73 GRA CLEAR WINDOW A
BBDE M 74 GRA SET PEN A
BBE1 M 75 GRA GET PEN A
BBE4 M 76 GRA SET PAPER A
BBE7 M 77 GRA GET PAPER A
BBEA M 78 GRA PLOT ABSOLUTE A
BBED M 79 GRA PLOT RELATIVE A
BBF0 M 80 GRA TEST ABSOLUTE A
BBF3 M 81 GRA TEST RELATIVE A
BBF6 M 82 GRA LINE ABSOLUTE A D
BBF9 M 83 GRA LINE RELATIVE A D
BBFC M 84 GRA WR CHAR A D
BBFF M 85 SCR INITIALISE A
BC02 M 86 SCR RESET A
BC05 M 87 SCR SET OFFSET A
BC08 M 88 SCR SET BASE A
BC0B M 89 SCR GET LOCATION A
BC0E M 90 SCR SET MODE A
BC11 M 91 SCR GET MODE A
BC14 M 92 SCR CLEAR A
BC17 M 93 SCR CHAR LIMITS A
BC1A M 94 SCR CHAR POSITION A
BC1D M 95 SCR DOT POSITION A
BC20 M 96 SCR NEXT BYTE A
BC23 M 97 SCR PREV BYTE A
BC26 M 98 SCR NEXT LINE A
BC29 M 99 SCR PREV LINE A
BC2C M 100 SCR INK ENCODE A
BC2F M 101 SCR INK DECODE A
BC32 M 102 SCR SET INK A
BC35 M 103 SCR GET INK A
BC38 M 104 SCR SET BORDER A
BC3B M 105 SCR GET BORDER A
BC3E M 106 SCR SET FLASHING A
BC41 M 107 SCR GET FLASHING A
BC44 M 108 SCR FILL BOX A
BC47 M 109 SCR FLOOD BOX A
BC4A M 110 SCR CHAR INVERT A
BC4D M 111 SCR HW ROLL A
BC50 M 112 SCR SW ROLL A
BC53 M 113 SCR UNPACK A
BC56 M 114 SCR REPACK A
BC59 M 115 SCR ACCESS A
BC5C M 116 SCR PIXELS A
BC5F M 117 SCR HORIZONTAL A
BC62 M 118 SCR VERTICAL A
BC65 M 119 CAS INITIALISE A D
BC68 M 120 CAS SET SPEED A
BC6B M 121 CAS NOISY A
BC6E M 122 CAS START MOTOR A
BC71 M 123 CAS STOP MOTOR A
BC74 M 124 CAS RESTORE MOTOR A
BC77 M 125 CAS IN OPEN A PD
BC7A M 126 CAS IN CLOSE A PD
BC7D M 127 CAS IN ABANDON A P
BC80 M 128 CAS IN CHAR A PD
BC83 M 129 CAS IN DIRECT A PD
BC86 M 130 CAS RETURN A P
BC89 M 131 CAS TEST EOF A PD
BC8C M 132 CAS OUT OPEN A PD
BC8F M 133 CAS OUT CLOSE A PD
BC92 M 134 CAS OUT ABANDON A P
BC95 M 135 CAS OUT CHAR A PD
BC98 M 136 CAS OUT DIRECT A PD
BC9B M 137 CAS CATALOG A PD
BC9E M 138 CAS WRITE A
BCA1 M 139 CAS READ A
BCA4 M 140 CAS CHECK A
BCA7 M 141 SOUND RESET A
BCAA M 142 SOUND QUEUE A
BCAD M 143 SOUND CHECK A
BCB0 M 144 SOUND ARM EVENT A
BCB3 M 145 SOUND RELEASE A
BCB6 M 146 SOUND HOLD A
BCB9 M 147 SOUND CONTINUE A
BCBC M 148 SOUND AMPL ENVELOPE A
BCBF M 149 SOUND TONE ENVELOPE A
BCC2 M 150 SOUND A ADDRESS A
BCC5 M 151 SOUND T ADDRESS A
BCC8 M 152 KL CHOKE OFF A
BCCB M 153 KL ROM WALK A D
BCCE M 154 KL INIT BACK A D
BCD1 M 155 KL LOG EXT A
BCD4 M 156 KL FIND COMMAND A D
BCD7 M 157 KL NEW FRAME FLY A
BCDA M 158 KL ADD FRAME FLY A
BCDD M 159 KL DEL FRAME FLY A
BCE0 M 160 KL NEW FAST TICKER A
BCE3 M 161 KL ADD FAST TICKER A
BCE6 M 162 KL DEL FAST TICKER A
BCE9 M 163 KL ADD TICKER A
BCEC M 164 KL DEL TICKER A
BCEF M 165 KL INIT EVENT A
BCF2 M 166 KL EVENT A
BCF5 M 167 KL SYNC RESET A
BCF8 M 168 KL DEL SYNCHRONOUS A
BCFB M 169 KL NEXT SYNC A
BCFE M 170 KL DO SYNC A
BD01 M 171 KL DONE SYNC A
BD04 M 172 KL EVENT DISABLE A
BD07 M 173 KL EVENT ENABLE A
BD0A M 174 KL DISARM EVENT A
BD0D M 175 KL TIME PLEASE A
BD10 M 176 KL TIME SET A
BD13 M 177 MC BOOT PROGRAM A
BD16 M 178 MC START PROGRAM A
BD19 M 179 MC WAIT FLYBACK A
BD1C M 180 MC SET MODE A
BD1F M 181 MC SCREEN OFFSET A
BD22 M 182 MC CLEAR INKS A
BD25 M 183 MC SET INKS A
BD28 M 184 MC RESET PRINTER A
BD2B M 185 MC PRINT CHAR A
BD2E M 186 MC BUSY PRINTER A
BD31 M 187 MC SEND PRINTER A
BD34 M 188 MC SOUND REGISTER A
BD37 M 189 JUMP RESTORE A
BD3A M 190 KM SET LOCKS 6
BD3D M 191 KM FLUSH 6
BD40 M 192 TXT ASK STATE 6
BD43 M 193 GRA DEFAULT 6
BD46 M 194 GRA SET BACK 6
BD49 M 195 GRA SET FIRST 6
BD4C M 196 GRA SET LINE MASK 6
BD4F M 197 GRA FROM USER 6
BD52 M 198 GRA FILL 6
BD55 M 199 SCR SET POSITION 6
BD58 M 200 MC PRINT TRANSLATION 6
BD5B M 201 KL BANK SWITCH 8
BDCD I 0 TXT DRAW CURSOR A
BDD0 I 1 TXT UNDRAW CURSOR A
BDD3 I 2 TXT WRITE CHAR A
BDD6 I 3 TXT UNWRITE A
BDD9 I 4 TXT OUT ACTION A D
BDDC I 5 GRA PLOT A
BDDF I 6 GRA TEST A
BDE2 I 7 GRA LINE A D
BDE5 I 8 SCR READ A
BDE8 I 9 SCR WRITE A
BDEB I 10 SCR MODE CLEAR A D
BDEE I 11 KM TEST BREAK A
BDF1 I 12 MC WAIT PRINTER A
BDF4 I 13 KM SCAN KEYS A
```

## 7. Maths jumpblock and TEXT INPUT (not in SOFT 968)

SOFT 968 does not document these entries. The firmware sets them up behind the main jumpblock
(FIRM JUMP, RST 5, into the lower ROM; TEXT INPUT uses LOW JUMP, RST 1), so the addresses differ
per machine: the 464 has them from #BD3A, the 664 from #BD5B (behind entry 200) and the 6128 from
#BD5E (behind entry 201). The 664 and 6128 share the order, every 664 address is the 6128 address
- 3; the 464 has its own order and an integer jumpblock (#BDA3-#BDCA) that the 664/6128 dropped
(their REAL RND and REAL RND(0) take the places of the 464's REAL SUBTRACTION and REAL EXPONENT
ADDITION). Compiled for cpcxray from:

- TD: *The Amstrad CPC Firmware Guide*, Bob Taylor and Thomas Defoe, 1992 (unofficial;
  docs/Taylor_Defoe_Amstrad_firmware.pdf, "The Maths Firmware", pp. 77-81). It gives the 6128
  address and the 464 address; its "664 and 6128" addresses are the 6128's.
- BFS: CPCWiki, *BIOS Function Summary* ("Edit and Floating Point Functions", with separate
  464/664/6128 columns; "Integer Functions - 464 only").
- WMG: CPCWiki, *Amstrad Whole Memory Guide - BASIC support* (464 addresses #BD3D-#BD8B).

Names follow TD; where TD only says "internal subroutine", the name is made from the BFS
description. All addresses were checked against the jumpblocks of booted 464, 664 and 6128
firmware (tests/machine/test_firmware_jumpblocks.py).

| 464 | 664 | 6128 | Name | Section | Sources | Notes |
|---|---|---|---|---|---|---|
| #BD3A | #BD5B | #BD5E | TEXT INPUT | edit | TD BFS | line editor (BFS: EDIT); RST 1, not RST 5 |
| #BD3D | #BD5E | #BD61 | MOVE REAL | maths | TD BFS WMG | TD prints #BDC1 for the 6128: a typo |
| #BD40 | #BD61 | #BD64 | INTEGER TO REAL | maths | TD BFS WMG |  |
| #BD43 | #BD64 | #BD67 | BINARY TO REAL | maths | TD BFS WMG | 4-byte binary |
| #BD46 | #BD67 | #BD6A | REAL TO INTEGER | maths | TD BFS WMG |  |
| #BD49 | #BD6A | #BD6D | REAL TO BINARY | maths | TD BFS WMG | BFS: round |
| #BD4C | #BD6D | #BD70 | REAL FIX | maths | TD BFS WMG |  |
| #BD4F | #BD70 | #BD73 | REAL INT | maths | TD BFS WMG |  |
| #BD52 | #BD73 | #BD76 | REAL PREPARE DECIMAL | maths | BFS WMG | TD: internal subroutine |
| #BD55 | #BD76 | #BD79 | REAL *10^A | maths | TD BFS WMG |  |
| #BD58 | #BD79 | #BD7C | REAL ADDITION | maths | TD BFS WMG |  |
| #BD5B | - | - | REAL SUBTRACTION | maths | TD BFS WMG | 464 only |
| #BD9D | #BD7C | #BD7F | REAL RND | maths | TD BFS | TD: internal subroutine on the 464 |
| #BD5E | #BD7F | #BD82 | REAL REVERSE SUBTRACTION | maths | TD BFS WMG |  |
| #BD61 | #BD82 | #BD85 | REAL MULTIPLICATION | maths | TD BFS WMG |  |
| #BD64 | #BD85 | #BD88 | REAL DIVISION | maths | TD BFS WMG |  |
| #BD67 | - | - | REAL EXPONENT ADDITION | maths | TD BFS WMG | 464 only; BFS: FLO SHIFT |
| #BDA0 | #BD88 | #BD8B | REAL RND(0) | maths | TD BFS | last RND value; TD: internal subroutine on the 464 |
| #BD6A | #BD8B | #BD8E | REAL COMPARISON | maths | TD BFS WMG |  |
| #BD6D | #BD8E | #BD91 | REAL UNARY MINUS | maths | TD BFS WMG |  |
| #BD70 | #BD91 | #BD94 | REAL SIGNUM/SGN | maths | TD BFS WMG |  |
| #BD73 | #BD94 | #BD97 | SET ANGLE MODE | maths | TD BFS WMG | DEG/RAD |
| #BD76 | #BD97 | #BD9A | REAL PI | maths | TD BFS WMG |  |
| #BD79 | #BD9A | #BD9D | REAL SQR | maths | TD BFS WMG |  |
| #BD7C | #BD9D | #BDA0 | REAL POWER | maths | TD BFS WMG |  |
| #BD7F | #BDA0 | #BDA3 | REAL LOG | maths | TD BFS WMG |  |
| #BD82 | #BDA3 | #BDA6 | REAL LOG 10 | maths | TD BFS WMG |  |
| #BD85 | #BDA6 | #BDA9 | REAL EXP | maths | TD BFS WMG |  |
| #BD88 | #BDA9 | #BDAC | REAL SINE | maths | TD BFS WMG |  |
| #BD8B | #BDAC | #BDAF | REAL COSINE | maths | TD BFS WMG |  |
| #BD8E | #BDAF | #BDB2 | REAL TANGENT | maths | TD BFS |  |
| #BD91 | #BDB2 | #BDB5 | REAL ARCTANGENT | maths | TD BFS |  |
| #BD94 | #BDB5 | #BDB8 | FIVE BYTE TO REAL | maths | BFS | TD: internal subroutine |
| #BD97 | #BDB8 | #BDBB | REAL RND INIT | maths | BFS | seed &89656C07; TD: internal subroutine |
| #BD9A | #BDBB | #BDBE | REAL RND SEED | maths | BFS | seed xor (HL); TD: internal subroutine |
| #BDA3 | - | - | INTEGER ABS | maths | BFS | 464 only; HL=ABS(HL), B.7=sign; TD: internal subroutine |
| #BDA6 | - | - | INTEGER POS | maths | BFS | 464 only; B.7=0; TD: internal subroutine |
| #BDA9 | - | - | INTEGER UNDO | maths | BFS | 464 only; unsigned HL + sign B.7 to signed; TD: internal subroutine |
| #BDAC | - | - | INTEGER ADDITION | maths | TD BFS | 464 only |
| #BDAF | - | - | INTEGER SUBTRACTION | maths | TD BFS | 464 only |
| #BDB2 | - | - | INTEGER REVERSE SUBTRACTION | maths | TD BFS | 464 only |
| #BDB5 | - | - | INTEGER MULTIPLICATION | maths | TD BFS | 464 only |
| #BDB8 | - | - | INTEGER DIVISION | maths | TD BFS | 464 only |
| #BDBB | - | - | INTEGER DIVISION 2 | maths | TD BFS | 464 only; BFS: MOD |
| #BDBE | - | - | INTEGER UNSIGNED MULTIPLICATION | maths | BFS | 464 only; TD: internal subroutine |
| #BDC1 | - | - | INTEGER UNSIGNED DIVISION | maths | BFS | 464 only; TD: internal subroutine |
| #BDC4 | - | - | INTEGER COMPARISON | maths | TD BFS | 464 only |
| #BDC7 | - | - | INTEGER UNARY MINUS | maths | TD BFS | 464 only |
| #BDCA | - | - | INTEGER SIGNUM/SGN | maths | TD BFS | 464 only |
