"""Generates the home-screen icons (no extra packages needed)."""
import struct
import zlib
from pathlib import Path

COLORS = [(59, 130, 246), (100, 116, 139), (59, 130, 246),
          (88, 101, 242), (245, 158, 11), (16, 185, 129),
          (29, 185, 84), (234, 179, 8), (239, 68, 68)]


def rounded(x, y, x0, y0, x1, y1, r):
    if not (x0 <= x < x1 and y0 <= y < y1):
        return False
    cx = min(max(x, x0 + r), x1 - r - 1)
    cy = min(max(y, y0 + r), y1 - r - 1)
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def make(size, path):
    pad = size * 0.16
    gap = size * 0.06
    cell = (size - 2 * pad - 2 * gap) / 3
    rad = int(cell * 0.24)
    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            px = (14, 14, 18)
            for i, c in enumerate(COLORS):
                cx0 = pad + (i % 3) * (cell + gap)
                cy0 = pad + (i // 3) * (cell + gap)
                if rounded(x, y, int(cx0), int(cy0), int(cx0 + cell), int(cy0 + cell), rad):
                    px = c
                    break
            row += bytes(px)
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)) \
        + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
    Path(path).write_bytes(png)


if __name__ == "__main__":
    web = Path(__file__).resolve().parent / "web"
    make(180, web / "icon-180.png")
    make(512, web / "icon-512.png")
    print("icons written")
