"""Reference images and design elements.

An image is measured, never uploaded: the browser reads its dimensions, MIME type,
byte size and dominant colours and sends only those numbers. This module turns them
into three things:

  a per-image check against what the endpoint documents (size, type, and whether the
  role you gave the image is one this endpoint can actually use),
  a palette anchor line, in the documented "Palette anchors: amber, cream, walnut
  brown" shape, built from colours read off your own images, and
  a reference sheet — the rules that change how you write once an image is in play,
  each of them quoted from the model owner that published it.

Nothing here invents a colour, a role or a rule. A role the endpoint cannot use is
reported as unusable rather than quietly dropped.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from .improve import load_design
from .storyboard import load_storyboard, role_catalog

MAX_IMAGES = 4
MAX_PALETTE = 5
HEX_RE = re.compile(r"^#?([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
ALLOWED_TYPES = ("image/jpeg", "image/png", "image/webp")


def reference_roles() -> List[Dict[str, Any]]:
    return role_catalog()


def _hex(value: str) -> Optional[str]:
    match = HEX_RE.match(str(value).strip())
    if not match:
        return None
    digits = match.group(1).lower()
    if len(digits) == 3:
        digits = "".join(ch * 2 for ch in digits)
    return "#" + digits


def clean_image(raw: Any, index: int) -> Optional[Dict[str, Any]]:
    """Validate one image's browser-side measurements."""
    if not isinstance(raw, dict):
        return None
    try:
        width = int(raw.get("width") or 0)
        height = int(raw.get("height") or 0)
        size = int(raw.get("bytes") or 0)
    except (TypeError, ValueError):
        return None
    if not (0 <= width <= 8192 and 0 <= height <= 8192):
        return None
    palette = []
    for value in (raw.get("palette") or [])[:12]:
        colour = _hex(value)
        if colour and colour not in palette:
            palette.append(colour)
    role = str(raw.get("role") or "first_frame")
    known = {r["id"] for r in reference_roles()}
    return {
        "index": index,
        "role": role if role in known else "first_frame",
        "role_asked": role,
        "name": str(raw.get("name") or f"image-{index}")[:80],
        "width": width,
        "height": height,
        "type": str(raw.get("type") or "")[:40],
        "bytes": size,
        "palette": palette,
        "scene": raw.get("scene") if isinstance(raw.get("scene"), int) else None,
    }


