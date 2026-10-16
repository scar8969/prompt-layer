"""The video planner: talk a concept through, then turn it into scenes.

Three deterministic operations, no model calls:

  read_facets   read what the concept already says — subject, action, setting,
                camera, light, sound, length — by pattern, and report only what it
                actually found. It never fills a gap with a guess.
  plan          the chat turn: what was read, what is missing, and the questions
                worth asking. Every question carries the reason the model needs it
                and the source that says so.
  build_scenes  split the concept into beats and turn each beat into one scene:
                an assembled prompt, the endpoint's own parameters, the reference
                image it uses, and a lint score.

The split rule is published — Veo's best-practice page says chaining several
events into one prompt "often leads to muddled or incomplete videos" and shows
three separate clips instead. The *cue words* that find the boundaries are this
tool's heuristic, and `storyboard.json` marks them `sourced: false` so nothing
here pretends otherwise.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

from .improve import (MOTION_HINTS, _find_moves, apply_design, clean_prompt, lint_prompt,
                      load_design, vocab)

HERE = os.path.dirname(os.path.abspath(__file__))
_SB: Optional[Dict[str, Any]] = None

MAX_BEATS = 24
MAX_QUESTIONS = 6

# Reading patterns. These are how the tool reads prose, not claims about any model.
DURATION_RE = re.compile(r"\b(\d+(?:\.\d+)?)\s*(?:s\b|sec\b|secs\b|second|seconds)\b", re.I)
ASPECT_RE = re.compile(r"\b(\d{1,2})\s*:\s*(\d{1,2})\b")
ASPECT_WORDS = {
    "vertical": "9:16", "portrait": "9:16", "landscape": "16:9", "widescreen": "16:9",
    "square": "1:1", "horizontal": "16:9",
}
PROPER_RE = re.compile(r"(?<![.!?]\s)(?<!^)\b([A-Z][a-z]{2,})\b")
DETERMINER_RE = re.compile(r"(?=(?:\b(?:a|an|the|his|her|their|its|my)\s+"
                           r"([a-z][a-z\-]*(?:\s+[a-z][a-z\-]*){0,2})))")
HANDLE_RE = re.compile(r"@([a-z][\w\-]{1,30})")
# A subject read out of prose stops at the first relation word: "a video about a
# cat" is a request, not a subject called "video about a".
SUBJECT_STOP = re.compile(r"\s+(?:about|with|of|in|on|at|that|which|who|whom|and|then|"
                          r"from|for|to|by|into|over|under|near|behind)\b", re.I)
# Words that describe the artefact rather than the thing in it. None of these is a
# subject, so none is ever reported as one.
NOT_A_SUBJECT = {
    "video", "clip", "shot", "scene", "film", "movie", "animation", "thing",
    "something", "anything", "story", "idea", "concept", "person", "someone",
    "footage", "sequence", "camera", "frame", "image", "picture", "reel",
}
QUOTED_RE = re.compile(r"[\u201c\"]([^\u201d\"]{2,})[\u201d\"]")
SETTING_RE = re.compile(
    r"\b(?:in|on|at|inside|outside|across|through|under|over|near|beside|behind|above|"
    r"below|along|around)\s+(?:the|a|an|his|her|their|my)?\s*"
    r"([a-z][\w\-]*(?:\s+[a-z][\w\-]*){0,2})", re.I)
# A setting read out of prose trails into the next clause; trim the connective.
SETTING_TAIL = re.compile(r"\s+(?:at|in|on|and|then|with|to|of|for|by|from|as|when|while|but)$", re.I)
SETTING_STOP = re.compile(r"\s+(?:and|then|but|while|as)\s+", re.I)
LEADING_CONNECTIVE = re.compile(r"^\s*(?:and|then|so|but|also|now)\b[\s,]*", re.I)


def load_storyboard() -> Dict[str, Any]:
    global _SB
    if _SB is None:
        with open(os.path.join(HERE, "storyboard.json"), encoding="utf-8") as fh:
            _SB = json.load(fh)
    return _SB


def reload_storyboard() -> Dict[str, Any]:
    global _SB
    _SB = None
    return load_storyboard()


def storyboard_version() -> str:
    return load_storyboard()["version"]


def planner_rules() -> Dict[str, Any]:
    """The published rules the planner applies, each with its verbatim evidence."""
    sb = load_storyboard()
    out = {}
    for key, rule in sb["rules"].items():
        out[key] = {**rule, "source": sb["sources"].get(rule["source"], rule["source"])}
    return out


def facet_catalog() -> List[Dict[str, Any]]:
    """The facets a video prompt needs, with the sourced options for each."""
    sb = load_storyboard()
    known = vocab()
    out = []
    for facet in sb["facets"]:
        entry = {
            "id": facet["id"],
            "label": facet["label"],
            "blocking": facet["blocking"],
            "free": facet["free"],
            "why": facet["why"],
            "source": sb["sources"].get(facet["source"], facet["source"]),
            "evidence": facet["evidence"],
            "options": [],
            "options_from": facet.get("options_from"),
            "options_from_params": facet.get("options_from_params"),
        }
        key = facet.get("options_from")
        if key and key in known:
            entry["options"] = list(known[key]["items"])
        out.append(entry)
    return out


def role_catalog() -> List[Dict[str, Any]]:
    sb = load_storyboard()
    return [{**role, "source": sb["sources"].get(role["source"], role["source"])} for role in sb["roles"]]


def _contains(text: str, item: str) -> bool:
    return re.search(r"(?<![\w-])" + re.escape(item) + r"(?![\w-])", text, re.I) is not None


def read_facets(text: str) -> Dict[str, Any]:
    """Read the facets out of the concept. Only what is there, nothing filled in.

    The reading is a pattern match, so it is labelled as one: every value returned
    here is a string taken from the user's own words, and the UI lets them edit it.
    """
    known = vocab()
    found: Dict[str, Any] = {}

    # Camera moves use the inflection-tolerant matcher, so "dollies in" is found.
    moves = _find_moves(text)
    if moves:
        found["camera_move"] = moves

    for key in ("shot_size", "lens", "light_source", "tone", "style", "audio", "time_of_day"):
        items = [item for item in known.get(key, {}).get("items", []) if _contains(text, item)]
        if items:
            found[key] = items

    if MOTION_HINTS.search(text):
        verbs = sorted({m.group(0).lower() for m in MOTION_HINTS.finditer(text)})
        found["action"] = verbs

    settings = []
    for match in SETTING_RE.finditer(text):
        value = SETTING_STOP.split(match.group(1))[0].strip()
        while SETTING_TAIL.search(value):
            value = SETTING_TAIL.sub("", value)
        if len(value) > 2 and value.lower() not in {
                "the", "a", "an", "his", "her", "their", "it", "this", "that", "time"}:
            settings.append(value)
    settings = list(dict.fromkeys(settings))
    if settings:
        found["setting"] = settings[:4]

    subjects = HANDLE_RE.findall(text)
    subjects += [m.group(1) for m in PROPER_RE.finditer(text)]
    if not subjects:
        for match in DETERMINER_RE.finditer(text):
            phrase = SUBJECT_STOP.split(match.group(1))[0].strip()
            # "a boy running" names the boy: a trailing participle is not the noun.
            phrase = re.sub(r"\s+\w+ing$", "", phrase).strip()
            words = phrase.split()
            if words and words[0].lower() in NOT_A_SUBJECT:
                continue
            if phrase and phrase.lower() not in NOT_A_SUBJECT:
                subjects.append(phrase)
            if len(subjects) >= 2:
                break
    subjects = [s for s in dict.fromkeys(subjects)
                if s.lower() not in NOT_A_SUBJECT
                and s.lower() not in {"the", "this", "that", "it", "me", "us", "them"}]
    if subjects:
        found["subject"] = subjects[:4]

    durations = DURATION_RE.findall(text)
    if durations:
        found["duration"] = durations

    aspect = ASPECT_RE.search(text)
    if aspect:
        found["aspect"] = [f"{aspect.group(1)}:{aspect.group(2)}"]
    else:
        words = [canon for word, canon in ASPECT_WORDS.items() if _contains(text, word)]
        if words:
            found["aspect"] = words[:1]

    quoted = QUOTED_RE.findall(text)
    if quoted:
        found["dialogue"] = quoted

    return {
        "found": found,
        "ids": sorted(found),
        "note": ("Read from your words by pattern — no model involved. "
                 "Edit anything it got wrong; it will not guess."),
    }


def _param_options(param: Dict[str, Any]) -> List[str]:
    """The values an endpoint documents for one parameter, as written."""
    if not isinstance(param, dict):
        return []
    options = param.get("options")
    if isinstance(options, list) and options:
        return [str(o) for o in options]
    default = param.get("default")
    if isinstance(default, str) and default.strip():
        return [part.strip() for part in re.split(r"\s+or\s+", default) if part.strip()]
    if isinstance(default, list):
        return [str(o) for o in default]
    return []


def _first_number(value: Any) -> Optional[float]:
    """The first number in a chat answer, so "6 seconds each" is 6."""
    match = re.search(r"\d+(?:\.\d+)?", str(value or ""))
    return float(match.group()) if match else None


def _questions(missing: List[str], model_detail: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One question per gap, blocking first, each with the reason and its source."""
    by_id = {f["id"]: f for f in facet_catalog()}
    params = (model_detail or {}).get("params") or {}
    out: List[Dict[str, Any]] = []
    for facet_id in missing:
        facet = by_id.get(facet_id)
        if facet is None:
            continue
        options: List[str] = []
        origin = None
        if facet.get("options_from_params"):
            key = facet["options_from_params"]
            options = _param_options(params.get(key) or {})
            origin = f"{model_detail.get('display') or 'this endpoint'} · parameter '{key}'" if options else None
        elif facet.get("options_from"):
            options = facet["options"]
            origin = facet["source"]
        out.append({
            "id": f"ask_{facet_id}",
            "facet": facet_id,
            "label": facet["label"],
            "question": _question_text(facet, options),
            "why": facet["why"],
            "source": origin or facet["source"],
            "evidence": facet["evidence"],
            "options": options,
            "free": facet["free"],
            "blocking": facet["blocking"],
        })
    out.sort(key=lambda q: (not q["blocking"], q["facet"]))
    return out[:MAX_QUESTIONS]


