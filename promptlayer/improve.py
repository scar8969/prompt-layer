"""How the output can be improved: design elements and a prompt lint.

Two deterministic operations, no model calls:

  lint_prompt      checks a prompt against the rules published by the model
                   owners, and returns findings that each carry a source.
  apply_design     merges chosen design elements (camera, light, lens, palette,
                   negatives, sound) into the prompt in a documented shape, and
                   reports exactly what it added.

The vocabulary in `design.json` is not this project's taste: every item and every
rule cites the model owner or the provider that published it. Nothing is sent
anywhere, so a reference image never leaves the browser — only its dimensions and
type are used, for the checks that need them.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
_DESIGN: Optional[Dict[str, Any]] = None

SEVERITY_PENALTY = {"error": 25, "warn": 12, "info": 4}

# Motion indicators, used only to decide whether *anything* in the prompt moves.
# Deliberately verbs and motion nouns rather than adjectives, because those are
# what a video model has to animate.
MOTION_HINTS = re.compile(
    r"\b(?:walk|walks|walking|run|runs|running|jog|jogs|step|steps|turn|turns|turning|"
    r"spin|spins|dance|dances|jump|jumps|fall|falls|falling|rise|rises|rising|drift|drifts|"
    r"float|floats|sway|sways|flutter|flutters|open|opens|close|closes|lift|lifts|reach|reaches|"
    r"pull|pulls|push|pushes|raise|raises|nod|nods|smile|smiles|blink|blinks|breathe|breathes|"
    r"pan|pans|tilt|tilts|dolly|zoom|zooms|track|tracks|orbit|orbits|circle|circles|"
    r"pour|pours|splash|splashes|burn|burns|crackle|crackles|steam|smoke|wind|rain|snow|waves?|"
    r"motion|moving|movement|speed|slow-?motion|time-?lapse|action)\b",
    re.I,
)


def load_design() -> Dict[str, Any]:
    global _DESIGN
    if _DESIGN is None:
        with open(os.path.join(HERE, "design.json"), encoding="utf-8") as fh:
            _DESIGN = json.load(fh)
    return _DESIGN


def reload_design() -> Dict[str, Any]:
    global _DESIGN
    _DESIGN = None
    return load_design()


def design_version() -> str:
    return load_design()["version"]


def vocab() -> Dict[str, Any]:
    return load_design()["vocab"]


def _finding(check: Dict[str, Any], detail: str, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    source_key = check["source"]
    return {
        "id": check["id"],
        "severity": check["severity"],
        "title": check["title"],
        "why": check["why"],
        "fix": check["fix"],
        "detail": detail,
        "source": load_design()["sources"].get(source_key, source_key),
        "evidence": check["evidence"],
        **(extra or {}),
    }


def _words(text: str) -> int:
    return len(re.findall(r"\S+", text))


def _matches(patterns: List[str], text: str) -> List[str]:
    found: List[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, re.I):
            found.append(match.group(0))
    return found


def _move_pattern(item: str) -> re.Pattern:
    """Match a camera move in the forms people actually write.

    The vocabulary lists infinitives ("dolly in"), but a prompt says "dollies in"
    or "the camera pans left". Without this, the camera-conflict rule would stay
    silent on exactly the prompts it exists for.
    """
    words = item.split()
    if words and words[0].lower() in ("slow", "medium", "fast"):
        words = words[1:]  # the qualifier is optional in prose
    if not words:
        return re.compile(r"$^")
    head = words[0]
    stem = head[:-1] if head.endswith("y") else head
    parts = [rf"\b{re.escape(stem)}(?:y|ies|ying|s|ed|ing)?"]
    for word in words[1:]:
        parts.append(r"\s+" + re.escape(word))
    return re.compile("".join(parts), re.I)


def _find_moves(text: str) -> List[str]:
    return [item for item in vocab()["camera_move"]["items"] if _move_pattern(item).search(text)]


def lint_prompt(
    prompt: str,
    model_layers: Optional[List[Dict[str, Any]]] = None,
    image_count: int = 0,
    accepts_image: bool = False,
    clip_seconds: Optional[int] = None,
) -> Dict[str, Any]:
    """Run every published rule against the prompt. Returns findings, not opinions."""
    design = load_design()
    findings: List[Dict[str, Any]] = []

    for check in design["checks"]:
        cid = check["id"]

        if cid == "camera_conflict":
            moves = _find_moves(prompt)
            conflict = None
            for left, right in check["opposing_pairs"]:
                if _move_pattern(left).search(prompt) and _move_pattern(right).search(prompt):
                    conflict = f"{left} + {right}"
                    break
            if conflict:
                findings.append(_finding(check, f"Opposing moves: {conflict}.", {"found": [conflict]}))
            elif len(moves) > check["max_moves"]:
                findings.append(_finding(check, f"{len(moves)} camera moves: {', '.join(moves)}.", {"found": moves}))
            continue

        if cid == "dialogue_too_long":
            quoted = re.findall(r"[“\"]([^”\"]{2,})[”\"]", prompt)
            total = sum(_words(q) for q in quoted)
            if quoted and total > check["max_words_in_quotes"]:
                findings.append(_finding(
                    check,
                    f"{total} words of speech across {len(quoted)} line(s) — the rule assumes a 4-8s clip.",
                    {"found": quoted, "limit": check["max_words_in_quotes"]},
                ))
            continue

        if cid == "length":
            n = _words(prompt)
            if 0 < n < check["too_short_words"]:
                findings.append(_finding(check, f"{n} words — very open; the model will invent the rest.",
                                         {"words": n}))
            elif n > check["too_long_words"]:
                findings.append(_finding(check, f"{n} words — long enough to constrain the model's own choices.",
                                         {"words": n}))
            continue

        if cid == "no_motion":
            if not MOTION_HINTS.search(prompt):
                findings.append(_finding(check, "No verb or motion noun found in the prompt.", {}))
            continue

        # Generic pattern checks, with an optional exemption list and an optional
        # inverted mode for rules that fire when something is *missing*.
        hits = _matches(check.get("patterns", []), prompt)
        unless = _matches(check.get("unless", []), prompt)
        if check.get("require_absent"):
            if not hits:
                findings.append(_finding(check, "Nothing about light, colour or grade.", {}))
            continue
        if hits and not unless:
            found = sorted(set(hits))
            findings.append(_finding(check, f"Found: {', '.join(found[:6])}{'…' if len(found) > 6 else ''}",
                                     {"found": found}))

    # A rewriting layer plus a hand-written prompt is double-prompting.
    for layer in model_layers or []:
        if layer.get("active") and layer.get("kind") in ("rewrite", "expand"):
            findings.append({
                "id": "rewriting_layer_active",
                "severity": "warn",
                "title": "This endpoint will rewrite your prompt anyway",
                "why": "You are paying attention to wording that the provider replaces before generation.",
                "fix": (layer.get("disable_with") or {}).get("prompt_optimizer") is False
                        and "Send prompt_optimizer=false to keep your wording, or write three words and let it expand."
                        or "Either disable the layer or trust it and write a short intent, not a paragraph.",
                "detail": f"{layer['id']} is active on this endpoint.",
                "source": layer.get("source"),
                "evidence": layer.get("evidence"),
            })

    # Reference-image guidance, from the image-to-video rulebooks.
    image_rules = design["image_reference"]
    if image_count > 0:
        if not accepts_image:
            findings.append({
                "id": "image_not_supported",
                "severity": "error",
                "title": "This endpoint does not take an image",
                "why": "An image you attach here has nowhere to go; pick an image-to-video model instead.",
                "fix": "Use an image-to-video endpoint (Wan 2.2 i2v, Seedance i2v, LTX-2 i2v, Kling i2v, Hailuo i2v) or Sora's input_reference.",
                "detail": "No image input is documented for this model.",
                "source": image_rules["source"],
                "evidence": image_rules["evidence"],
            })
        else:
            findings.append({
                "id": "image_reference_rules",
                "severity": "info",
                "title": "You supplied a reference image — change how you write",
                "why": image_rules["rules"][0],
                "fix": image_rules["rules"][1],
                "detail": " · ".join(image_rules["rules"][2:]),
                "source": image_rules["motion_only_rule"]["source"],
                "evidence": image_rules["motion_only_rule"]["evidence"],
            })

    penalty = sum(SEVERITY_PENALTY.get(f["severity"], 0) for f in findings)
    return {
        "findings": findings,
        "counts": {
            sev: sum(1 for f in findings if f["severity"] == sev) for sev in ("error", "warn", "info")
        },
        "score": max(0, 100 - penalty),
        "score_note": "100 minus 25 per error, 12 per warning, 4 per note. A checklist reading, not a model score.",
        "clip_seconds": clip_seconds,
    }


ORDER = ["style", "time_of_day", "shot_size", "camera_move", "lens", "light_source",
         "light_quality", "tone", "composition", "negative", "audio"]

LABELS = {
    "style": "Style", "time_of_day": "Time", "shot_size": "Shot", "camera_move": "Camera",
    "lens": "Lens", "light_source": "Light", "light_quality": "Light quality",
    "tone": "Palette", "composition": "Composition", "negative": "Negative", "audio": "Sound",
}


def apply_design(
    prompt: str,
    selections: Optional[Dict[str, Any]] = None,
    template: str = "inline",
    palette: Optional[str] = None,
) -> Dict[str, Any]:
    """Merge design elements into the prompt, in a documented shape.

    Selections are validated against the vocabulary: a value we cannot source is
    reported back in `unknown` rather than silently written into your prompt.
    """
    design = load_design()
    template_conf = design["templates"].get(template)
    if template_conf is None:
        raise KeyError(template)
    selections = dict(selections or {})
    known = vocab()
    added: List[Dict[str, str]] = []
    unknown: List[str] = []

    def take(key: str) -> List[str]:
        value = selections.get(key)
        if value in (None, "", []):
            return []
        values = value if isinstance(value, list) else [value]
        allowed = known.get(key, {}).get("items", [])
        out = []
        for item in values:
            if item in allowed:
                out.append(item)
                added.append({"element": key, "value": item, "source": design["sources"].get(
                    known[key]["source"], known[key]["source"]), "evidence": known[key]["evidence"]})
            else:
                unknown.append(f"{key}={item}")
        return out

    parts: Dict[str, str] = {}
    for key in ORDER:
        if key == "negative":
            continue
        items = take(key)
        if items:
            parts[key] = ", ".join(items)
    negatives = take("negative")
    palette_value = (palette or "").strip()

    # Inline: one sentence of settings appended after the prose.
    settings_bits = []
    for key in ORDER:
        if key == "negative" or key not in parts:
            continue
        settings_bits.append(f"{LABELS[key]}: {parts[key]}")
    if palette_value:
        settings_bits.append(f"Palette anchors: {palette_value}")
        added.append({"element": "palette", "value": palette_value, "source": design["sources"]["sora"],
                      "evidence": "Lighting + palette: 'Palette anchors: amber, cream, walnut brown' — name three to five colours."})
    inline = prompt.strip()
    if settings_bits:
        inline = (inline + ("" if inline.endswith((".", "!", "?")) else ".") + " " + ". ".join(settings_bits) + ".")
    if negatives:
        inline += f" Negative prompt: {', '.join(negatives)}."

    if template == "inline":
        return {
            "prompt": inline,
            "template": "inline",
            "template_origin": template_conf["source"],
            "added": added,
            "unknown": unknown,
        }

    # Labelled: OpenAI's documented block shape, prose first.
    lines = [prompt.strip(), ""]
    lines.append("Cinematography:")
    shot = ", ".join(filter(None, [parts.get("shot_size"), parts.get("camera_move")]))
    if shot:
        lines.append(f"Camera shot: {shot}")
    if parts.get("lens"):
        lines.append(f"Lens: {parts['lens']}")
    light = ", ".join(filter(None, [parts.get("light_source"), parts.get("light_quality"),
                                    parts.get("tone"), parts.get("time_of_day")]))
    if light:
        lines.append(f"Lighting + palette: {light}")
    if palette_value:
        lines.append(f"Palette anchors: {palette_value}")
    if parts.get("composition"):
        lines.append(f"Composition: {parts['composition']}")
    if parts.get("style"):
        lines.append(f"Style: {parts['style']}")
    if parts.get("audio"):
        lines.append(f"Background Sound: {parts['audio']}")
    if negatives:
        lines.append(f"Negative prompt: {', '.join(negatives)}")
    return {
        "prompt": "\n".join(lines).strip() + "\n",
        "template": "labelled",
        "template_origin": template_conf["source"],
        "added": added,
        "unknown": unknown,
    }


def clean_prompt(prompt: str, keep_move: Optional[str] = None) -> Dict[str, Any]:
    """Remove what the published rules say hurts, deterministically.

    Every removal is reported with a reason and a source. This is not a rewrite —
    it changes no wording that the rules do not name, so you can diff it.
    """
    design = load_design()
    checks = {c["id"]: c for c in design["checks"]}
    changes: List[Dict[str, str]] = []
    text = prompt

    # 1. Feeling-words with no picture in them.
    slop = checks["slop_adjectives"]
    for pattern in slop["patterns"]:
        for match in list(re.finditer(pattern, text, re.I)):
            changes.append({"removed": match.group(0), "reason": slop["title"], "source": "sora"})
        text = re.sub(pattern, "", text, flags=re.I)

    # 2. Resolution, duration and aspect asked for in prose.
    params = checks["params_in_prose"]
    for pattern in params["patterns"]:
        for match in list(re.finditer(pattern, text, re.I)):
            changes.append({"removed": match.group(0), "reason": params["title"], "source": "sora"})
        text = re.sub(pattern, "", text, flags=re.I)

    # 3. Quotation marks around speech: they tend to get rendered as on-screen text.
    for match in list(re.finditer(r'["“]([^"”]{2,})["”]', text)):
        changes.append({"removed": f'"{match.group(1)}" → {match.group(1)}',
                        "reason": checks["quoted_dialogue"]["title"], "source": "veo_bp"})
    text = re.sub(r'["“]([^"”]{2,})["”]', r"\1", text)

    # 4. Extra camera moves: one shot has one move.
    moves = _find_moves(text)
    if len(moves) > 1:
        keep = keep_move if keep_move in moves else moves[0]
        for move in moves:
            if move == keep:
                continue
            pattern = _move_pattern(move)
            if pattern.search(text):
                changes.append({"removed": move, "reason": f"second camera move (the shot keeps '{keep}')",
                                "source": "wan_director"})
                text = pattern.sub("", text, count=1)
        text = re.sub(r"\b(?:and|while|then)\s+(?=[.,])", "", text, flags=re.I)

    # Tidy the surgery: doubled spaces, space before punctuation, empty clauses.
    text = re.sub(r"\s{2,}", " ", text)
    text = re.sub(r"\s+([.,;:!?])", r"\1", text)
    text = re.sub(r"\.\s*\.", ".", text)
    text = re.sub(r"^\s*[.,;:]\s*", "", text)
    text = text.strip()
    if text and text[-1] not in ".!?":
        text += "."

    return {"prompt": text, "changes": changes}


def check_selection_conflicts(prompt: str, selections: Dict[str, Any]) -> List[Dict[str, str]]:
    """Flag design elements that fight what the prompt already says.

    Merging a second camera move into a shot that already has one is the most
    common way this tool could make a prompt worse, so it says so.
    """
    design = load_design()
    conflicts: List[Dict[str, str]] = []

    def norm(value: str) -> str:
        # "pan left" and "slow pan left" are the same move; the qualifier is a
        # speed choice, not a different shot.
        return re.sub(r"^(?:slow|medium|fast)\s+", "", value.strip().lower())

    chosen = selections.get("camera_move")
    existing = _find_moves(prompt)
    existing_norm = {norm(m) for m in existing}
    if chosen and existing and norm(chosen) not in existing_norm:
        conflicts.append({
            "id": "camera_move_conflict",
            "detail": f"Your prompt already has a camera move ({', '.join(existing)}); the merge adds {chosen}. One shot has one move — clean the prose (which keeps yours) or pick the other move.",
            "source": design["sources"]["wan_director"],
        })
    elif chosen and len(existing) > 1 and norm(chosen) in existing_norm:
        conflicts.append({
            "id": "camera_move_redundant",
            "detail": f"The prompt already lists {len(existing)} moves including {chosen}; merging keeps adding information the shot cannot use.",
            "source": design["sources"]["wan_director"],
        })
    return conflicts


def build_improve(
    prompt: str,
    selections: Optional[Dict[str, Any]] = None,
    template: str = "inline",
    palette: Optional[str] = None,
    model_layers: Optional[List[Dict[str, Any]]] = None,
    image_count: int = 0,
    accepts_image: bool = False,
    clean: bool = False,
) -> Dict[str, Any]:
    """Lint first, then (optionally) clean, then merge.

    Order matters: the findings must describe what you wrote, and the score delta
    must describe what the merge and the cleanup actually changed.
    """
    selections = dict(selections or {})
    lint = lint_prompt(prompt, model_layers=model_layers, image_count=image_count,
                       accepts_image=accepts_image)

    base = prompt
    cleaned: Optional[Dict[str, Any]] = None
    if clean:
        cleaned = clean_prompt(prompt, keep_move=selections.get("camera_move"))
        base = cleaned["prompt"]

    conflicts = check_selection_conflicts(base, selections)
    merged = apply_design(base, selections, template, palette)
    after = lint_prompt(merged["prompt"], model_layers=None, image_count=0, accepts_image=False)
    return {
        "before": {"prompt": prompt, **lint},
        "cleaned": cleaned,
        "after": {"prompt": merged["prompt"], "score": after["score"], "counts": after["counts"],
                  "findings": after["findings"]},
        "design": {"template": merged["template"], "template_origin": merged["template_origin"],
                   "added": merged["added"], "unknown": merged["unknown"]},
        "conflicts": conflicts,
        "delta_score": after["score"] - lint["score"],
        "version": design_version(),
    }