def role_support(role_id: str, model_detail: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Whether this endpoint can use an image in this role, and how.

    Read out of the endpoint's own dataset entry: the documented image field, the
    documented parameters, and whether an image input exists at all.
    """
    ref = (model_detail or {}).get("reference") or {}
    params = (model_detail or {}).get("params") or {}
    accepts = bool(ref.get("accepts") or (model_detail or {}).get("accepts_image"))
    field = str(ref.get("field") or "")
    if not accepts:
        return {"supported": False, "how": None,
                "reason": "no image input is documented for this endpoint",
                "source": ref.get("source"), "evidence": ref.get("evidence")}
    if role_id == "last_frame" and "last_frame" not in field.lower():
        return {"supported": False, "how": None,
                "reason": f"this endpoint documents {field or 'one image'} only",
                "source": ref.get("source"), "evidence": ref.get("evidence")}
    if role_id == "subject" and "characters" in params:
        return {"supported": True, "how": "the characters parameter — up to 2 reusable characters",
                "source": ref.get("source"), "evidence": ref.get("evidence")}
    return {"supported": True, "how": field or "the image field",
            "source": ref.get("source"), "evidence": ref.get("evidence")}


def _documented_sizes(model_detail: Optional[Dict[str, Any]]) -> List[str]:
    params = (model_detail or {}).get("params") or {}
    for key in ("size", "resolution", "aspect_ratio"):
        entry = params.get(key)
        if isinstance(entry, dict) and isinstance(entry.get("default"), str):
            return [part.strip() for part in re.split(r"\s+or\s+", entry["default"]) if part.strip()]
    return []


def build_reference_set(raw_images: Any, model_detail: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Validate the set, check it against the endpoint, and build the palette line."""
    images: List[Dict[str, Any]] = []
    dropped: List[str] = []
    # A caller that sent something other than a list sent no images: `list(7)`
    # raises, and `list("no")` silently becomes two unusable entries.
    entries = raw_images[:MAX_IMAGES] if isinstance(raw_images, list) else []
    if raw_images not in (None, []) and not isinstance(raw_images, list):
        dropped.append("the reference images were not sent as a list")
    for index, raw in enumerate(entries, start=1):
        cleaned = clean_image(raw, index)
        if cleaned is None:
            dropped.append(f"image {index} had no usable measurements")
        else:
            images.append(cleaned)

    design = load_design()
    rules = design["image_reference"]
    ref_conf = (model_detail or {}).get("reference") or {}
    sizes = _documented_sizes(model_detail)
    checks: List[Dict[str, Any]] = []
    palette: List[str] = []
    used_roles: List[str] = []

    for image in images:
        if image["palette"]:
            for colour in image["palette"]:
                if colour not in palette:
                    palette.append(colour)
        support = role_support(image["role"], model_detail)
        if not support["supported"]:
            checks.append({
                "id": "role_not_supported",
                "severity": "warn",
                "image": image["index"],
                "detail": f"'{image['role']}' has nowhere to go on this endpoint: {support['reason']}. "
                          f"Describe it in words instead, or pick an endpoint that takes an image.",
                "source": support.get("source"),
                "evidence": support.get("evidence"),
            })
        else:
            used_roles.append(image["role"])
        if image["type"] and image["type"] not in ALLOWED_TYPES:
            checks.append({
                "id": "reference_type",
                "severity": "warn",
                "image": image["index"],
                "detail": f"{image['type']} is not in the documented set "
                          f"({', '.join(ALLOWED_TYPES)}).",
                "source": ref_conf.get("source"),
                "evidence": ref_conf.get("evidence"),
            })
        mine = f"{image['width']}x{image['height']}"
        if sizes and mine not in sizes and not any(
                mine == s for s in sizes) and all("x" in s for s in sizes):
            checks.append({
                "id": "reference_size",
                "severity": "warn",
                "image": image["index"],
                "detail": f"Your image is {mine}; this endpoint documents "
                          f"{' or '.join(sizes)}. A mismatch is rejected rather than resized.",
                "source": ref_conf.get("source"),
                "evidence": ref_conf.get("evidence"),
            })

    if len([i for i in images if i["role"] == "subject"]) > 2 and "characters" in (
            (model_detail or {}).get("params") or {}):
        checks.append({
            "id": "too_many_characters",
            "severity": "warn",
            "image": None,
            "detail": "This endpoint recommends no more than 2 reusable characters per generation.",
            "source": ref_conf.get("source"),
            "evidence": ref_conf.get("evidence"),
        })

    palette = palette[:MAX_PALETTE]
    block = _palette_line(palette)
    return {
        "images": images,
        "dropped": dropped,
        "roles": reference_roles(),
        "used_roles": sorted(set(used_roles)),
        "checks": checks,
        "palette": palette,
        "palette_line": block,
        "sheet": reference_sheet(images, model_detail),
        "model": (model_detail or {}).get("slug"),
        "accepts_image": bool((model_detail or {}).get("accepts_image")),
        "source": rules["source"],
        "evidence": rules["evidence"],
        "rules": rules["rules"],
    }


def _palette_line(palette: List[str]) -> Optional[str]:
    """The documented palette shape, filled with colours read off your own images."""
    if not palette:
        return None
    design = load_design()
    return {
        "line": "Palette anchors: " + ", ".join(palette),
        "colours": palette,
        "source": design["sources"]["sora"],
        "evidence": "Lighting + palette: 'Palette anchors: amber, cream, walnut brown' — name three to five colours.",
    }


def reference_sheet(images: List[Dict[str, Any]],
                    model_detail: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """The rules that change once an image is attached, quoted from their owners."""
    design = load_design()
    rules = design["image_reference"]
    lines = list(rules["rules"])
    if images:
        ref_conf = (model_detail or {}).get("reference") or {}
        if ref_conf.get("field"):
            lines.insert(0, f"Send it as: {ref_conf['field']}."
                            + (f" {ref_conf['constraints']}." if ref_conf.get("constraints") else ""))
    return {
        "lines": lines,
        "source": rules["motion_only_rule"]["source"],
        "evidence": rules["motion_only_rule"]["evidence"],
        "images": len(images),
    }


def apply_palette(prompt: str, palette_line: Optional[Dict[str, Any]]) -> str:
    """Append the palette anchors to a prompt, in the documented shape."""
    if not palette_line or not palette_line.get("line"):
        return prompt
    text = prompt.strip()
    if text and text[-1] not in ".!?":
        text += "."
    return f"{text} {palette_line['line']}."


def design_summary(selections: Dict[str, Any], palette_line: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Every element in a design set with the source it came from."""
    known = load_design()
    design = known["vocab"]
    out: List[Dict[str, Any]] = []
    for key, value in (selections or {}).items():
        values = value if isinstance(value, list) else [value]
        entry = design.get(key)
        # The group's source, resolved through the dataset's source table.
        group_source = None
        if entry:
            group_source = known["sources"].get(entry.get("source"), entry.get("source"))
        for item in values:
            known_value = bool(entry and item in entry.get("items", []))
            out.append({
                "element": key,
                "value": item,
                "known": known_value,
                # A value outside the documented vocabulary has no source: pointing
                # at the group's source would claim a provenance it does not have.
                "source": group_source if known_value else None,
                "evidence": entry.get("evidence") if known_value else None,
            })
    if palette_line and palette_line.get("colours"):
        out.append({
            "element": "palette",
            "value": ", ".join(palette_line["colours"]),
            "known": True,
            "source": palette_line["source"],
            "evidence": palette_line["evidence"],
        })
    return out
