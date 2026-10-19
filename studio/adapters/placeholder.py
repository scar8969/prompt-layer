"""Deterministic placeholder media, written with the standard library alone.

The development mock adapter has to hand a real, viewable file to the rest of the
pipeline -- object storage, the asset library, the image viewer, the download
endpoint -- without a provider, a network call, or an imaging library. Everything
here is built from zlib and struct.

Nothing in this module is a generated result. The picture is an identicon derived
from a seed string, so it carries no information about a prompt, and every caller
is expected to carry the mock flag through to the user. The module exists so that
the mock can be honest: it produces a file that really exists and really opens,
rather than a broken URL that pretends to be an image.
"""

from __future__ import annotations

import colorsys
import hashlib
import os
import struct
import zlib
from dataclasses import dataclass
from typing import List, Optional, Sequence, Tuple

RGB = Tuple[int, int, int]

#: Frames are deliberately small. Every pixel costs nine bits in the GIF stream
#: because the encoder below never grows its code table, so a placeholder that is
#: twice as wide would be four times the bytes for no extra usefulness.
DEFAULT_IMAGE_SIZE = 768
DEFAULT_VIDEO_SIZE = (160, 90)
DEFAULT_GIF_FRAMES = 8
DEFAULT_GIF_DELAY_MS = 110
IDENTICON_CELLS = 9

BACKGROUND_TOP = (13, 14, 18)
BACKGROUND_BOTTOM = (31, 33, 42)

#: Codes emitted between two CLEAR codes in the GIF stream. The decoder's table
#: gains one entry per code after the first, so staying under 255 keeps every
#: code nine bits wide and removes the whole class of encoder bugs where the
#: table grows and the two sides disagree about the code width.
CODES_PER_BLOCK = 250
CLEAR_CODE = 256
END_CODE = 257
MIN_CODE_SIZE = 8
CODE_SIZE = 9


@dataclass(frozen=True)
class Placeholder:
    """A written placeholder file, and what it honestly is."""

    path: str
    content_type: str
    width: int
    height: int
    byte_size: int
    seed: str
    frames: int = 1

    @property
    def is_video(self) -> bool:
        return self.content_type.startswith("video/")

    def to_public(self) -> dict:
        return {
            "content_type": self.content_type,
            "width": self.width,
            "height": self.height,
            "byte_size": self.byte_size,
            "frames": self.frames,
            "placeholder": True,
            "note": (
                "Development placeholder produced locally by the mock provider. "
                "It is not the output of any model."
            ),
        }


# ---------------------------------------------------------------------------
# Seed-derived geometry and colour
# ---------------------------------------------------------------------------


def seed_digest(seed: str) -> bytes:
    return hashlib.sha256((seed or "studio").encode("utf-8")).digest()


def identicon_pattern(seed: str, cells: int = IDENTICON_CELLS) -> List[List[bool]]:
    """A mirrored on/off grid derived from the seed.

    Mirrored down the vertical axis so the shape reads as a mark rather than as
    noise, which is what makes two different jobs visually distinguishable at a
    glance.
    """
    if cells < 2:
        cells = 2
    digest = seed_digest(seed)
    bits = int.from_bytes(digest[4:32], "big")
    half = (cells + 1) // 2
    grid = [[False] * cells for _ in range(cells)]
    index = 0
    for row in range(cells):
        for col in range(half):
            if (bits >> index) & 1:
                grid[row][col] = True
                grid[row][cells - 1 - col] = True
            index += 1
    if not any(any(line) for line in grid):
        centre = cells // 2
        grid[centre][centre] = True
    return grid


def _accent(seed: str, shift: int) -> RGB:
    digest = seed_digest(seed)
    hue = ((digest[shift] << 8) | digest[shift + 1]) / 65535.0
    saturation = 0.46 + (digest[shift + 2] / 255.0) * 0.26
    value = 0.66 + (digest[shift + 3] / 255.0) * 0.24
    red, green, blue = colorsys.hsv_to_rgb(hue, saturation, value)
    return (int(red * 255), int(green * 255), int(blue * 255))


def _mix(first: RGB, second: RGB, amount: float) -> RGB:
    amount = 0.0 if amount < 0.0 else (1.0 if amount > 1.0 else amount)
    return (
        int(first[0] + (second[0] - first[0]) * amount),
        int(first[1] + (second[1] - first[1]) * amount),
        int(first[2] + (second[2] - first[2]) * amount),
    )


