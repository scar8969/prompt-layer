"""Upload validation by content sniffing, header-only metadata probes, thumbnails.

What this module decides, and what it refuses
---------------------------------------------
Every file is judged by its bytes, never by the name it arrived with and never by
the content type a client declared. A Windows executable renamed to .png is
refused twice over: the header proves no image format we accept, and, if a type
was declared, the declared type disagrees with the bytes.

Metadata is read from headers only. Nothing here decodes a frame, walks a pixel
stream, or trusts a length field it has not bounds-checked. When the bytes are
shorter than the structure they describe, or a container box cannot be
understood, the probe returns zeroes rather than a guess. A zero in MediaInfo
means "was not read", not "is zero".

The standard library is the only import-time dependency. Pillow is imported
lazily and only inside thumbnail(); without it this module still imports and
thumbnails are simply not produced.

What the video probes can and cannot read is stated per function. In short: the
MP4 walker reads an unfragmented file's mvhd duration and tkhd dimensions, and
WebM is refused rather than faked.
"""

from __future__ import annotations

import io
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .config import MediaConfig
from .errors import ApiError, CAPABILITY_UNSUPPORTED, VALIDATION_FAILED
from .util import humanize_bytes, truncate

# -- Defaults ---------------------------------------------------------------
#: Ceilings used when no MediaConfig is supplied. They are deliberately lower
#: than the download ceiling: an upload arrives on a request thread, a generated
#: artifact does not.
DEFAULT_MAX_IMAGE_BYTES = 20 * 1024 * 1024
DEFAULT_MAX_VIDEO_BYTES = 200 * 1024 * 1024
DEFAULT_MAX_AUDIO_BYTES = 40 * 1024 * 1024

# -- Signatures -------------------------------------------------------------
# Each content type maps to the byte prefixes that prove it. For the containers
# whose proof sits at a fixed offset rather than at byte 0 -- RIFF and ISO base
# media -- the prefix listed here is the part of that proof that must also be
# true; the offset requirements live in _OFFSET_REQUIREMENTS and are what
# sniff_type actually enforces.

IMAGE_SIGNATURES: Dict[str, Tuple[bytes, ...]] = {
    "image/png": (b"\x89PNG\r\n\x1a\n",),
    "image/jpeg": (b"\xff\xd8\xff",),
    "image/gif": (b"GIF87a", b"GIF89a"),
    "image/webp": (b"RIFF",),
    "image/bmp": (b"BM",),
}

VIDEO_SIGNATURES: Dict[str, Tuple[bytes, ...]] = {
    "video/mp4": (b"ftyp",),
    "video/webm": (b"\x1a\x45\xdf\xa3",),
    "video/quicktime": (b"ftypqt  ",),
}

AUDIO_SIGNATURES: Dict[str, Tuple[bytes, ...]] = {
    "audio/mpeg": (b"ID3", b"\xff\xfb"),
    "audio/wav": (b"RIFF",),
}

#: Containers that share a prefix and are told apart by a marker deeper in the
#: header. Every pair in the tuple must hold; one mismatch means no match.
_OFFSET_REQUIREMENTS: Dict[str, Tuple[Tuple[int, bytes], ...]] = {
    "image/webp": ((0, b"RIFF"), (8, b"WEBP")),
    "audio/wav": ((0, b"RIFF"), (8, b"WAVE")),
    "video/quicktime": ((4, b"ftypqt  "),),
    "video/mp4": ((4, b"ftyp"),),
}

#: Longest header any single check needs to read.
_SNIFF_WINDOW = 16

#: The order signatures are tried in. QuickTime before MP4 because an MOV file is
#: an MP4 file with a different brand, and BMP last because "BM" is two bytes that
#: many unrelated files happen to start with.
_SNIFF_ORDER: Tuple[str, ...] = (
    "video/quicktime",
    "video/mp4",
    "image/webp",
    "audio/wav",
    "image/png",
    "image/jpeg",
    "image/gif",
    "video/webm",
    "audio/mpeg",
    "image/bmp",
)

