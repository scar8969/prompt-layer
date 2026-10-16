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

## Two tools

**Preflight** — paste a prompt, pick a model, get:

- the exact upstream payload (messages for chat, forwarded prompt + params for video),
- every parameter **with its source** — `provider default` vs `your override`,
- each layer touching the request: what it does, whether it is active, and the knob
  that turns it off,
- `full_control_patch`: the JSON that switches every additive layer off in one go,
- the source + evidence string for each claim, expandable in the UI.

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

## Endpoints

| method | path | purpose |
|---|---|---|
| `GET` | `/api/health` | liveness + dataset size |
| `GET` | `/api/models` | models, expanders, dataset version |
| `GET` | `/api/models?slug=` | one model: layers, params, sources |
| `POST` | `/api/preflight` | `{model, prompt, options?, system_message?, image_count?}` |
| `POST` | `/api/compile` | `{prompt, style, model?, dry_run?}` |

```bash
curl -s -X POST localhost:8090/api/preflight -H 'Content-Type: application/json' \
  -d '{"model":"hailuo-02-standard","prompt":"a cat surfing a huge wave","options":{"prompt_optimizer":false}}' \
  | jq '.data.prompt.rewritten_upstream, .data.params.prompt_optimizer'
```

## Run it

```bash
python dev.py          # http://127.0.0.1:8090   (emulates the Vercel layout)
python -m pytest -q    # 33 tests
```

No dependencies — the service, the dataset and the functions are all standard
library.

## Deploy (Vercel)

1. Import this repo at [vercel.com/new](https://vercel.com/new).
2. Framework preset **Other**. No build command, no output directory — the repo
   root is static and every file in `api/` becomes a function.
3. Deploy. `/` serves the UI; the four functions answer under `/api/*`.

`vercel.json` sets `includeFiles: promptlayer/**` on every function. That matters:
the shared logic lives in a Python package outside `api/`, and without it the
functions would import-fail at runtime with a 500. If a deploy ever 500s on
`/api/health`, that is the first thing to check.

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
api/compile.py        /
promptlayer/
  assembler.py        pure assembly logic (no I/O)
  layers.json         the dataset: every fact + its source and evidence
  http.py             shared request/response helpers
dev.py                local emulation of the Vercel layout
tests/                33 tests: dataset, assembly, and the deployed entry points
```

Adding a model is one entry in `promptlayer/layers.json`. The tests fail an entry
whose `source` is not a fetchable identifier, which is the point: the value of
this tool is that every claim can be checked.

## Scope and honesty

- Provider defaults drift. `layers.json` is versioned and surfaced in the footer;
  refreshing it means re-reading the endpoint specs and bumping `version`.
- Where a provider's behaviour is undocumented, the entry is omitted rather than
  filled in — an empty answer beats a confident wrong one.
- Model identities are those published by the providers. This tool reads public
  API schemas and open-source repositories; it does not reverse-engineer anything
  private.

Built as a [tokN](https://tokn.co.in) tool.

MIT — see [LICENSE](LICENSE).