def _tone(colour: RGB, factor: float) -> RGB:
    return (
        max(0, min(255, int(colour[0] * factor))),
        max(0, min(255, int(colour[1] * factor))),
        max(0, min(255, int(colour[2] * factor))),
    )


# ---------------------------------------------------------------------------
# PNG
# ---------------------------------------------------------------------------


def _png_chunk(tag: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )


def _frame_row_colours(
    seed: str, width: int, height: int, phase: float = 0.0
) -> List[List[RGB]]:
    """One row of colours per scanline, built as runs rather than per pixel."""
    grid = identicon_pattern(seed)
    cells = len(grid)
    accent = _accent(seed, 0)
    accent_alt = _accent(seed, 6)

    box = int(min(width, height) * 0.62)
    box = max(box, cells * 2)
    box -= box % cells
    cell = max(1, box // cells)
    left = (width - cell * cells) // 2
    top = (height - cell * cells) // 2

    rows: List[List[RGB]] = []
    for y in range(height):
        base = _mix(BACKGROUND_TOP, BACKGROUND_BOTTOM, y / max(1, height - 1))
        # A slow diagonal wash keeps a large flat frame from looking like a bug.
        wash = 0.06 + 0.06 * (1.0 + _wave(y / max(1, height - 1), phase)) / 2.0
        row: List[RGB] = [_mix(base, accent, wash)] * width
        if top <= y < top + cell * cells:
            cell_row = (y - top) // cell
            for column in range(cells):
                if not grid[cell_row][column]:
                    continue
                shade = (cell_row + column) / max(1, (cells - 1) * 2)
                colour = _mix(accent, accent_alt, shade + 0.12 * phase)
                start = left + column * cell
                row[start : start + cell] = [colour] * cell
        rows.append(row)
    return rows


def _wave(position: float, phase: float) -> float:
    """A smooth 0..1 pulse. Used instead of random so a seed is reproducible."""
    value = (position * 6.283185307179586) + phase * 6.283185307179586
    return (1.0 - _cos(value)) / 2.0


def _cos(value: float) -> float:
    import math

    return math.cos(value)


def png_bytes(seed: str, width: int = DEFAULT_IMAGE_SIZE, height: Optional[int] = None) -> bytes:
    """A real PNG for the given seed, encoded here rather than by a library."""
    height = width if height is None else height
    if width < 1 or height < 1:
        raise ValueError("A placeholder needs a positive size")

    rows = _frame_row_colours(seed, width, height)
    raw = bytearray()
    for row in rows:
        raw.append(0)
        for colour in row:
            raw.append(colour[0])
            raw.append(colour[1])
            raw.append(colour[2])

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + _png_chunk(b"IEND", b"")
    )


# ---------------------------------------------------------------------------
# GIF
# ---------------------------------------------------------------------------


def _palette_table() -> Tuple[List[RGB], dict]:
    """A 256-entry palette, and the quantiser that addresses it.

    Eight red levels by eight green levels by four blue levels. A placeholder is
    a mark and a wash, not a photograph, so the banding this introduces costs
    nothing and the exact index arithmetic means no search is needed.
    """
    table: List[RGB] = []
    lookup: dict = {}
    red_steps = [int(round(level * 255 / 7)) for level in range(8)]
    green_steps = [int(round(level * 255 / 7)) for level in range(8)]
    blue_steps = [int(round(level * 255 / 3)) for level in range(4)]
    for red in red_steps:
        for green in green_steps:
            for blue in blue_steps:
                lookup[(red, green, blue)] = len(table)
                table.append((red, green, blue))
    return table, lookup


