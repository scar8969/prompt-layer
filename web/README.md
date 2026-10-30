# Prompt Layer Studio — client

A prompt-centric, visual-first studio for AI image and video generation, built
against the backend contract in `../docs/API_CONTRACT.md`. Model access is
bring-your-own-key / bring-your-own-model: you connect a provider endpoint, the
backend stores the key, and the models you register on it appear in the studios.

## Run it

```
cd web
npm run dev        # vite dev server on http://localhost:5173
npm run typecheck  # tsc --noEmit
npm run build      # production bundle into web/dist
```

Nothing needs installing: React, Vite and TypeScript are already in
`node_modules`. Plain CSS only — no Tailwind, no component library, no state or
routing dependency.

The dev server proxies `/api` to `http://127.0.0.1:8091` with
`changeOrigin: false` so the httpOnly `studio_session` cookie is issued for the
dev origin. Point the backend at a different port by editing
`vite.config.ts`.

## What is here

```
src/
  api/         transport, error normalisation, typed endpoint functions, contract types
  app/         hash router, navigation table, application shell
  components/  the shared UI kit (buttons, fields, dialogs, media, states, …)
  features/    one directory per surface
  state/       session, system status, query cache, settings, derived job polling
  styles/      design tokens and every rule in the app
```

Twelve surfaces, all reachable from the rail: Home/Discover, Prompt Lab, Image
Studio, Video Studio, Projects, Asset Library, Presets & Workflows, Character
Library, History & Jobs, Models, API Connections, Settings. Detail screens exist
for jobs, projects, models and connections.

Prompt Lab is the layer dataset: pick a model the repository has facts for, paste a
prompt, and read the payload that reaches the provider, every layer that touches it
and the parameter that turns each one off. The same dataset fills the prompt-layers
section of the model inspector and the documented-model picker in the connection
wizard, so a model is added by name rather than by remembering an endpoint id.

## Decisions worth knowing

**No invented state.** Every image, video, job, cost, token and percentage on
screen came from a backend response. There is no seeded sample data, no
placeholder artwork, no optimistic "success". When a response is missing a key
the client raises `invalid_response` instead of filling in a default.

**Mock disclosure has one source.** `components/Badge.tsx` exports
`MockBadge({ isMock })`, and it renders *only* when the backend sent
`is_mock: true`. Model lists, the model picker, job cards, job detail, the
studio canvas, the asset grid and the connection list all route the flag through
it, so a local development placeholder cannot be shown bare.

**Capability gating is fail-closed.** A mode, aspect ratio, duration, resolution,
camera move, seed, negative prompt, mask or audio switch is enabled only when the
model's capability profile declares it. Otherwise the control is disabled (or
hidden) with a sentence saying why. The vocabulary a form offers comes from the
backend too: `/api/models/{id}/capabilities` for the controls, `/api/prompt/modes`
for the generation modes, so a name the service would refuse cannot reach a
request from here.

**Cost and billing.** `lib/util.ts → formatCost()` appends "— estimate" whenever
`cost_estimate.is_estimate` is true, and screens additionally state that a
non-billable request is billed by your own provider account. The platform's
billing mode is rendered from `system.status.billing` through
`billingSentence()`; "billing unconfigured" is shown as the backend reports it.

**Progress.** `controls → ProgressBar` draws a percentage only when
`progress_is_estimate === false` **and** a numeric `progress` exists. The payload
carries neither today, so a running job shows its state and says the provider
reported no measured progress — which is what is true.

**Two vocabularies, one translation.** The server stores a job's `status`
(`queued` … `completed`); the screens speak `state` (`succeeded`). The mapping is
in `api/endpoints.ts` and `tests/test_job_vocabulary.py` fails if the database
grows a status it does not translate. Readers translate every wire name to the
view model in the same file, and `tests/test_client_contract.py` fails when a
reader reads a field the payload does not carry.

**Camera moves.** A move with `is_native: false` is labelled "prompt wording
only" both in the option label and in an inline note; it is described as wording
added to the prompt, never as camera control.

**Errors.** `ApiError` keeps the backend's `code`, `message`, `hint`,
`details` and `request_id`. `ErrorState` and `ErrorNotice` print all of them
verbatim. Nothing is paraphrased and nothing is swallowed.

