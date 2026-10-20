"""Minimal PNG encoder for RGBA images (standard library only).

Used for `screen_image` (MCP image content and files), so the headless server needs no imaging
library. Writes one IDAT chunk with filter type 0 (none) on every row.
"""

from __future__ import annotations

import struct
import zlib

import numpy as np

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _chunk(kind: bytes, data: bytes) -> bytes:
    """Return a PNG chunk: length, type, data, CRC over type and data."""
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def encode_png(image: np.ndarray) -> bytes:
    """Encode an RGBA image of shape (height, width, 4), dtype uint8, as PNG."""
    height, width, channels = image.shape
    if channels != 4 or image.dtype != np.uint8:
        raise ValueError("expected an RGBA uint8 image")
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)  # 8 bit, RGBA
    rows = np.zeros((height, 1 + width * 4), dtype=np.uint8)  # leading filter byte 0
    rows[:, 1:] = image.reshape(height, width * 4)
    return (
        PNG_SIGNATURE
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(rows.tobytes(), 6))
        + _chunk(b"IEND", b"")
    )