def _quantise(colour: RGB, lookup: dict) -> int:
    red = min(7, max(0, colour[0] * 8 // 256))
    green = min(7, max(0, colour[1] * 8 // 256))
    blue = min(3, max(0, colour[2] * 4 // 256))
    key = (
        int(round(red * 255 / 7)),
        int(round(green * 255 / 7)),
        int(round(blue * 255 / 3)),
    )
    return lookup[key]


def _pack_codes(codes: Sequence[int]) -> bytes:
    """Pack fixed-width codes least-significant-bit first, as GIF requires."""
    out = bytearray()
    accumulator = 0
    bits = 0
    mask = (1 << CODE_SIZE) - 1
    for code in codes:
        accumulator |= (code & mask) << bits
        bits += CODE_SIZE
        while bits >= 8:
            out.append(accumulator & 0xFF)
            accumulator >>= 8
            bits -= 8
    if bits:
        out.append(accumulator & 0xFF)
    return bytes(out)


def _lzw_stream(indices: Sequence[int]) -> bytes:
    """A valid GIF LZW stream that never lets its code table grow.

    A CLEAR code is emitted every CODES_PER_BLOCK pixels. The encoder therefore
    only ever emits root codes, every code stays nine bits wide, and the decoder
    rebuilds exactly the same table the encoder assumed. It costs a few percent
    over a fully adaptive encoder and removes the code-width transition that is
    the usual source of unreadable GIFs.
    """
    codes: List[int] = []
    since_clear = 0
    for value in indices:
        if since_clear >= CODES_PER_BLOCK:
            codes.append(CLEAR_CODE)
            since_clear = 0
        if since_clear == 0:
            codes.append(CLEAR_CODE)
        codes.append(value & 0xFF)
        since_clear += 1
    codes.append(END_CODE)
    return _pack_codes(codes)


def _sub_blocks(payload: bytes) -> bytes:
    out = bytearray()
    for start in range(0, len(payload), 255):
        block = payload[start : start + 255]
        out.append(len(block))
        out.extend(block)
    out.append(0)
    return bytes(out)


def gif_bytes(
    seed: str,
    width: int = DEFAULT_VIDEO_SIZE[0],
    height: int = DEFAULT_VIDEO_SIZE[1],
    *,
    frames: int = DEFAULT_GIF_FRAMES,
    delay_ms: int = DEFAULT_GIF_DELAY_MS,
) -> bytes:
    """An animated GIF standing in for a video result.

    Deliberately a GIF rather than a fabricated MP4: the pipeline, the asset
    library and the player are all exercised by a real moving image, and nobody
    can mistake the file for a model's video output.
    """
    frames = max(2, min(int(frames), 48))
    table, lookup = _palette_table()
    delay = max(2, int(round(delay_ms / 10.0)))

    header = b"GIF89a"
    screen = struct.pack("<HHBBB", width, height, 0xF7, 0, 0)
    colour_table = b"".join(struct.pack("BBB", *colour) for colour in table)
    loop = b"\x21\xff\x0bNETSCAPE2.0\x03\x01\x00\x00\x00"

    body = bytearray()
    for index in range(frames):
        phase = index / float(frames)
        rows = _frame_row_colours(seed, width, height, phase)
        indices: List[int] = []
        for row in rows:
            for colour in row:
                indices.append(_quantise(colour, lookup))
        body.extend(b"\x21\xf9\x04\x04")
        body.extend(struct.pack("<H", delay))
        body.extend(b"\x00\x00")
        body.extend(b"\x2c")
        body.extend(struct.pack("<HHHHB", 0, 0, width, height, 0))
        body.append(MIN_CODE_SIZE)
        body.extend(_sub_blocks(_lzw_stream(indices)))

    return header + screen + colour_table + loop + bytes(body) + b"\x3b"


# ---------------------------------------------------------------------------
# Writing
# ---------------------------------------------------------------------------


def for_modality(seed: str, modality: str) -> Tuple[bytes, str, int, int, int]:
    """The bytes, content type, size and frame count for one modality."""
    if (modality or "").strip().lower() == "video":
        width, height = DEFAULT_VIDEO_SIZE
        return gif_bytes(seed, width, height), "image/gif", width, height, DEFAULT_GIF_FRAMES
    return (
        png_bytes(seed, DEFAULT_IMAGE_SIZE),
        "image/png",
        DEFAULT_IMAGE_SIZE,
        DEFAULT_IMAGE_SIZE,
        1,
    )


def write_placeholder(directory: str, seed: str, modality: str = "image") -> Placeholder:
    """Write the placeholder to a directory, atomically, and describe it."""
    payload, content_type, width, height, frames = for_modality(seed, modality)
    os.makedirs(directory, exist_ok=True)
    suffix = ".gif" if content_type == "image/gif" else ".png"
    stem = hashlib.sha256((seed or "studio").encode("utf-8")).hexdigest()[:20]
    path = os.path.join(directory, "mock-" + stem + suffix)
    temporary = path + ".part"
    with open(temporary, "wb") as handle:
        handle.write(payload)
    os.replace(temporary, path)
    return Placeholder(
        path=path,
        content_type=content_type,
        width=width,
        height=height,
        byte_size=len(payload),
        seed=seed,
        frames=frames,
    )
