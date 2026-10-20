#!/usr/bin/env bash
# Download external CPU test programs into tests/data/external (not committed).
#   SingleStepTests/z80  per-opcode JSON tests, ~1.4 GB (design.md §8, phase 1)
#   ZEXDOC/ZEXALL        Frank Cringle, GPL-2.0, from agn453/ZEXALL (YAZE-AG 2.51.3), pinned commit
#   z80test 1.2a         Patrik Rak, MIT, official GitHub release (phase 2)
# Pinned files are verified against their SHA-256 checksums.
set -euo pipefail
cd "$(dirname "$0")/.."
dest=tests/data/external
mkdir -p "$dest"

if [ ! -d "$dest/singlestep-z80" ]; then
    git clone --depth 1 https://github.com/SingleStepTests/z80.git "$dest/singlestep-z80"
else
    git -C "$dest/singlestep-z80" pull --ff-only
fi

# download URL FILE SHA256: fetch unless present, then verify
download() {
    local url=$1 file=$2 sha256=$3
    if [ ! -f "$file" ]; then
        curl -sSfL -o "$file.part" "$url"
        mv "$file.part" "$file"
    fi
    echo "$sha256  $file" | sha256sum --check --quiet
}

zex_commit=8f71d418bae69a476a5a0e5c6e122c8801b8d9f4
mkdir -p "$dest/zexall"
download "https://raw.githubusercontent.com/agn453/ZEXALL/$zex_commit/zexdoc.com" "$dest/zexall/zexdoc.com" \
    34923a7ed82285d3038b2d54bd64899e12173eebb61f9d07b4fc72e78af2ae8f
download "https://raw.githubusercontent.com/agn453/ZEXALL/$zex_commit/zexall.com" "$dest/zexall/zexall.com" \
    6e2da55147a04f28d303d5da6a1e6b771557ac244653590a0f24a2d39c8537e8
download "https://raw.githubusercontent.com/agn453/ZEXALL/$zex_commit/zexdoc.z80" "$dest/zexall/zexdoc.z80" \
    20312a2bbada7d78b2935715f72247ebb90c2de66424f86f14fd67b930b81880
download "https://raw.githubusercontent.com/agn453/ZEXALL/$zex_commit/zexall.z80" "$dest/zexall/zexall.z80" \
    cd30810760d5d580bbb5a19a05a480a9968166230d8d1e21c3dc86b234211266

mkdir -p "$dest/z80test"
download "https://github.com/raxoft/z80test/releases/download/v1.2a/z80test-1.2a.zip" \
    "$dest/z80test/z80test-1.2a.zip" 7df0443d703e6b3114ea04b4cdef3e13b91421c62e37185c1036d06864cacbaf
if [ ! -d "$dest/z80test/z80test-1.2a" ]; then
    unzip -q "$dest/z80test/z80test-1.2a.zip" -d "$dest/z80test"
fi
echo "test data complete in $dest"