QUESTION_TEXT = {
    "subject": "Who or what is this about?",
    "action": "What moves during the clip?",
    "setting": "Where does it happen?",
    "camera_move": "What should the camera do?",
    "shot_size": "How close is the shot?",
    "lens": "Which lens?",
    "light_source": "What is the light?",
    "time_of_day": "What time of day?",
    "tone": "Which colours?",
    "style": "What look?",
    "audio": "What do we hear?",
    "dialogue": "Any spoken line?",
    "duration": "How long is each clip?",
    "aspect": "What shape is the frame?",
}


def _question_text(facet: Dict[str, Any], options: List[str]) -> str:
    """The question, on its own.

    The options are returned as `options` and rendered as buttons, so repeating
    them inside the sentence would only make the question harder to read.
    """
    return QUESTION_TEXT.get(facet["id"], f"{facet['label']}?")


def split_beats(text: str) -> List[Dict[str, Any]]:
    """Split the concept into beats: one beat, one shot, one generation.

    Sentence boundaries first, then the cue words in `storyboard.json`. The cue
    list is a heuristic and is labelled as one wherever it is shown. The cue word
    itself is a boundary marker, not content, so it is cut out of the beat rather
    than left at the front of the prompt.
    """
    sb = load_storyboard()
    cues = [re.compile(p, re.I) for p in sb["cues"]["patterns"]]
    beats: List[Dict[str, Any]] = []
    for sentence_index, sentence in enumerate(re.split(r"(?<=[.!?])\s+|\n+", text.strip())):
        sentence = sentence.strip()
        if not sentence:
            continue
        cuts = []
        for cue in cues:
            for match in cue.finditer(sentence):
                cuts.append((match.start(), match.end()))
        cuts.sort()
        # Overlapping cues ("and then" also matches "then"): keep the longest.
        selected: List[List[int]] = []
        for start, end in cuts:
            if selected and start < selected[-1][1]:
                if end > selected[-1][1]:
                    selected[-1][1] = end
                continue
            selected.append([start, end])
        cursor = 0
        for start, end in selected + [[len(sentence), len(sentence)]]:
            chunk = sentence[cursor:start].strip(" ,;:—-")
            if chunk:
                beats.append({"text": chunk, "cue": bool(cursor), "sentence": sentence_index})
            cursor = end
    # Cutting mid-sentence on a cue can leave a stub ("and then he sits" -> "he
    # sits"). A stub inside a sentence is merged into the beat it was cut from; a
    # short sentence of its own is a real beat and stays one.
    merged: List[Dict[str, Any]] = []
    for beat in beats:
        text = LEADING_CONNECTIVE.sub("", beat["text"]).strip(" ,;:—-")
        if not text:
            continue
        previous = merged[-1] if merged else None
        if (previous and len(text.split()) < 3
                and previous["sentence"] == beat["sentence"]):
            previous["text"] = f"{previous['text']} {text}".strip()
        else:
            merged.append({"text": text, "cue": beat["cue"], "sentence": beat["sentence"]})
    return merged[:MAX_BEATS]