ALLOWED_IMAGE_TYPES = frozenset(IMAGE_SIGNATURES)
ALLOWED_VIDEO_TYPES = frozenset(VIDEO_SIGNATURES)
ALLOWED_AUDIO_TYPES = frozenset(AUDIO_SIGNATURES)

_KIND_OF: Dict[str, str] = {}
for _content_type in ALLOWED_IMAGE_TYPES:
    _KIND_OF[_content_type] = "image"
for _content_type in ALLOWED_VIDEO_TYPES:
    _KIND_OF[_content_type] = "video"
for _content_type in ALLOWED_AUDIO_TYPES:
    _KIND_OF[_content_type] = "audio"
del _content_type

#: Declared names that mean a type we know by another name. Compared lowercased
#: and without parameters, so image/jpg is a JPEG and not a lie.
_DECLARED_ALIASES: Dict[str, str] = {
    "image/jpg": "image/jpeg",
    "image/pjpeg": "image/jpeg",
    "image/x-png": "image/png",
    "image/x-ms-bmp": "image/bmp",
    "video/mov": "video/quicktime",
    "video/x-m4v": "video/mp4",
    "video/mp4v-es": "video/mp4",
    "audio/mp3": "audio/mpeg",
    "audio/mpeg3": "audio/mpeg",
    "audio/x-mp3": "audio/mpeg",
    "audio/wave": "audio/wav",
    "audio/x-wav": "audio/wav",
}

_EXTENSION_OF: Dict[str, str] = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/webp": ".webp",
    "image/bmp": ".bmp",
    "video/mp4": ".mp4",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
    "audio/mpeg": ".mp3",
    "audio/wav": ".wav",
}

#: How a size message names the limit it enforced.
_LIMIT_LABELS: Dict[str, str] = {
    "upload": "an upload",
    "reference": "a reference upload",
    "output": "generated output",
}


# -- Small readers ----------------------------------------------------------
def _be16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 2], "big")


def _be32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 4], "big")


def _be64(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 8], "big")


def _le16(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 2], "little")


def _le24(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 3], "little")


def _le32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 4], "little")


# -- Sniffing ---------------------------------------------------------------
def sniff_type(data: bytes) -> str:
    """The content type the first bytes of data prove, or "" for no match.

    Accepts any bytes, including an empty string, a truncated header, or the body
    of an unrelated format: those return "". It never raises, and it never
    consults a declared type or a file name -- a caller that has one must compare
    it against this answer, not pass it in here.

    Refuses to answer on a prefix alone when the format needs more than a prefix:
    RIFF headers are a WebP or a WAV only when the marker at offset 8 says so, and
    a bare ftyp box at offset 4 is MP4 unless the brand is QuickTime's.
    """
    if not data:
        return ""
    head = data[:_SNIFF_WINDOW]
    for content_type in _SNIFF_ORDER:
        requirements: Optional[Tuple[Tuple[int, bytes], ...]] = _OFFSET_REQUIREMENTS.get(
            content_type
        )
        if requirements is not None:
            matched = True
            for offset, marker in requirements:
                if head[offset : offset + len(marker)] != marker:
                    matched = False
                    break
            if matched:
                return content_type
            continue
        prefixes = (
            IMAGE_SIGNATURES.get(content_type)
            or VIDEO_SIGNATURES.get(content_type)
            or AUDIO_SIGNATURES.get(content_type)
            or ()
        )
        for prefix in prefixes:
            if head.startswith(prefix):
                return content_type
    return ""


def _kind_for(content_type: str) -> str:
    return _KIND_OF.get(content_type, "")


# -- Image probes -----------------------------------------------------------
def png_size(data: bytes) -> Tuple[int, int]:
    """Width and height from a PNG's IHDR chunk.

    Accepts a complete PNG or only the first 24 bytes of one: the dimensions are
    in the first chunk, so the image data is never touched. Refuses anything
    whose signature is not PNG, whose first chunk is not IHDR of length 13, or
    whose width or height is not a positive integer, and returns (0, 0) instead
    of raising.
    """
    if len(data) < 24 or not data.startswith(b"\x89PNG\r\n\x1a\n"):
        return (0, 0)
    if data[12:16] != b"IHDR":
        return (0, 0)
    if _be32(data, 8) != 13:
        return (0, 0)
    width = _be32(data, 16)
    height = _be32(data, 20)
    if width <= 0 or height <= 0:
        return (0, 0)
    return (width, height)


