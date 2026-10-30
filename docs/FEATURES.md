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
| **Not built** | Needs something this repository does not have: a model we train, an audio pipeline, a canvas runtime, or a provider-specific endpoint. Named rather than promised. |

---

## Images

| Higgsfield | Here | Notes |
|---|---|---|
| Text-to-image with 50+ style presets | **Built** | 40 image presets, each a sourced prompt template with settings, a frame and required references. The library is a visual grid with category filters; the catalogue ships no preview images, so a tile says "no preview" rather than faking one. |
| Soul (their own fashion/cinema model) | **Not built** | We do not train or host models. You connect Nano Banana, Seedream, FLUX, Qwen, Gemini image or any fal endpoint and get the same control. |
| Soul Reference (lock a look from an image) | **Built** | Reference images with a role — `composition`, `style`, `character_identity`, `color_palette`, `pose`, `lighting` — attached per request, gated on what the model declares. |
| Soul ID (train an identity from 20–80 photos) | **Partial** | A character stores a description and reference images; casting it appends the description to the prompt and attaches its references. No training, so the face is locked by references, not by weights. |
| Soul HEX (palette from a photo) | **Partial** | `color_palette` is a reference role and the prompt layer reports whether the model accepts one. No palette extraction tool. |
| Moodboard (style learned from up to 80 refs) | **Not built** | Some endpoints take several references; there is no moodboard object that learns a style. |
| Photodump (15 images, 15 styles, one character) | **Not built** | Batch count is bounded by the model's declared `max_outputs`; the 15-style sweep is not a feature here. |
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
| Cinema Studio (camera body, lens, aperture, genre, era, tempo, lighting, palette, emotion wheel, 50 refs, 30 s) | **Partial** | A model's `parameter_schema` can carry any of those fields and the studio renders them; multi-shot and first/last-frame modes exist. There is no Director's-Panel UI. |
| Extend a clip (sequel / prequel) | **Built** | `POST /api/generations/{id}/extend` with seconds and an optional continuation; the job screen has the form. |
| Motion transfer from a reference video | **Wired to your key** | `motion_transfer` is a declared mode; no UI flow yet. |
| Recast / face swap | **Not built** | Provider-specific; would need its own adapter with a known wire format. |
| Lipsync, talking avatar, voiceover, translation | **Not built** | No audio pipeline, no TTS/ASR provider, no lip-sync model. |
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
| Canvas: node pipelines, realtime collaboration | **Not built** | Jobs are linear, with retry, variations and derived requests. |
| Marketing Studio, UGC factory, Fashion factory | **Not built** | Product-URL-to-ad and UGC campaigns are their own product surface. |
| Credits and plans | **Not built, on purpose** | You spend your own provider account. The platform's billing mode is `byok` and it says so. |
| MCP access to the preset catalogue | **Not built** | The HTTP API is the surface; an MCP server would be a thin wrapper over `/api/prompt/catalog`. |

---

## What "all the features" would still cost

In order of what a user would notice first, and without pretending any of it is
one commit:

1. **Previews for presets.** 70 presets, 0 images. This is the single biggest
   difference from Higgsfield: their presets are pictures, ours are sentences. It
   needs either a connected provider to render one frame per preset (cheap, honest,
   and it would light up the grid) or a committed asset per preset.
2. **A Director's Panel.** Camera body, lens, aperture, lighting, palette, emotion —
   all of it is expressible as a model's `parameter_schema` today; the panel is
   interface work, not a new backend.
3. **Audio.** Lipsync, voiceover, translation-dubbing: a new provider family and a
   new job kind (media in, media + track out).
4. **A canvas.** Node graph over the existing routes, with saved pipelines.
5. **Anything that needs our own weights** — Soul, Soul ID's training, DoP's VFX:
   a fine-tuning pipeline and a GPU budget, which is exactly what a BYO-key platform
   is designed to avoid.
