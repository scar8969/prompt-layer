# Where this platform stands against Higgsfield

Higgsfield is a commercial suite with its own models (Soul, Soul ID, DoP, Cinema
Studio), audio, and a node canvas. This platform is the opposite bet: **no models of
its own, your keys for everyone else's** — plus the one thing a BYO-key tool can
offer that a closed suite cannot, a stated account of what each provider does to
your prompt before you pay (`/prompt`, `docs/API.md`, `promptlayer/layers.json`).

So "all the features" splits into three groups, and only the first two are honest
to claim:

| | Meaning |
|---|---|
| **Built** | It works here today, on any model you connect, and the tests say so. |
| **Wired to your key** | The capability exists as a route and a control, and it works if the model you connected declares it. Whether it runs is the provider's answer, not ours. |
| **Not built** | Needs something this repository does not have: a model we train, a provider-specific wire format, or a public feed of other people's work. Named rather than promised. |

---

## Images

| Higgsfield | Here | Notes |
|---|---|---|
| Text-to-image with 50+ style presets | **Built** | 40 image presets, each a sourced prompt template with settings, a frame and required references. The library is a visual grid with category filters; a tile is a deterministic style card until this workspace spends its own key on a preview, and then it is the real render, labelled as such. |
| Soul (their own fashion/cinema model) | **Not built** | We do not train or host models. You connect Nano Banana, Seedream, FLUX, Qwen, Gemini image or any fal endpoint and get the same control. |
| Soul Reference (lock a look from an image) | **Built** | Reference images with a role — `composition`, `style`, `character_identity`, `color_palette`, `pose`, `lighting` — attached per request, gated on what the model declares. |
| Soul ID (train an identity from 20–80 photos) | **Partial** | A character stores a description and reference images; casting it appends the description to the prompt and attaches its references. No training, so the face is locked by references, not by weights. |
| Soul HEX (palette from a photo) | **Partial** | `color_palette` is a reference role and the prompt layer reports whether the model accepts one. No palette extraction tool. |
| Moodboard (style learned from up to 80 refs) | **Not built** | Some endpoints take several references; there is no moodboard object that learns a style. |
| Photodump (15 images, 15 styles, one character) | **Built** | `/api/factories` with `kind: "photodump"` plans one subject across catalogue styles into a canvas, one node per style, each naming the preset it came from. It is capped at 12 styles per run, and the batch still runs one job per node. |
| Inpaint / Draw to Edit | **Built** | `POST /api/generations/{id}/edit` with `inpaint`; the job screen has the form. A mask is optional and only offered when the model declares `inpaint`. |
| Outpaint (expand the canvas) | **Wired to your key** | Same route with `outpaint` and an expansion box; requires the model to declare outpaint. |
| Multi-reference | **Wired to your key** | Requests carry a reference list up to the endpoint's declared maximum. |
| Image upscale | **Wired to your key** | `upscale` is a declared mode; a model that does not declare it shows a disabled control with the reason. There is no separate upscaler model. |
| Aspect / quality / batch | **Built** | Aspect ratio and resolution are validated against the model profile; anything else is a parameter the model's own schema declares. |

## Video

| Higgsfield | Here | Notes |
|---|---|---|
| Text-to-video | **Built** | Any connected model that declares `text_to_video`. |
| Image-to-video from a keyframe | **Built** | `image_to_video` with a first-frame reference. |
| First/last frame control (Wan-style) | **Wired to your key** | `first_frame` / `first_last_frame` modes, each gated on declaration. |
| DoP camera presets (bullet time, crash zoom, orbit, FPV) | **Partial** | 12 camera moves with the wording each adds, plus 30 video presets that carry camera settings. The move is a control in the studio, not a grid of VFX tiles bound to models. |
| Cinema Studio (camera body, lens, aperture, genre, era, tempo, lighting, palette, emotion wheel, 50 refs, 30 s) | **Built** | The Director's Panel (`/cinema`) compiles body, glass, light and grade into one prompt, clause by clause, and `POST /api/cinema/compile` exposes the same compiler. Multi-shot and first/last-frame modes are declared per model. |
| Extend a clip (sequel / prequel) | **Built** | `POST /api/generations/{id}/extend` with seconds and an optional continuation; the job screen has the form. |
| Motion transfer from a reference video | **Wired to your key** | `motion_transfer` is a declared mode; no UI flow yet. |
| Recast / face swap | **Not built** | Provider-specific; would need its own adapter with a known wire format. |
| Lipsync, talking avatar, voiceover, translation | **Wired to your key** | `POST /api/generations/{id}/audio` with `lip_sync`, `voiceover` or `dub`; the job screen has the form. A lip sync is refused without a track and a voiceover without a script. No audio model is hosted here: the mode runs only on a model you connect that declares it. |
| Video upscale, frame interpolation, deflicker | **Not built** | These are their own models; none is connected here. |
| Restyle a clip | **Built** | `POST /api/generations/{id}/transform` with `restyle` / `video_to_video` / `style_transfer`, gated on the model. |
| Reframe for each aspect ratio | **Wired to your key** | Aspect ratio per generation; batch reframe is not a feature. |