def gif_size(data: bytes) -> Tuple[int, int]:
    """Width and height from a GIF's logical screen descriptor.

    Accepts GIF87a and GIF89a, and only the first 10 bytes are required. Refuses
    anything without a GIF signature, and returns (0, 0) for a zero dimension
    rather than raising.
    """
    if len(data) < 10:
        return (0, 0)
    if not (data.startswith(b"GIF87a") or data.startswith(b"GIF89a")):
        return (0, 0)
    width = _le16(data, 6)
    height = _le16(data, 8)
    if width <= 0 or height <= 0:
        return (0, 0)
    return (width, height)


#: Start-of-frame markers. C4, C8 and CC sit in the same range but are Huffman
#: table, JPEG extension and arithmetic coding conditioning segments.
_JPEG_SOF_MARKERS = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}


def jpeg_size(data: bytes) -> Tuple[int, int]:
    """Width and height from a JPEG, by walking its markers to a frame header.

    Accepts any JPEG whose marker chain is intact up to a start-of-frame segment
    (baseline, extended and progressive all carry the same two fields), including
    one truncated right after that segment. Refuses data without the SOI
    signature, a chain that runs into entropy-coded scan data before a frame
    header, a segment length under 2, and a header that ends early; each of those
    returns (0, 0) rather than raising.
    """
    if len(data) < 4 or not data.startswith(b"\xff\xd8\xff"):
        return (0, 0)
    total = len(data)
    offset = 2
    while offset + 4 <= total:
        if data[offset] != 0xFF:
            return (0, 0)
        marker = data[offset + 1]
        if marker == 0xFF:  # a fill byte before the real marker
            offset += 1
            continue
        if marker == 0xD8 or marker == 0x01 or 0xD0 <= marker <= 0xD7:
            offset += 2  # standalone markers carry no length field
            continue
        if marker == 0xD9 or marker == 0xDA:
            return (0, 0)  # end of image, or scan data before any frame header
        segment_length = _be16(data, offset + 2)
        if segment_length < 2:
            return (0, 0)
        if marker in _JPEG_SOF_MARKERS:
            if offset + 9 > total:
                return (0, 0)
            height = _be16(data, offset + 5)
            width = _be16(data, offset + 7)
            if width <= 0 or height <= 0:
                return (0, 0)
            return (width, height)
        offset += 2 + segment_length
    return (0, 0)


