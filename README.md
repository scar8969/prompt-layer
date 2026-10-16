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

## Four tools

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

## Presets — Hugging Face's own

Prompting is easier with an example in front of you, so the Improve tab carries the
presets **Hugging Face itself uses**, and none of them are written here:

| collection | what it is | size | source |
|---|---|---|---|
| **Hub defaults** | the example inputs the Hub shows in a model widget's examples dropdown when the card defines none | **201 presets, 14 languages** | `huggingface/huggingface.js` → `packages/tasks/src/default-widget-inputs.ts` |
| **Documented widget examples** | the example inputs HF documents for every widget type | **28 types, 50 examples** | `huggingface/hub-docs` → `docs/hub/models-widgets-examples.md` |
| **A model's own card** | the `widget:` metadata of one model — what *its* page shows | per model, read live | `huggingface.co/api/models/<id>` |

Both files are vendored under `promptlayer/vendor/` and parsed by
`scripts/build_hf_presets.py` and `scripts/build_hf_widget_examples.py`. The
committed JSON is not hand-written: a test re-parses the vendored source on every
run and fails if the two have drifted apart.

![Prompt Layer — the Hugging Face presets panel: collection, language and task
filters, the source line, and the preset list](docs/screenshot-presets.png)

Two findings worth knowing, both encoded as tests:

- **HF's runtime defaults are text-only.** All 201 ship for NLP tasks; there is no
  `text-to-image` or `image-to-video` default anywhere in that file. That is why the
  second collection exists — the vision and video examples live in the widget docs.
- **A card can carry its own.** `openai/clip-vit-base-patch32` defines one widget
  example; `Wan-AI/Wan2.2-T2V-A14B` defines none, and the tool says exactly that
  rather than showing something it made up.

Only the per-model read touches the network, only when you type a model id, and it
is one public document — nothing about your prompt or your machine is sent. Offline,
it reports the failure and the local collections still work.

**Video** — talk a concept through, then get one prompt per shot.

A whole video is several shots, and a model given one prompt for all of them produces
a muddle — Veo's own best-practice page says so and shows three separate clips as the
fix. So this tab is a conversation that ends in a storyboard:

1. **Explain it** — write the scene, or the whole video, the way you would say it
   out loud.
2. **It reads what you wrote.** Subject, action, setting, camera, light, sound and
   length are matched by pattern and reported back. It **never fills a gap with a
   guess**: what is not in your words is listed as missing.
3. **It asks only what is blocking**, each question carrying the reason the model
   needs it and the guide that says so. Answers become design elements, parameters,
   or — if they are none of those — a note saying where they *should* go instead of
   vanishing.
4. **One beat, one clip.** The concept is split into beats and each beat becomes one
   scene: an assembled prompt, that endpoint's own parameters, the reference image it
   uses, and the lint score. Edit a beat and rebuild.
5. **The requests it becomes** — one job per scene, with the exact method, path and
   body. That panel is a **dry run**; see the gate below.

![Prompt Layer — the Video tab: the reading of a concept, the questions it asks, two
scenes with their prompts, and the request each one becomes](docs/screenshot-video.png)

**Reference images** attach to that storyboard, and they are measured, never
uploaded: the browser reads dimensions, MIME type, byte size and the dominant
colours, and sends only those numbers. Attach a 1920×1080 still to `sora-2` and it
tells you the endpoint documents `720x1280` or `1280x720` — with the sentence from
the guide — instead of letting the request fail. Attach one to a text-to-video
endpoint and it says the image has nowhere to go, rather than dropping it quietly.

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
| `GET` | `/api/models` | models, expanders, dataset version, improve vocabulary, preset counts |
| `GET` | `/api/models?slug=` | one model: layers, params, sources, image support |
| `GET` | `/api/presets` | Hugging Face presets: `?lang=&task=&limit=`, `?collection=widget-examples&section=&task=`, `?model=<hf id>` |
| `POST` | `/api/preflight` | `{model, prompt, options?, system_message?, image_count?}` |
| `POST` | `/api/improve` | `{model, prompt, selections?, palette?, template?, clean?, image?}` |
| `POST` | `/api/storyboard` | `{action:"plan"\|"scenes", concept, answers?, model?, design?, beats?, references?}` |
| `POST` | `/api/reference` | `{model, images:[{role,width,height,type,bytes,palette}]}` — measurements only |
| `POST` | `/api/video` | `{action:"plan"\|"submit", storyboard\|concept, model?, confirm?}` — dry run unless gated |
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

```bash
curl -s 'localhost:8090/api/presets?collection=widget-examples&section=Computer%20Vision' \
  | jq '.data.types, .data.presets[4].examples'
```

```bash
curl -s 'localhost:8090/api/presets?lang=en&task=fill-mask' | jq '.data.presets[].text'
```

```bash
curl -s -X POST localhost:8090/api/storyboard -H 'Content-Type: application/json' \
  -d '{"action":"scenes","model":"sora-2","concept":"A detective named Mara walks through a rain-soaked alley at night. Then she finds a glowing book in a doorway and picks it up.","answers":{"camera_move":"slow pan left"},"design":{"template":"labelled"},"palette":"amber, cream, walnut brown"}' \
  | jq '.data.totals, .data.scenes[].prompt'
```