def plan(concept: str,
         answers: Optional[Dict[str, Any]] = None,
         model_detail: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """One chat turn: read the concept, report the gaps, ask the next questions."""
    answers = {k: v for k, v in (answers or {}).items() if v not in (None, "", [])}
    reading = read_facets(concept)
    found = dict(reading["found"])
    for key, value in answers.items():
        found[key] = value if isinstance(value, list) else [value]

    facets = facet_catalog()
    missing = [f["id"] for f in facets if f["id"] not in found]
    blocking = [f["id"] for f in facets if f["blocking"] and f["id"] not in found]
    questions = _questions(missing, model_detail)
    beats = split_beats(concept)

    warnings: List[Dict[str, Any]] = []
    if len(beats) > 1:
        rule = planner_rules()["one_beat_per_clip"]
        warnings.append({
            "id": "multiple_beats",
            "title": f"This reads as {len(beats)} shots, not one",
            "detail": ("A clip is one shot. Each beat below becomes its own scene and its "
                       "own generation — that is the documented fix, not a limitation here."),
            "source": rule["source"],
            "evidence": rule["evidence"],
        })
    if "camera_move" in found and len(found["camera_move"]) > 1:
        rule = planner_rules()["shot_specific"]
        warnings.append({
            "id": "many_moves",
            "title": f"{len(found['camera_move'])} camera moves in one beat",
            "detail": "One shot has one move: " + ", ".join(found["camera_move"]) + ".",
            "source": rule["source"],
            "evidence": rule["evidence"],
        })

    reply = _reply(found, blocking, len(beats), questions)
    return {
        "reading": reading,
        "found": found,
        "missing": missing,
        "blocking_missing": blocking,
        "questions": questions,
        "beats": beats,
        "warnings": warnings,
        "ready": not blocking,
        "reply": reply,
        "version": storyboard_version(),
    }


def _reply(found: Dict[str, Any], blocking: List[str], beat_count: int,
           questions: List[Dict[str, Any]]) -> str:
    bits = []
    for key in ("subject", "action", "setting", "camera_move", "shot_size", "light_source",
                "time_of_day", "tone", "style", "audio", "dialogue", "duration", "aspect"):
        if key in found:
            values = found[key] if isinstance(found[key], list) else [found[key]]
            bits.append(f"{key}: {', '.join(str(v) for v in values[:4])}")
    head = "Read from your words — " + ("; ".join(bits) if bits else "nothing yet") + "."
    if beat_count > 1:
        head += f" That is {beat_count} beats, so {beat_count} clips."
    if blocking:
        head += f" Still missing: {', '.join(blocking)}."
    elif questions:
        head += " Nothing blocking left — the rest is optional polish."
    else:
        head += " Nothing missing. Ready to turn into scenes."
    return head


def _snap_seconds(target: float, options: List[str], default: Optional[str]) -> Dict[str, Any]:
    """Snap a wanted clip length to the lengths the endpoint documents."""
    numeric = []
    for option in options:
        try:
            numeric.append((float(option), option))
        except ValueError:
            continue
    if not numeric:
        return {"seconds": None, "asked": target, "options": [], "note": "no documented lengths"}
    exact = [o for value, o in numeric if abs(value - target) < 0.01]
    if exact:
        return {"seconds": exact[0], "asked": target, "options": [o for _, o in numeric], "note": "exact"}
    below = [(value, o) for value, o in numeric if value <= target]
    chosen = max(below)[1] if below else min(numeric)[1]
    return {
        "seconds": chosen,
        "asked": target,
        "options": [o for _, o in numeric],
        "note": f"asked {target:g}s; this endpoint documents {'/'.join(o for _, o in numeric)}s — using {chosen}s",
    }


def _param_value(entry: Any, value: Any) -> bool:
    """Whether an endpoint documents this value for this parameter.

    A parameter is written only when the dataset says the endpoint accepts the
    value. An undocumented value is reported instead of sent: guessing here means
    a request that fails at the provider, which is worse than a note.
    """
    if not isinstance(entry, dict):
        return True
    options = _param_options(entry)
    if options:
        return str(value) in options
    return True


def scene_params(model_detail: Optional[Dict[str, Any]], seconds: Optional[str],
                 aspect: Optional[str], negative: Optional[str]) -> Dict[str, Any]:
    """The endpoint's own parameters for one scene, named as its schema names them."""
    params: Dict[str, Any] = {}
    documented = (model_detail or {}).get("params") or {}
    if seconds:
        for key in ("seconds", "duration"):
            if key in documented and _param_value(documented[key], seconds):
                params[key] = seconds
                break
    if aspect:
        for key in ("size", "aspect_ratio", "resolution"):
            if key in documented and _param_value(documented[key], aspect):
                params[key] = aspect
                break
    if negative and "negative_prompt" in documented:
        params["negative_prompt"] = negative
    return params


def rejected_params(model_detail: Optional[Dict[str, Any]], seconds: Optional[str],
                    aspect: Optional[str]) -> List[Dict[str, Any]]:
    """Values that were asked for but are not documented for this endpoint."""
    documented = (model_detail or {}).get("params") or {}
    out: List[Dict[str, Any]] = []
    for value, keys in ((seconds, ("seconds", "duration")), (aspect, ("size", "aspect_ratio",
                                                                     "resolution"))):
        if not value:
            continue
        present = [k for k in keys if k in documented]
        if not present:
            continue
        if any(_param_value(documented[k], value) for k in present):
            continue
        allowed = _param_options(documented[present[0]])
        out.append({
            "parameter": present[0],
            "asked": value,
            "documented": allowed,
            "detail": (f"{value} is not a documented value for '{present[0]}' on this endpoint"
                       + (f"; it documents {' or '.join(allowed)}" if allowed else "")
                       + ". Left out of the request rather than sent as a guess."),
        })
    return out


def build_scenes(concept: str,
                 model: Optional[str] = None,
                 model_detail: Optional[Dict[str, Any]] = None,
                 design: Optional[Dict[str, Any]] = None,
                 beats: Optional[List[Dict[str, Any]]] = None,
                 total_seconds: Optional[float] = None,
                 scene_seconds: Optional[float] = None,
                 aspect: Optional[str] = None,
                 references: Optional[List[Dict[str, Any]]] = None,
                 answers: Optional[Dict[str, Any]] = None,
                 template: str = "inline") -> Dict[str, Any]:
    """Turn the concept into scenes: one beat, one prompt, one generation.

    `answers` is what the chat turn collected. An answer that names a documented
    design group is merged as a design element; an answer for a parameter the
    endpoint documents (duration, aspect) is written as that parameter; anything
    else is reported rather than dropped — a chat answer that never reaches the
    prompt is worse than a note saying why it could not.
    """
    design = dict(design or {})
    selections = dict(design.get("selections") or {})
    palette = design.get("palette")
    negatives = list(design.get("negative") or [])
    template = design.get("template") or template

    answers = {k: v for k, v in (answers or {}).items() if v not in (None, "", [])}
    known = vocab()
    placed: Dict[str, Any] = {}
    unplaced: List[str] = []
    answered_seconds: Optional[float] = None
    for key, value in answers.items():
        if key in known and key != "negative":
            selections.setdefault(key, value)
            placed[key] = value
        elif key == "dialogue":
            selections.setdefault("dialogue", value)
            placed[key] = value
        elif key == "duration":
            number = _first_number(value)
            if number:
                answered_seconds = number
                placed[key] = value
            else:
                unplaced.append(key)
        elif key == "aspect":
            aspect = aspect or str(value)
            placed[key] = value
        else:
            unplaced.append(key)

    beats = beats if beats is not None else split_beats(concept)
    if not beats:
        return {"scenes": [], "totals": {"clips": 0, "seconds": 0}, "notes": [], "continuity": {}}

    documented = (model_detail or {}).get("params") or {}
    seconds_param = documented.get("seconds") or documented.get("duration") or {}
    options = _param_options(seconds_param) if isinstance(seconds_param, dict) else []
    default_seconds = seconds_param.get("default") if isinstance(seconds_param, dict) else None

    stated = read_facets(concept)["found"].get("duration")
    if scene_seconds is None:
        if answered_seconds:
            target = answered_seconds
        elif total_seconds:
            target = total_seconds / len(beats)
        elif stated:
            target = float(stated[0])
        elif str(default_seconds or "").replace(".", "", 1).isdigit():
            target = float(default_seconds)
        else:
            target = 4.0
    else:
        target = float(scene_seconds)
    snapped = _snap_seconds(target, options, str(default_seconds) if default_seconds else None)

    refs = list(references or [])
    continuity = continuity_sheet(concept, refs)
    if aspect is None:
        found_aspect = read_facets(concept)["found"].get("aspect")
        aspect = found_aspect[0] if found_aspect else None
    clean_beats = bool(design.get("clean", True))
    scenes: List[Dict[str, Any]] = []
    notes: List[Dict[str, Any]] = []

    for index, beat in enumerate(beats, start=1):
        text = beat["text"]
        # Parameters asked for in prose are not prose: strip them the same way the
        # Improve tab does, and report every removal so the scene can be diffed.
        cleaned = clean_prompt(text) if clean_beats else None
        base = cleaned["prompt"] if cleaned else text
        present = read_facets(base)["found"]

        # Only merge what the scene's own words do not already say, so the design
        # set can never double a camera move the beat already carries.
        inherited = {k: v for k, v in selections.items() if k not in present}
        merged = apply_design(base, inherited, template, palette)
        negative = ", ".join(dict.fromkeys(negatives + (
            [str(documented.get("negative_prompt", {}).get("default"))]
            if isinstance(documented.get("negative_prompt"), dict)
            and documented["negative_prompt"].get("default") else [])))

        lint = lint_prompt(merged["prompt"], model_layers=(model_detail or {}).get("layers"),
                           image_count=0, accepts_image=False)
        scene_refs = [r for r in refs if r.get("scene") in (None, index)]
        scenes.append({
            "id": f"scene-{index}",
            "index": index,
            "title": " ".join(text.split()[:6]),
            "beat": text,
            "cue": beat.get("cue", False),
            "seconds": snapped["seconds"],
            "prompt": merged["prompt"],
            "negative": negative or None,
            "cleaned": cleaned,
            "added": merged["added"],
            "unknown": merged["unknown"],
            "params": scene_params(model_detail, snapped["seconds"],
                                   aspect or ((model_detail or {}).get("params", {})
                                              .get("aspect_ratio", {}) or {}).get("default"),
                                   negative),
            "score": lint["score"],
            "findings": lint["findings"],
            "counts": lint["counts"],
            "references": [{"role": r.get("role"), "index": r.get("index")} for r in scene_refs],
            "continuity": continuity["lines"],
        })

    rule = planner_rules()["one_beat_per_clip"]
    notes.append({
        "id": "one_beat_per_clip",
        "title": f"{len(scenes)} scene(s) → {len(scenes)} generation(s)",
        "detail": ("Each scene is one shot and one request. Keep them separate rather than "
                   "chaining the beats back into one prompt."),
        "source": rule["source"],
        "evidence": rule["evidence"],
    })
    if snapped["note"] not in ("exact", "no documented lengths"):
        notes.append({
            "id": "clip_length",
            "title": "Clip length snapped to the documented values",
            "detail": snapped["note"],
            "source": planner_rules()["extension"]["source"],
            "evidence": planner_rules()["extension"]["evidence"],
        })
    if not model:
        notes.append({
            "id": "no_model",
            "title": "No endpoint chosen yet",
            "detail": "Pick a video model and the parameters below become that endpoint's own.",
            "source": None,
            "evidence": None,
        })
    for rejected in rejected_params(model_detail, snapped["seconds"], aspect):
        notes.append({
            "id": "undocumented_param",
            "title": f"'{rejected['parameter']}' left out of the request",
            "detail": rejected["detail"],
            "source": None,
            "evidence": None,
        })
    if unplaced:
        facets = {f["id"]: f for f in facet_catalog()}
        first = facets.get(unplaced[0], {})
        notes.append({
            "id": "answer_not_placed",
            "title": f"{len(unplaced)} chat answer(s) did not become a prompt or a parameter",
            "detail": ("Nothing here is a documented design element or parameter, so it was not written "
                       "into the prompt: " + ", ".join(unplaced) + ". Put it in the concept prose instead "
                       "— then it lands in the beat it belongs to."),
            "source": first.get("source"),
            "evidence": first.get("evidence"),
        })

    total = sum(float(s["seconds"] or 0) for s in scenes)
    return {
        "concept": concept,
        "model": model,
        "scenes": scenes,
        "continuity": continuity,
        "answers_placed": placed,
        "answers_unplaced": unplaced,
        "totals": {
            "clips": len(scenes),
            "seconds": total,
            "per_clip": snapped["seconds"],
            "per_clip_asked": snapped["asked"],
            "documented_lengths": snapped["options"],
        },
        "aspect": aspect,
        "param_notes": rejected_params(model_detail, snapped["seconds"], aspect),
        "notes": notes,
        "version": storyboard_version(),
    }


def continuity_sheet(concept: str, references: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """What has to stay identical from scene to scene, and why."""
    rules = planner_rules()
    reading = read_facets(concept)["found"]
    subjects = reading.get("subject") or []
    handles = []
    for subject in subjects[:2]:
        handle = "@" + re.sub(r"[^a-z0-9]+", "-", subject.lower()).strip("-")
        handles.append({"handle": handle, "wording": subject})
    lines = [
        f"Refer to {h['wording']} as “{h['handle']}” in every scene — same words each time."
        for h in handles
    ]
    if references:
        framed = [r for r in references if r.get("role") in ("first_frame", "last_frame")]
        if framed:
            lines.append("Attach the reference frame to each scene that needs it; the frame "
                         "locks composition and look, so describe only what moves.")
    if reading.get("tone"):
        lines.append("Keep the palette identical: " + ", ".join(reading["tone"]))
    return {
        "subjects": handles,
        "lines": lines,
        "source": rules["character_reference"]["source"],
        "evidence": rules["character_reference"]["evidence"],
    }