def webp_size(data: bytes) -> Tuple[int, int]:
    """Width and height from a WebP's VP8, VP8L or VP8X chunk.

    Walks the RIFF chunk list from byte 12 and decodes the first frame header it
    finds. Accepts lossy (VP8), lossless (VP8L) and extended (VP8X) files, and a
    file truncated right after that chunk header. Refuses a RIFF container that
    is not WebP, an unknown chunk layout, a VP8 start code that is not there, and
    a VP8X chunk shorter than its fixed fields; each returns (0, 0).
    """
    if len(data) < 16 or data[0:4] != b"RIFF" or data[8:12] != b"WEBP":
        return (0, 0)
    total = len(data)
    offset = 12
    while offset + 8 <= total:
        fourcc = data[offset : offset + 4]
        size = _le32(data, offset + 4)
        payload = offset + 8
        available = min(payload + size, total) - payload
        if fourcc == b"VP8X":
            if available < 10:
                return (0, 0)
            width = _le24(data, payload + 4) + 1
            height = _le24(data, payload + 7) + 1
            return (width, height)
        if fourcc == b"VP8 ":
            if available < 10 or data[payload + 3 : payload + 6] != b"\x9d\x01\x2a":
                return (0, 0)
            width = _le16(data, payload + 6) & 0x3FFF
            height = _le16(data, payload + 8) & 0x3FFF
            if width <= 0 or height <= 0:
                return (0, 0)
            return (width, height)
        if fourcc == b"VP8L":
            if available < 5 or data[payload] != 0x2F:
                return (0, 0)
            bits = _le32(data, payload + 1)
            return ((bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1)
        offset = payload + size + (size & 1)  # chunks are padded to even lengths
    return (0, 0)


def bmp_size(data: bytes) -> Tuple[int, int]:
    """Width and height from a BMP's DIB header.

    Accepts the 40-byte and larger BITMAPINFOHEADER family, read as signed 32-bit
    fields because BMP stores a top-down bitmap with a negative height. Refuses
    anything without the "BM" signature and a header shorter than 26 bytes; both
    return (0, 0).
    """
    if len(data) < 26 or not data.startswith(b"BM"):
        return (0, 0)
    header_size = _le32(data, 14)
    if header_size < 12:
        return (0, 0)
    width = int.from_bytes(data[18:22], "little", signed=True)
    height = int.from_bytes(data[22:26], "little", signed=True)
    if width <= 0 or height == 0:
        return (0, 0)
    return (width, abs(height))


def image_size(data: bytes) -> Tuple[int, int]:
    """Width and height for whatever image format the bytes prove.

    Accepts PNG, JPEG, GIF, WebP and BMP, and dispatches on the sniffed type, not
    on a caller's claim. Refuses every other input -- an unknown format, a
    truncated header, a video -- by returning (0, 0) and does not raise, so a
    caller can inspect a file whose dimensions are simply not readable.
    """
    content_type = sniff_type(data)
    if content_type == "image/png":
        return png_size(data)
    if content_type == "image/jpeg":
        return jpeg_size(data)
    if content_type == "image/gif":
        return gif_size(data)
    if content_type == "image/webp":
        return webp_size(data)
    if content_type == "image/bmp":
        return bmp_size(data)
    return (0, 0)


# -- Video probes -----------------------------------------------------------
#: Boxes the MP4 walker will descend into when looking for a child box.
_MP4_CONTAINERS = (b"moov", b"trak", b"mdia", b"minf", b"stbl", b"edts")

#: Guards against a hand-crafted file that nests containers to exhaust the stack.
_MP4_MAX_DEPTH = 8


def _mp4_boxes(data: bytes, start: int, end: int) -> List[Tuple[bytes, int, int]]:
    """Boxes at one level: (type, payload start, box end). Stops on junk."""
    boxes: List[Tuple[bytes, int, int]] = []
    offset = start
    while offset + 8 <= end:
        size = _be32(data, offset)
        box_type = data[offset + 4 : offset + 8]
        header = 8
        if size == 1:
            if offset + 16 > end:
                break
            size = _be64(data, offset + 8)
            header = 16
        elif size == 0:
            size = end - offset
        if size < header or offset + size > end:
            break
        boxes.append((box_type, offset + header, offset + size))
        offset += size
    return boxes


def _mp4_find(
    data: bytes, start: int, end: int, wanted: bytes, depth: int = 0
) -> Optional[Tuple[int, int]]:
    for box_type, payload_start, box_end in _mp4_boxes(data, start, end):
        if box_type == wanted:
            return (payload_start, box_end)
        if depth < _MP4_MAX_DEPTH and box_type in _MP4_CONTAINERS:
            found = _mp4_find(data, payload_start, box_end, wanted, depth + 1)
            if found is not None:
                return found
    return None


def _mp4_find_all(
    data: bytes, start: int, end: int, wanted: bytes
) -> List[Tuple[int, int]]:
    return [
        (payload_start, box_end)
        for box_type, payload_start, box_end in _mp4_boxes(data, start, end)
        if box_type == wanted
    ]


def _mp4_mvhd_duration(data: bytes, payload: int, end: int) -> float:
    """Seconds from an mvhd payload, or 0.0 when its fields are not readable."""
    available = end - payload
    if available < 4:
        return 0.0
    version = data[payload]
    if version == 1:
        if available < 32:
            return 0.0
        timescale = _be32(data, payload + 20)
        duration = _be64(data, payload + 24)
    else:
        if available < 20:
            return 0.0
        timescale = _be32(data, payload + 12)
        duration = _be32(data, payload + 16)
    if timescale <= 0:
        return 0.0
    seconds = duration / float(timescale)
    if seconds < 0:
        return 0.0
    return round(seconds, 3)


def _mp4_tkhd_size(data: bytes, payload: int, end: int) -> Tuple[int, int]:
    """Pixels from a tkhd payload, which stores them as 16.16 fixed point."""
    available = end - payload
    if available < 4:
        return (0, 0)
    version = data[payload]
    width_offset = 88 if version == 1 else 76
    if available < width_offset + 8:
        return (0, 0)
    width = round(_be32(data, payload + width_offset) / 65536.0)
    height = round(_be32(data, payload + width_offset + 4) / 65536.0)
    if width <= 0 or height <= 0:
        return (0, 0)
    return (width, height)


def mp4_metadata(data: bytes) -> Tuple[float, int, int]:
    """(duration_seconds, width, height) from an MP4's moov box.

    Reads two fields and nothing else: the mvhd timescale and duration, and the
    first tkhd that carries non-zero dimensions. Only box headers and those
    payloads are touched, so a 300 MB file costs a few hundred bytes of reading.

    Accepts an unfragmented MP4 whose top-level boxes chain correctly, with moov
    either before or after mdat. Refuses fragmented MP4 (moof, sidx: the duration
    lives in fragments this walker does not follow), files whose boxes overrun
    their declared sizes, and any structure it does not recognise, returning
    (0.0, 0, 0) rather than a guess. It never reports frame counts or codecs.
    """
    if len(data) < 8:
        return (0.0, 0, 0)
    moov = _mp4_find(data, 0, len(data), b"moov")
    if moov is None:
        return (0.0, 0, 0)
    moov_start, moov_end = moov

    duration = 0.0
    mvhd = _mp4_find(data, moov_start, moov_end, b"mvhd")
    if mvhd is not None:
        duration = _mp4_mvhd_duration(data, mvhd[0], mvhd[1])

    width = 0
    height = 0
    for trak_start, trak_end in _mp4_find_all(data, moov_start, moov_end, b"trak"):
        tkhd = _mp4_find(data, trak_start, trak_end, b"tkhd")
        if tkhd is None:
            continue
        candidate_width, candidate_height = _mp4_tkhd_size(data, tkhd[0], tkhd[1])
        if candidate_width and candidate_height:
            width, height = candidate_width, candidate_height
            break

    return (duration, width, height)


def webm_metadata(data: bytes) -> Tuple[float, int, int]:
    """Always (0.0, 0, 0). Deliberately refuses to report WebM metadata.

    Accepts any bytes and returns nothing from them: WebM's duration and
    dimensions live in EBML elements whose variable-length integer encoding,
    size-unknown cluster handling and per-track settings this module does not
    decode. The signature is kept so inspect() can treat every video the same
    way. It will not invent a duration, and it is not a stub that starts
    answering later without this docstring changing.
    """
    return (0.0, 0, 0)


# -- The report -------------------------------------------------------------
@dataclass(frozen=True)
class MediaInfo:
    """What inspection proved about one file. A zero means "not read"."""

    content_type: str
    byte_size: int
    width: int = 0
    height: int = 0
    duration_seconds: float = 0.0
    frame_count: int = 0
    kind: str = ""

    def to_public(self) -> Dict[str, Any]:
        """The wire form for the UI: every key present, every value type fixed."""
        return {
            "content_type": self.content_type,
            "byte_size": self.byte_size,
            "width": self.width,
            "height": self.height,
            "duration_seconds": self.duration_seconds,
            "frame_count": self.frame_count,
            "kind": self.kind,
        }


# -- Inspection -------------------------------------------------------------
def _normalise_declared(declared_type: str) -> str:
    raw = (declared_type or "").strip().lower()
    if not raw:
        return ""
    raw = raw.split(";")[0].strip()
    return _DECLARED_ALIASES.get(raw, raw)


def _limit_message(kind: str, size: int, limit: int, context: str) -> str:
    label = _LIMIT_LABELS.get(context, "an upload")
    return "This %s is %s. The limit for %s is %s." % (
        kind,
        humanize_bytes(size),
        label,
        humanize_bytes(limit),
    )


def _inspect(
    data: bytes,
    declared_type: str,
    max_image_bytes: int,
    max_video_bytes: int,
    max_audio_bytes: int,
    context: str,
) -> MediaInfo:
    """The one implementation behind inspect() and validate_upload()."""
    size = len(data)
    if size == 0:
        raise ApiError(
            VALIDATION_FAILED,
            "The file is empty, so there is nothing to inspect.",
            field="file",
            details={"byte_size": 0},
        )

    content_type = sniff_type(data)
    if not content_type:
        raise ApiError(
            VALIDATION_FAILED,
            "This file is not an image, video or audio format this platform accepts.",
            field="file",
            details={
                "declared_type": truncate(declared_type or "", 80),
                "first_bytes_hex": data[:16].hex(),
            },
        )

    declared = _normalise_declared(declared_type)
    if declared and declared != content_type:
        raise ApiError(
            VALIDATION_FAILED,
            "This file is declared as %s but its bytes are %s. A file is stored as what it is, not as what it is called."
            % (truncate(declared, 80), content_type),
            field="file",
            details={
                "declared_type": truncate(declared, 80),
                "detected_type": content_type,
                "first_bytes_hex": data[:16].hex(),
            },
        )

    kind = _kind_for(content_type)
    limit = {
        "image": max_image_bytes,
        "video": max_video_bytes,
        "audio": max_audio_bytes,
    }[kind]
    if size > limit:
        raise ApiError(
            VALIDATION_FAILED,
            _limit_message(kind, size, limit, context),
            field="file",
            details={
                "content_type": content_type,
                "byte_size": size,
                "limit_bytes": limit,
            },
        )

    width = 0
    height = 0
    duration = 0.0
    if kind == "image":
        width, height = image_size(data)
    elif kind == "video":
        if content_type == "video/mp4":
            duration, width, height = mp4_metadata(data)
        elif content_type == "video/webm":
            duration, width, height = webm_metadata(data)

    return MediaInfo(
        content_type=content_type,
        byte_size=size,
        width=width,
        height=height,
        duration_seconds=duration,
        frame_count=0,
        kind=kind,
    )


def inspect(
    data: bytes,
    declared_type: str = "",
    *,
    max_image_bytes: int = DEFAULT_MAX_IMAGE_BYTES,
    max_video_bytes: int = DEFAULT_MAX_VIDEO_BYTES,
    max_audio_bytes: int = DEFAULT_MAX_AUDIO_BYTES,
) -> MediaInfo:
    """Sniff data and report what the bytes prove, within the given ceilings.

    Accepts any non-empty byte string carrying a PNG, JPEG, GIF, WebP, BMP, MP4,
    WebM, QuickTime, MPEG audio or WAV signature, with or without a declared type
    that agrees with it. Dimensions are filled in for images, and duration and
    dimensions for MP4; every other field stays 0, which means "not read".

    Refuses, with ApiError of code validation_failed: empty data; bytes whose
    prefix proves no accepted format (details carry the declared type and the
    first 16 bytes as hex); a declared type that disagrees with the bytes,
    whichever side is wrong; and a file over the ceiling for its kind. Raises
    nothing else, and never raises for a file it accepts.
    """
    return _inspect(
        data,
        declared_type,
        max_image_bytes,
        max_video_bytes,
        max_audio_bytes,
        "upload",
    )


def validate_upload(
    data: bytes,
    *,
    declared_type: str = "",
    purpose: str = "reference",
    config: Optional[MediaConfig] = None,
) -> MediaInfo:
    """Inspect an upload the way a route should: by purpose and by workspace policy.

    purpose="reference" treats the file as something a user supplied to guide
    generation; purpose="output" treats it as something a model produced and the
    platform is about to store. Both apply the same ceilings and the same
    sniffing, and the refusal message names which ceiling was hit.

    When config is a MediaConfig, its max_upload_bytes,
    max_video_upload_bytes, allowed_image_types and allowed_video_types replace
    the defaults and the built-in allowlists. With config=None the module
    defaults apply and every sniffable type is allowed.

    Accepts the same bytes inspect() accepts. Refuses a purpose other than the
    two named above (validation_failed, because that is a bad request), a config
    that is neither None nor a MediaConfig (TypeError, because that is a
    programming error rather than a bad file), a content type the workspace has
    not enabled (capability_unsupported, details listing what is enabled), and
    everything inspect() refuses.
    """
    if purpose not in ("reference", "output"):
        raise ApiError(
            VALIDATION_FAILED,
            'The upload purpose must be "reference" or "output".',
            details={"purpose": truncate(str(purpose), 40)},
        )
    if config is not None and not isinstance(config, MediaConfig):
        raise TypeError(
            "config must be a MediaConfig or None, not %s" % type(config).__name__
        )

    max_image_bytes = DEFAULT_MAX_IMAGE_BYTES
    max_video_bytes = DEFAULT_MAX_VIDEO_BYTES
    max_audio_bytes = DEFAULT_MAX_AUDIO_BYTES
    allowed_images = ALLOWED_IMAGE_TYPES
    allowed_videos = ALLOWED_VIDEO_TYPES

    if config is not None:
        max_image_bytes = config.max_upload_bytes
        max_video_bytes = config.max_video_upload_bytes
        allowed_images = frozenset(config.allowed_image_types) or ALLOWED_IMAGE_TYPES
        allowed_videos = frozenset(config.allowed_video_types) or ALLOWED_VIDEO_TYPES

    content_type = sniff_type(data)
    if content_type:
        kind = _kind_for(content_type)
        allowed = True
        enabled: List[str] = []
        if kind == "image":
            allowed = content_type in allowed_images
            enabled = sorted(allowed_images)
        elif kind == "video":
            allowed = content_type in allowed_videos
            enabled = sorted(allowed_videos)
        if not allowed:
            raise ApiError(
                CAPABILITY_UNSUPPORTED,
                "%s files are not enabled for this workspace." % content_type,
                field="file",
                details={"content_type": content_type, "allowed_types": enabled},
            )

    return _inspect(
        data,
        declared_type,
        max_image_bytes,
        max_video_bytes,
        max_audio_bytes,
        "output" if purpose == "output" else "reference",
    )


# -- Thumbnails -------------------------------------------------------------
def thumbnail(data: bytes, *, max_edge: int = 320) -> Tuple[bytes, str]:
    """A PNG thumbnail no larger than max_edge on either side.

    Accepts any image bytes Pillow can decode, including formats this module
    would otherwise refuse; the size is kept, format support is Pillow's. Returns
    (png_bytes, "image/png").

    Returns (b"", "") -- never a placeholder image and never an exception -- when
    Pillow is not importable, when the bytes do not decode, when data is empty,
    or when max_edge is not positive. A caller must treat the empty result as "no
    thumbnail was produced"; nothing here pretends one exists.
    """
    if not data or max_edge <= 0:
        return (b"", "")
    try:
        from PIL import Image
    except Exception:
        return (b"", "")
    try:
        with Image.open(io.BytesIO(data)) as image:
            image.load()
            working = image.copy()
        working.thumbnail((max_edge, max_edge))
        buffer = io.BytesIO()
        working.save(buffer, format="PNG")
        payload = buffer.getvalue()
        if not payload:
            return (b"", "")
        return (payload, "image/png")
    except Exception:
        return (b"", "")


def extension_for(content_type: str) -> str:
    """A leading-dot file extension for a content type, ".bin" when unknown.

    Accepts a bare content type such as image/png, or an entry from the ALLOWED_*
    sets. Refuses a parameterised header (image/png; charset=x, which it strips
    to the type), an empty string, and any type it does not know -- all of which
    answer ".bin", so a stored object never claims an extension it has not
    earned.
    """
    key = (content_type or "").strip().lower()
    if not key:
        return ".bin"
    key = key.split(";")[0].strip()
    return _EXTENSION_OF.get(key, ".bin")