**Keys.** The API key is typed into exactly two places (the connection wizard and
the key-rotation field), posted once, and cleared from component state as soon as
the backend answers. Only the masked summary (`••••last4`) is ever rendered
back. There is no code path that puts a credential into localStorage, a URL or a
log: `state/storage.ts` is the only persistence layer and it refuses any key
outside a four-item allow-list *and* any value matching a secret pattern. Settings
re-reads localStorage and reports what it finds.

**Session.** The session cookie is httpOnly, so the client never sees it. The
CSRF token arrives in the login/register payload and lives in a module variable;
on reload `adoptCsrfFromCookie()` picks it up from a readable CSRF cookie if the
backend publishes one. If no token can be found, the header is omitted rather
than faked, and the backend's own rejection (with `request_id`) is displayed.

**Live job state.** `state/jobs.ts` polls a non-terminal job with backoff, stops
as soon as the job is terminal, and suspends entirely while the tab is hidden. It
never fabricates a percentage between polls.

**Accessibility.** Semantic landmarks, a skip link, labelled controls, roving
tabindex in the segmented control and tabs, focus moved to `<main>` on route
change, native `<dialog>` for sheets and a focus-trapped drawer, `aria-live`
regions for job state and toasts, alt text on every image, a visible focus ring,
and `prefers-reduced-motion` plus an explicit `[data-motion='reduced']`
override driven by Settings.

**Responsive.** Three columns at ≥1280px; the inspector collapses into a drawer
at ≤1279px (the trigger is in the page header); one column at ≤900px; the rail
becomes a bottom tab bar at ≤767px.

## Assumptions a reviewer should double-check

1. **No prompt-improvement endpoint exists in the contract.** The contract lists
   no assistant route, so `api/endpoints.ts → assistant.improve()` probes three
   plausible paths (`/api/prompts/improve`, `/api/prompts/improvements`,
   `/api/improve`) and rethrows any non-404 error. When none answer, the panel in
   the studio reports the backend's response verbatim and offers no suggestion.
   It is never faked.
2. **CSRF after reload.** The contract returns `session.csrf_token` only from
   login and register. A reload therefore depends either on the backend also
   setting a readable CSRF cookie (names tried: `csrf_token`, `csrf`,
   `xsrf-token`, `xsrf`, `studio_csrf`) or on `/api/auth/me` returning the
   token. If neither holds, mutating requests will be refused by the backend and
   the refusal is shown as-is. Confirm which mechanism the backend uses.
3. **Preset thumbnails.** `Preset.thumbnail_url` is optional and not in the
   contract. A preset card renders an image only when the field actually carries a
   URL; otherwise it says no thumbnail was returned rather than showing stock art.
4. **Extra header values are not readable.** The contract exposes
   `extra_header_names` but never the values, so the connection detail screen
   shows the names and requires a value to be re-entered to change one. It does
   not pretend the existing values are known.
5. **Upload transport.** `POST /api/assets` is documented as taking
   `data_base64`, so the browser reads the file, base64-encodes it and posts it in
   one request. The size ceiling is the backend's `max_input_bytes`; the client
   does not enforce a limit it cannot verify.
6. **Model registration shape.** `ModelInput` is sent exactly as the contract
   describes, including `parameter_schema.parameters`. The wizard validates that
   the JSON has a `parameters` array and refuses to submit anything else.
7. **Rate limits and quotas** are not modelled client-side. `rate_limited` /
   `rate_limit_exceeded` from the backend is displayed verbatim; there is no
   client-side cooldown timer, because inventing one would misrepresent the
   backend's behaviour.
8. **Jobs of unknown state.** `JobState` is an open string union. Any state
   outside `succeeded/failed/cancelled/canceled` is treated as non-terminal and
   polled; the client never assumes a job finished because it does not recognise
   the state name.

## Deliberate omissions

- No canvas mask painting UI. The backend accepts `mask_asset_id`, so the client
  lets you pick an existing mask asset; a paint surface would produce an image the
  client made up rather than one the API returned.
- No drag-to-reorder in projects or presets: the contract has no order field.
- No token-count or "credits remaining" display: the backend exposes neither.
- No dark/light **per-surface** theming; theme is a single app-wide preference.
