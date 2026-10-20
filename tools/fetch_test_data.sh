#!/usr/bin/env bash
# Download external CPU test vectors into tests/data/external (not committed).
#   SingleStepTests/z80 (per-opcode JSON tests, ~1.4 GB): design.md §8, phase 1
set -euo pipefail
cd "$(dirname "$0")/.."
dest=tests/data/external
mkdir -p "$dest"
if [ ! -d "$dest/singlestep-z80" ]; then
    git clone --depth 1 https://github.com/SingleStepTests/z80.git "$dest/singlestep-z80"
else
    git -C "$dest/singlestep-z80" pull --ff-only
fi
