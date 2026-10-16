# Prompt Layer

**See what a model provider adds to your prompt before you pay for it — then choose.**

Every hosted video and chat endpoint rewrites, expands, screens or pads your
prompt before the model runs, and none of it is visible in the UI:

| what happens | where | default |
|---|---|---|
| Your prompt is **rewritten** if a policy check fails | Veo 3 / Veo 3 Fast (`auto_fix`) | `true` |
| Your prompt is **expanded by an LLM** | Hailuo-02 (`prompt_optimizer`), LTX-2 (`enable_prompt_expansion`) | `true` |
| A **canned negative prompt is attached** | Kling (`"blur, distort, and low quality"`), Pika (`"ugly, bad, terrible"`), LTX-2 (~200 words) | on |
| **Screen** before generation | `enable_safety_checker` (Seedance: *"unauthorized requests are always checked"*), `safety_tolerance` | `true` / `"4"` |
| **Resolution, aspect, duration, audio pinned** | every hosted endpoint | provider's choice |

Prompt Layer shows all of it, per endpoint, **before** you spend anything — and
gives you the exact parameter to flip if you want your wording back.

Nothing in this project is folklore or a guess: every layer carries the URL it was
read from and the verbatim evidence string, and the test suite refuses an entry
without a fetchable source. For a model we have not probed, the API returns
`unknown_model: true` and the generic layers only — it never invents a default.

![Prompt Layer — preflight for MiniMax Hailuo 02, showing the active
`expand.prompt_optimizer` layer, the warning with the parameter that turns it off,
and the resolved parameters tagged `provider default`](docs/screenshot.png)

## Three tools

**Preflight** — paste a prompt, pick a model, get:

- the exact upstream payload (messages for chat, forwarded prompt + params for video),
- every parameter **with its source** — `provider default` vs `your override`,
- each layer touching the request: what it does, whether it is active, and the knob
  that turns it off,
- `full_control_patch`: the JSON that switches every additive layer off in one go,
- the source + evidence string for each claim, expandable in the UI.

**Improve** — the other direction: what is *your* prompt costing you?

Two halves, both deterministic and both sourced:

1. **A lint** over the published rules — the things official guides name as
   mistakes, each with the guide's own words behind it. It fires only when the
   rule applies, so a well-written prompt comes back clean:

| check | what it catches | source |
|---|---|---|
| `slop_adjectives` | `beautiful`, `stunning`, `epic` — words that describe a feeling, not a picture | Sora 2 guide |
| `params_in_prose` | `4k`, `8k`, `make it longer` asked for in words instead of parameters | Sora 2 guide |
| `camera_conflict` | two camera moves in one shot, or opposing ones (`dolly in` + `pan left`) | Wan 2.2 system prompt |
| `quoted_dialogue` | quote marks around speech, which tend to be rendered as on-screen text | Veo prompt guide |
| `dialogue_too_long` | more speech than fits a 4–8 s clip | Veo prompt guide |
| `bare_cinematic` | `cinematic` with no technical reason given | Veo prompt guide |
| `no_lighting`, `no_motion`, `vague_action` | a shot with no light, no verb, or an action with no beats | Veo / Wan guides |
| `rewriting_layer_active` | you are already engineering a prompt *and* the endpoint rewrites it | the model's own layers |

2. **A merge** — the elements that cannot be written in prose (lens, light source,
   palette anchors, negatives, audio) added from a sourced vocabulary, with the
   output reporting exactly what was added and from where. It never invents a
   value: pick something outside the vocabulary and it is reported as
   `unknown`, not merged.

Both halves are visible in one screen, with the score delta:

![Prompt Layer — the Improve tab: a weak prompt scoring 27, the seven sourced
findings it trips, the cleanup log, and the merged prompt at 96](docs/screenshot-improve.png)

The reasoning behind the checks, in the order they matter, is written up in
[docs/improving-outputs.md](docs/improving-outputs.md) — the six levers, each with a
before/after and the guide it comes from.

**Compile** — run an **open-source** prompt expander over your idea, using the
verbatim system prompts shipped by the model owners:

| style | origin |
|---|---|
| `wan-engineer` | `Wan-Video/Wan2.1` → `wan/utils/prompt_extend.py` |
| `wan-director`, `wan-i2v` | `Wan-Video/Wan2.2` → `wan/utils/system_prompt.py` |
| `hunyuan-normal`, `hunyuan-master` | `Tencent-Hunyuan/HunyuanVideo` → `hyvideo/prompt_rewrite.py` |
| `kandinsky-beautify` | `ai-forever/Kandinsky-3` → README ("Prompt beautification") |

Compile is **dry run by default**: it returns the exact request that would be sent
and spends nothing.

### Why the improve step is not a black box

- **Cleanup is reported, not silent.** Removing `beautiful` or `make it longer`
  changes your words, so every removal is listed with the rule that required it.
  A prompt that breaks no rule comes back byte-identical.