```bash
curl -s -X POST localhost:8090/api/video -H 'Content-Type: application/json' \
  -d '{"action":"plan","model":"sora-2","concept":"A lantern drifts down a river at dusk"}' \
  | jq '.data.dry_run, .data.gate.reason, .data.jobs[0].body'
```

## Run it

```bash
python dev.py          # http://127.0.0.1:8090   (emulates the Vercel layout)
python -m pytest -q    # 178 tests
```

No dependencies — the service, the dataset and the functions are all standard
library.

## Deploy (Vercel)

1. Import this repo at [vercel.com/new](https://vercel.com/new).
2. Framework preset **Other**. No build command, no output directory.
3. Deploy. `/` serves the UI; the nine files in `api/` answer at `/api/health`,
   `/api/models`, `/api/presets`, `/api/preflight`, `/api/improve`,
   `/api/storyboard`, `/api/reference`, `/api/video`, `/api/compile`.

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

Generation has its **own** gate — `ALLOW_REMOTE_GENERATION=1`, `VIDEO_GATEWAY_URL`,
`VIDEO_API_KEY`, plus `confirm: true` on the request — and the two are deliberately
separate, so enabling prompt expansion can never silently enable paid generations.
Until then `/api/video` returns the exact requests and spends nothing.

## Layout

```
index.html            the app (no build step, no framework)
api/health.py         \  Vercel Python functions — each exports `handler`
api/models.py          |
api/presets.py         |
api/preflight.py       |
api/improve.py         |
api/storyboard.py      |  the chat turn and the scenes
api/reference.py       |  the measured-image checks
api/video.py           |  the requests, and the generation gate
api/compile.py        /
promptlayer/
  assembler.py        pure assembly logic (no I/O)
  layers.json         the dataset: every fact + its source and evidence
  improve.py          the lint, the deterministic cleanup, the design merge
  design.json         the improve vocabulary: 11 groups, each item sourced
  storyboard.py       read the concept, ask the gaps, split into beats, build scenes
  storyboard.json     the planner's rules and questions, each with its source
  reference.py        what an image measures to, and what the endpoint does with it
  video.py            one job per scene + the three-way generation gate
  presets.py          Hugging Face preset loaders + the one live card read
  hf-presets.json     parsed Hub defaults (201 presets, 14 languages)
  hf-widget-examples.json  parsed widget examples (28 types)
  vendor/             the upstream files, verbatim, so the parse can be re-checked
  http.py             shared request/response helpers
scripts/              the two parsers that generate the preset datasets
dev.py                local emulation of the Vercel layout
tests/                178 tests: datasets, assembly, improvement, the planner, the
                      deployed entry points
```

Adding a model is one entry in `promptlayer/layers.json`; adding a lint rule or a
design element is one entry in `promptlayer/design.json`. The tests fail an entry
whose `source` is not a fetchable identifier, which is the point: the value of
this tool is that every claim can be checked.

## Scope and honesty

- Provider defaults drift. `layers.json` and `design.json` are versioned and
  surfaced in the footer; refreshing them means re-reading the specs and bumping
  `version`.
- The preset datasets are generated, never typed. If upstream changes the shape of
  `default-widget-inputs.ts` or the widget docs, the fidelity test fails loudly
  instead of the tool serving a stale list.
- The widget docs describe the *shape* of a card's examples. Where a field is nested
  (`sentences:`, `structured_data:`) the entry is flagged `nested` and the verbatim
  block is kept, rather than flattening it into something the doc does not say.
- Where a provider's behaviour is undocumented, the entry is omitted rather than
  filled in — an empty answer beats a confident wrong one.
- The lint is a checklist, not a critic. It has no model behind it and no opinion
  beyond the rules the guides actually state; it will not tell you your idea is
  bad, and it does not pretend a higher score means a better video.
- Model identities are those published by the providers. This tool reads public
  API schemas and open-source repositories; it does not reverse-engineer anything
  private.
- **The planner has no model behind it either.** Reading the concept, choosing the
  questions and splitting the beats are pattern matches over your own words — no
  LLM is called anywhere in this repo, so nothing is generated that cannot be
  pointed at. Each rule and each question carries the guide that published it; the
  cue words that find the beat boundaries are this tool's own heuristic and
  `storyboard.json` marks them `sourced: false` so the UI can say so.
- **An undocumented parameter is never sent.** Each scene writes a value only where
  the endpoint's own schema lists it; anything else comes back as an
  `undocumented_param` note naming the parameter and the reason. A request that
  fails at the provider is worse than a note.
- **The chat's answers are placed or reported.** An answer that is a documented
  design element is merged, one that names a documented parameter becomes it, and
  anything else produces a note saying it belongs in the concept prose. An answer
  is never silently dropped on the floor between the conversation and the prompt.
- **Nothing is generated for you.** `/api/video` builds the requests and stops.
  The gate that would let it send them is off unless the deployment sets all three
  variables *and* the request confirms.

Built as a [tokN](https://tokn.co.in) tool.

MIT — see [LICENSE](LICENSE).
