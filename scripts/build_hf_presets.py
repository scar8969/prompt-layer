"""Regenerate promptlayer/hf-presets.json from the vendored Hugging Face file.

    python scripts/build_hf_presets.py

The dataset is Hugging Face's own default widget inputs — the example prompts the
Hub shows in a model widget's examples dropdown when the model card does not
define its own. The upstream file is TypeScript:

    huggingface/huggingface.js -> packages/tasks/src/default-widget-inputs.ts
    export const MAPPING_DEFAULT_WIDGET = new Map<LanguageCode, PerLanguageMapping>(...)

It is vendored verbatim under promptlayer/vendor/ so the parse is reproducible and
diffable. This module is the parser for that file's subset of TypeScript: template
literals, arrays, objects, numbers, booleans, and `[...].join("\\n")`. It is
deliberately not a general TS parser — it handles the constructs the upstream file
actually uses, and tests/test_presets.py re-parses the vendored copy on every run
and fails if the committed JSON and the vendored source have drifted apart.
"""

from __future__ import annotations

import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
VENDOR = os.path.join(REPO, "promptlayer", "vendor", "hf-default-widget-inputs.ts")
OUT = os.path.join(REPO, "promptlayer", "hf-presets.json")

UPSTREAM = {
    "repo": "huggingface/huggingface.js",
    "path": "packages/tasks/src/default-widget-inputs.ts",
    "raw_url": ("https://raw.githubusercontent.com/huggingface/huggingface.js/main/"
                "packages/tasks/src/default-widget-inputs.ts"),
    "blob_url": ("https://github.com/huggingface/huggingface.js/blob/main/"
                 "packages/tasks/src/default-widget-inputs.ts"),
    "docs": "huggingface.co/docs/hub/models-widgets",
    "what": ("MAPPING_DEFAULT_WIDGET — the example inputs the Hugging Face Hub shows in a "
             "model widget's examples dropdown when the model card defines no `widget:` "
             "metadata of its own."),
}

# ---------------------------------------------------------------- the tokenizer

PUNCT = set("[]{}:,().")


class ParseError(ValueError):
    pass


def _tokens(src: str):
    i, n = 0, len(src)
    while i < n:
        ch = src[i]
        if ch in " \t\r\n":
            i += 1
            continue
        if ch == "/" and src[i:i + 2] == "//":
            while i < n and src[i] != "\n":
                i += 1
            continue
        if ch == "`":
            i += 1
            buf = []
            while i < n and src[i] != "`":
                if src[i] == "\\" and i + 1 < n:
                    nxt = src[i + 1]
                    buf.append({"n": "\n", "t": "\t", "\\": "\\", "`": "`", "$": "$"}.get(nxt, "\\" + nxt))
                    i += 2
                    continue
                buf.append(src[i])
                i += 1
            if i >= n:
                raise ParseError("unterminated template literal")
            i += 1
            yield ("str", "".join(buf))
            continue
        if ch in "\"'":
            quote, i = ch, i + 1
            buf = []
            while i < n and src[i] != quote:
                if src[i] == "\\" and i + 1 < n:
                    nxt = src[i + 1]
                    buf.append({"n": "\n", "t": "\t"}.get(nxt, nxt))
                    i += 2
                    continue
                buf.append(src[i])
                i += 1
            i += 1
            yield ("str", "".join(buf))
            continue
        if ch.isdigit() or (ch == "-" and i + 1 < n and src[i + 1].isdigit()):
            j = i + 1
            while j < n and (src[j].isdigit() or src[j] in ".eE+-"):
                j += 1
            yield ("num", src[i:j])
            i = j
            continue
        if ch.isalpha() or ch in "_$":
            j = i
            while j < n and (src[j].isalnum() or src[j] in "_$"):
                j += 1
            yield ("ident", src[i:j])
            i = j
            continue
        if ch in PUNCT:
            yield (ch, ch)
            i += 1
            continue
        raise ParseError(f"unexpected character {ch!r} at offset {i}")


