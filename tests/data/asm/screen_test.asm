; Phase 5 screen test: called with the screen mode in A, the number of pens in B and the
; number of extra lines to print (for scrolling) in C. Uses only firmware calls.
; Assemble: sjasmplus --nologo screen_test.asm  (writes screen_test.bin, ORG #4000)

scr_set_mode    equ #BC0E
scr_set_ink     equ #BC32
scr_set_border  equ #BC38
txt_set_pen     equ #BB90
txt_output      equ #BB5A

        OUTPUT "screen_test.bin"
        ORG #4000
start:
        ld (pens),bc            ; C = scroll lines, B = pens (stored as a word)
        call scr_set_mode       ; A = mode; clears the screen
        ld bc,#0606             ; border: bright red (firmware colour 6), no flashing
        call scr_set_border
        ld hl,ink_colours       ; give pens 0-15 the colours from the table
        xor a
set_inks:
        push af
        push hl
        ld b,(hl)
        ld c,b
        call scr_set_ink
        pop hl
        pop af
        inc hl
        inc a
        cp 16
        jr nz,set_inks

        xor a                   ; one line per pen: "Pen n" and solid blocks in that pen
pen_line:
        push af
        call txt_set_pen
        ld hl,text_pen
        call print
        pop af
        push af
        add a,"0"
        cp "9"+1
        jr c,digit
        add a,7                 ; A-F
digit:
        call txt_output
        ld b,8
blocks:
        ld a,143                ; solid block character
        call txt_output
        djnz blocks
        ld hl,text_crlf
        call print
        pop af
        inc a
        ld hl,pens+1
        cp (hl)
        jr nz,pen_line

        ld a,1                  ; scrolling: print numbered lines with pen 1
        call txt_set_pen
        ld a,(pens)
        or a
        ret z
        ld b,a
        ld c,1
scroll_line:
        push bc
        ld hl,text_line
        call print
        ld a,c
        call print_decimal
        ld hl,text_crlf
        call print
        pop bc
        inc c
        djnz scroll_line
        ret

print:                          ; print the 0-terminated string at HL
        ld a,(hl)
        or a
        ret z
        call txt_output
        inc hl
        jr print

print_decimal:                  ; print A (0-99) as two digits
        ld b,"0"-1
tens:   inc b
        sub 10
        jr nc,tens
        add a,10+"0"
        push af
        ld a,b
        call txt_output
        pop af
        jp txt_output

pens:           defw 0
ink_colours:    defb 1, 24, 20, 6, 26, 0, 2, 8, 10, 12, 14, 16, 18, 22, 3, 15
text_pen:       defb "Pen ", 0
text_line:      defb "Line ", 0
text_crlf:      defb 13, 10, 0