## Systems

| Higgsfield | Here | Notes |
|---|---|---|
| Characters / Elements / AI Cast | **Built** | Characters with attributes, prompt details, preferred models and reference images, plus casting them into a request. |
| Preset library (70+ presets) | **Built** | 70 presets — 40 image, 30 video — every one a sourced prompt template. |
| Cinema multi-shot scene | **Wired to your key** | `multi_shot` and `scene_continuation` modes. |
| Projects, assets, history, cost records | **Built** | Plus per-request usage rows that record what the provider charged, when it said. |
| Trending / community feed | **Not built** | Needs other people's generations; there is no public feed here. |
| Canvas: node pipelines, realtime collaboration | **Partial** | `/canvas` saves a validated DAG of generate and note nodes, runs it node by node, and refuses a cycle by naming the nodes on it. Each node is one ordinary generation job, and a node is queued only after its upstream has a stored output. Collaboration is conflict-safe rather than live: every save carries the revision it read, a stale save is a 409 naming the current revision, and an idle editor adopts newer revisions by polling. Live cursors and presence are not built. |
| Marketing Studio, UGC factory, Fashion factory | **Built** | `/marketing` reads a product page's own metadata, composes a brief deterministically (no model writes the copy), and queues one real render per format. The factories (`/api/factories`) plan a UGC shoot, a fashion lookbook or a photodump into a canvas of shots; planning and saving are free, and only running the canvas queues jobs. |
| Credits and plans | **Not built, on purpose** | You spend your own provider account. The platform's billing mode is `byok` and it says so. |
| MCP access to the preset catalogue | **Built** | `studio/mcp.py` plus `scripts/preset_mcp.py` expose five read-only tools over stdio: list/get presets, compile a brief, list camera moves, read prompt layers. No tool can spend, and the dispatcher holds no credential. |

---

## What "all the features" would still cost

What is done, and what remains. The four items that were named here — previews, a
Director's Panel, audio, a canvas — are built; what is left is smaller, and one
item is deliberately not attempted.

1. **Previews for presets — built, per workspace.** A tile is a real frame once the
   workspace spends its own key to render one (`POST /api/presets/{id}/preview`),
   and a style card before that. What is still missing is a *shipped* preview set:
   70 presets with no render look like a catalogue, not a gallery, until somebody
   runs them. That is a deliberate cost decision, not a missing feature.
2. **A Director's Panel — built.** `/cinema` compiles body, glass, light, grade and
   mood into one prompt with a per-clause ledger. What it does not do is bind a VFX
   preset to a model's own effect id; that needs a provider that publishes one.
3. **Audio — routed to your key.** Voiceover, dub and lip sync exist as derived
   jobs with their own requirements. No audio model is hosted, so a workspace with
   no audio-capable provider sees a disabled control and the reason.
4. **A canvas — built for a DAG, with conflict-safe editing.** Nodes and edges, a
   topological order, upstream outputs feeding downstream requests, and a revision
   on every save so two editors cannot silently overwrite each other. Live cursors
   and presence are not built.
5. **Anything that needs our own weights** — Soul, Soul ID's training, DoP's VFX:
   a fine-tuning pipeline and a GPU budget, which is exactly what a BYO-key platform
   is designed to avoid. Still not attempted, still on purpose.