class _Parser:
    def __init__(self, tokens):
        self.tokens = list(tokens)
        self.pos = 0

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else (None, None)

    def next(self):
        tok = self.peek()
        self.pos += 1
        return tok

    def expect(self, kind):
        got = self.next()
        if got[0] != kind:
            raise ParseError(f"expected {kind!r}, got {got!r}")
        return got[1]

    def value(self):
        """A primary, plus the one method call the upstream file uses: `.join(sep)`."""
        val = self.primary()
        if self.peek() == (".", "."):
            self.next()
            method = self.expect("ident")
            if method != "join":
                raise ParseError(f"unsupported method .{method}()")
            self.expect("(")
            sep = self.primary()
            self.expect(")")
            if isinstance(val, str):
                raise ParseError("join() applied to a string")
            if not isinstance(sep, str):
                raise ParseError("join() separator must be a string")
            return sep.join(str(part) for part in val)
        return val

    def primary(self):
        kind, text = self.peek()
        if kind == "str":
            self.next()
            return text
        if kind == "num":
            self.next()
            return float(text) if any(c in text for c in ".eE") else int(text)
        if kind == "ident":
            self.next()
            if text == "true":
                return True
            if text == "false":
                return False
            if text == "null":
                return None
            raise ParseError(f"unexpected identifier {text!r}")
        if kind == "[":
            return self.array()
        if kind == "{":
            return self.object()
        raise ParseError(f"unexpected token {self.peek()!r}")

    def array(self):
        self.expect("[")
        out = []
        while self.peek()[0] != "]":
            item = self.value()
            out.append(item)
            if self.peek()[0] == ",":
                self.next()
            elif self.peek()[0] != "]":
                raise ParseError(f"expected , or ] in array, got {self.peek()!r}")
        self.expect("]")
        return out

    def object(self):
        self.expect("{")
        out = {}
        while self.peek()[0] != "}":
            kind, key = self.next()
            if kind not in ("ident", "str"):
                raise ParseError(f"expected a key, got {(kind, key)!r}")
            self.expect(":")
            out[key] = self.value()
            if self.peek()[0] == ",":
                self.next()
            elif self.peek()[0] != "}":
                raise ParseError(f"expected , or }} in object, got {self.peek()!r}")
        self.expect("}")
        return out


# ---------------------------------------------------------------- the file

_BLOCK = re.compile(r"^const\s+MAPPING_([A-Z]{2})\s*(?::[^=]+)?=\s*new\s+Map(?:<[^>]*>)?\(",
                    re.MULTILINE)


def _blocks(src: str):
    """Yield (language, body) for each `const MAPPING_XX ... = new Map([ ... ]);`."""
    for match in _BLOCK.finditer(src):
        lang = match.group(1).lower()
        start = src.index("[", match.end() - 1)
        depth, i = 0, start
        in_str = None
        while i < len(src):
            ch = src[i]
            if in_str:
                if ch == "\\":
                    i += 2
                    continue
                if ch == in_str:
                    in_str = None
            elif ch in "`\"'":
                in_str = ch
            elif ch in "[{":
                depth += 1
            elif ch in "]}":
                depth -= 1
                if depth == 0:
                    break
            i += 1
        yield lang, src[start:i + 1]


def parse(src: str) -> dict:
    """The whole MAPPING_DEFAULT_WIDGET, as {language: {task: [presets]}}."""
    languages = {}
    for lang, body in _blocks(src):
        pairs = _Parser(_tokens(body)).array()
        tasks = {}
        for pair in pairs:
            if not (isinstance(pair, list) and len(pair) == 2 and isinstance(pair[0], str)):
                raise ParseError(f"[{lang}] expected a [task, [examples]] pair, got {pair!r}")
            tasks[pair[0]] = pair[1]
        languages[lang] = tasks
    if not languages:
        raise ParseError("no MAPPING_* blocks found — did the upstream shape change?")
    return languages


def build(vendor_path: str = VENDOR) -> dict:
    with open(vendor_path, encoding="utf-8") as fh:
        src = fh.read()
    languages = parse(src)
    counts = {lang: sum(len(v) for v in tasks.values()) for lang, tasks in languages.items()}
    return {
        "version": "2026-10-16",
        "source": UPSTREAM,
        "languages": sorted(languages),
        "counts": {"total": sum(counts.values()), "by_language": counts},
        "presets": languages,
    }


def main() -> int:
    data = build()
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(f"wrote {OUT}")
    print(f"  {len(data['languages'])} languages, {data['counts']['total']} presets")
    for lang, count in sorted(data["counts"]["by_language"].items()):
        tasks = ", ".join(sorted(data["presets"][lang]))
        print(f"  {lang}: {count:>3}  {tasks}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