- **The merge flags conflicts first.** Adding a `dolly in` to a prompt that already
  pans is the one way this tool could make a prompt worse, so it says so instead of
  quietly stacking a second move.
- **Your image never leaves the browser.** Reference-image checks need dimensions
  and MIME type, so the page measures the file locally and sends only those — no
  upload, no storage.

## Endpoints

| method | path | purpose |
|---|---|---|
| `GET` | `/api/health` | liveness + dataset size |
| `GET` | `/api/models` | models, expanders, dataset version, improve vocabulary |
| `GET` | `/api/models?slug=` | one model: layers, params, sources, image support |
| `POST` | `/api/preflight` | `{model, prompt, options?, system_message?, image_count?}` |
| `POST` | `/api/improve` | `{model, prompt, selections?, palette?, template?, clean?, image?}` |
| `POST` | `/api/compile` | `{prompt, style, model?, dry_run?}` |

```bash
curl -s -X POST localhost:8090/api/preflight -H 'Content-Type: application/json' \
  -d '{"model":"hailuo-02-standard","prompt":"a cat surfing a huge wave","options":{"prompt_optimizer":false}}' \
  | jq '.data.prompt.rewritten_upstream, .data.params.prompt_optimizer'
```

```bash
curl -s -X POST localhost:8090/api/improve -H 'Content-Type: application/json' \
  -d '{"model":"hailuo-02-standard","prompt":"A beautiful cinematic 8k scene of a man. Camera dollies in and pans left. Make it longer.","selections":{"camera_move":"dolly in","shot_size":"close-up shot"},"palette":"amber, cream, walnut brown","clean":true}' \
  | jq '.data.before.score, .data.after.score, .data.cleaned.changes[].removed'
```

## Run it

```bash
python dev.py          # http://127.0.0.1:8090   (emulates the Vercel layout)
python -m pytest -q    # 60 tests
```

No dependencies — the service, the dataset and the functions are all standard
library.

## Deploy (Vercel)

1. Import this repo at [vercel.com/new](https://vercel.com/new).
2. Framework preset **Other**. No build command, no output directory.
3. Deploy. `/` serves the UI; the four files in `api/` answer at `/api/health`,
   `/api/models`, `/api/preflight`, `/api/compile`.

Two things about how Vercel treats this layout, both checked against their docs:

- Each `.py` file in `/api` becomes its own function, served at its file path,
  with the top-level name `handler` (a `BaseHTTPRequestHandler` subclass) — which
  is what each file here exports.
- Do **not** add Flask, FastAPI or Django to `requirements.txt`. A detected Python
  framework preset takes precedence, and if one is detected the files under `/api`
  stop becoming separate functions. There are deliberately no dependencies here.

`vercel.json` sets memory and a timeout per function, and excludes the tests from
the bundle. It does not need `includeFiles`: Python functions bundle every project
file reachable at build time, so `promptlayer/` ships with them automatically.

Compile stays dry-run-only unless you set **all three** of
`ALLOW_REMOTE_COMPILE=1`, `COMPILE_GATEWAY_URL`, `COMPILE_API_KEY`. That opt-in is
deliberate: on a public deployment an API key in the environment means anyone can
spend it, so a key alone is never enough.

## Layout

```
index.html            the app (no build step, no framework)
api/health.py         \  Vercel Python functions — each exports `handler`
api/models.py          |
api/preflight.py       |
api/improve.py         |
api/compile.py        /
promptlayer/
  assembler.py        pure assembly logic (no I/O)
  layers.json         the dataset: every fact + its source and evidence
  improve.py          the lint, the deterministic cleanup, the design merge
  design.json         the improve vocabulary: 11 groups, each item sourced
  http.py             shared request/response helpers
dev.py                local emulation of the Vercel layout
tests/                60 tests: dataset, assembly, improvement, and the deployed entry points
```

Adding a model is one entry in `promptlayer/layers.json`; adding a lint rule or a
design element is one entry in `promptlayer/design.json`. The tests fail an entry
whose `source` is not a fetchable identifier, which is the point: the value of
this tool is that every claim can be checked.

## Scope and honesty

- Provider defaults drift. `layers.json` and `design.json` are versioned and
  surfaced in the footer; refreshing them means re-reading the specs and bumping
  `version`.
- Where a provider's behaviour is undocumented, the entry is omitted rather than
  filled in — an empty answer beats a confident wrong one.
- The lint is a checklist, not a critic. It has no model behind it and no opinion
  beyond the rules the guides actually state; it will not tell you your idea is
  bad, and it does not pretend a higher score means a better video.
- Model identities are those published by the providers. This tool reads public
  API schemas and open-source repositories; it does not reverse-engineer anything
  private.

Built as a [tokN](https://tokn.co.in) tool.

MIT — see [LICENSE](LICENSE).
