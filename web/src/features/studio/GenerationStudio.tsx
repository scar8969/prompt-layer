import { useEffect, useMemo, useState } from 'react';
import {
  assets as assetsApi,
  assistant,
  generations,
  presets as presetsApi,
  projects as projectsApi,
} from '../../api/endpoints.ts';
import type {
  Asset,
  ImageGenerationInput,
  Job,
  Model,
  Preset,
  Project,
  ReferenceInput,
  VideoGenerationInput,
} from '../../api/types.ts';
import { Badge, MockBadge } from '../../components/Badge.tsx';
import { Button, IconButton } from '../../components/Button.tsx';
import { ProgressBar, Segmented, Slider } from '../../components/Controls.tsx';
import { Section } from '../../components/Data.tsx';
import { Checkbox, Select, TextArea, TextInput } from '../../components/Field.tsx';
import { ErrorNotice } from '../../components/ErrorNotice.tsx';
import { ErrorState, LoadingState } from '../../components/State.tsx';
import { Icon } from '../../components/Icon.tsx';
import { AssetImage, MediaCanvas, outputToMedia } from '../../components/Media.tsx';
import { Link, useRouter } from '../../app/router.tsx';
import { invalidate, useQuery } from '../../state/query.ts';
import { useModelCapabilities, useModelParameters, useModels } from '../../state/models.ts';
import { useDefaultModelId } from '../../state/models.ts';
import { useJobWatch, isTerminalState } from '../../state/jobs.ts';
import { Drawer } from '../../components/Dialog.tsx';
import { useSettings } from '../../state/settings.tsx';
import { useToasts } from '../../state/toasts.tsx';
import { Ids, formatCost, truncate } from '../../lib/util.ts';
import { ModelPicker } from './ModelPicker.tsx';
import { ParameterFields } from './ParameterFields.tsx';

type StudioKind = 'image' | 'video';

/**
 * The generation surface shared by Image Studio and Video Studio. The only
 * difference between the two is which optional fields the video form adds and
 * which endpoint receives the request; both are gated on what the selected
 * model actually declares.
 */
export function GenerationStudio({ kind }: { kind: StudioKind }) {
  const models = useModels({ enabled: true });
  const { model: preferred } = useDefaultModelId();
  const settings = useSettings();
  const toasts = useToasts();
  const { navigate } = useRouter();

  const [modelId, setModelId] = useState<string | null>(null);
  const [mode, setMode] = useState<string | null>(null);
  const [prompt, setPrompt] = useState('');
  const [negativePrompt, setNegativePrompt] = useState('');
  const [parameters, setParameters] = useState<Record<string, unknown>>({});
  const [count, setCount] = useState(1);
  const [seed, setSeed] = useState('');
  const [projectId, setProjectId] = useState('');
  const [presetId, setPresetId] = useState('');
  const [references, setReferences] = useState<ReferenceInput[]>([]);
  const [duration, setDuration] = useState<number | null>(null);
  const [aspectRatio, setAspectRatio] = useState('');
  const [resolution, setResolution] = useState('');
  const [cameraMove, setCameraMove] = useState('');
  const [generateAudio, setGenerateAudio] = useState(false);
  const [firstFrame, setFirstFrame] = useState('');
  const [lastFrame, setLastFrame] = useState('');

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<unknown>(null);
  const [submitted, setSubmitted] = useState<Job | null>(null);
  // Below 1280px the right-hand inspector becomes a drawer, so its controls stay
  // reachable rather than silently disappearing.
  const [inspectorOpen, setInspectorOpen] = useState(false);

  const capabilities = useModelCapabilities(modelId);
  const schema = useModelParameters(modelId);
  const eligible = useMemo(
    () => (models.data ?? []).filter((model) => model.modality === kind),
    [models.data, kind],
  );

  // Pick a model once the registry arrives: the workspace default, then the
  // first eligible one. Nothing is selected optimistically before that.
  useEffect(() => {
    if (modelId !== null || !eligible.length) return;
    const remembered = settings.defaultModelId
      ? eligible.find((model) => model.id === settings.defaultModelId)
      : undefined;
    setModelId((remembered ?? preferred ?? eligible[0]).id);
  }, [eligible, modelId, preferred, settings.defaultModelId]);

  const model: Model | null = eligible.find((item) => item.id === modelId) ?? null;
  const capabilityList = capabilities.data?.modes ?? [];
  const currentCapabilities = capabilities.data?.capabilities ?? model?.capabilities ?? null;

  // The first mode the profile actually supports. A mode the profile did not
  // declare is never preselected.
  useEffect(() => {
    setParameters({});
    setMode(null);
    setDuration(null);
    setAspectRatio('');
    setResolution('');
    setCameraMove('');
    setGenerateAudio(false);
    setFirstFrame('');
    setLastFrame('');
    setSeed('');
    setReferences([]);
    setPresetId('');
  }, [modelId]);

  useEffect(() => {
    if (mode !== null) return;
    const supported = capabilityList.find((option) => option.supported);
    const declared = model?.modes[0];
    setMode(supported?.key ?? declared ?? null);
  }, [capabilityList, model, mode]);

  const projects = useQuery<Project[]>('projects', () => projectsApi.list(), { staleMs: 30_000 });
  const presets = useQuery<Preset[]>(`presets?modality=${kind}`, () => presetsApi.list({ modality: kind }), {
    staleMs: 30_000,
  });
  const referenceAssets = useQuery<Asset[]>(
    `assets?kind=image&limit=40`,
    async () => (await assetsApi.list({ kind: 'image', limit: 40 })).assets,
    { staleMs: 30_000 },
  );

  const supportsNegative = currentCapabilities?.supports_negative_prompt === true;
  const supportsSeed = currentCapabilities?.supports_seed === true;
  const cameraMoves = capabilities.data?.camera_moves ?? [];

  const applyPreset = (preset: Preset) => {
    setPresetId(preset.id);
    if (preset.prompt_template) setPrompt(preset.prompt_template);
    if (preset.negative_prompt_template && supportsNegative) setNegativePrompt(preset.negative_prompt_template);
    if (preset.aspect_ratio) setAspectRatio(preset.aspect_ratio);
    if (preset.parameters) setParameters({ ...preset.parameters });
    if (preset.model_preference) {
      const match = eligible.find(
        (item) => item.id === preset.model_preference || item.model_key === preset.model_preference,
      );
      if (match) setModelId(match.id);
    }
    toasts.push({
      tone: 'info',
      title: `Preset "${preset.name}" applied`,
      text: 'Review the prompt and parameters before you submit.',
    });
  };

  const { job, error: watchError } = useJobWatch(submitted);

  const canSubmit =
    model !== null && mode !== null && prompt.trim().length > 0 && !submitting && !(job && !isTerminalState(job.state));

  const submit = async () => {
    if (!model || !mode) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const base: ImageGenerationInput = {
        model_id: model.id,
        mode,
        prompt: prompt.trim(),
        client_request_id: Ids.next('req'),
        project_id: projectId || null,
        preset_id: presetId || null,
        count,
      };
      if (supportsNegative && negativePrompt.trim()) base.negative_prompt = negativePrompt.trim();
      if (Object.keys(parameters).length) base.parameters = parameters;
      if (references.length) base.references = references;
      if (supportsSeed && seed.trim()) {
        const parsed = Number(seed);
        if (Number.isFinite(parsed)) base.seed = Math.trunc(parsed);
      }

      let created: Job;
      if (kind === 'video') {
        const video: VideoGenerationInput = { ...base };
        if (duration !== null) video.duration_seconds = duration;
        if (aspectRatio) video.aspect_ratio = aspectRatio;
        if (resolution) video.resolution = resolution;
        if (cameraMove) video.camera_move = cameraMove;
        if (currentCapabilities?.supports_audio) video.generate_audio = generateAudio;
        if (firstFrame) video.first_frame_asset_id = firstFrame;
        if (lastFrame) video.last_frame_asset_id = lastFrame;
        created = await generations.video(video);
      } else {
        created = await generations.image(base);
      }

      setSubmitted(created);
      invalidate('jobs');
      toasts.push({
        tone: 'info',
        title: 'Request accepted',
        text: `Job ${created.id} is ${created.state}. The state below comes from the backend.`,
      });
    } catch (err) {
      setSubmitError(err);
    } finally {
      setSubmitting(false);
    }
  };

  if (models.loading) return <LoadingState label="Reading the model registry…" />;
  if (models.error) return <ErrorState error={models.error} onRetry={models.reload} />;

  const outputs = job?.outputs.map(outputToMedia) ?? [];
  const current = outputs[0] ?? null;
  const busy = job !== null && !isTerminalState(job.state);

  const inspector = (
    <>
        <Section title="Request settings">
          <div className="stack stack--tight">
            <Slider
              label="Outputs to request"
              value={count}
              min={1}
              max={currentCapabilities?.max_outputs ?? 4}
              step={1}
              onChange={setCount}
              format={(value) => String(value)}
              disabled={(currentCapabilities?.max_outputs ?? 4) < 2}
              disabledReason="The model profile allows only one output per request."
            />

            {supportsSeed ? (
              <TextInput
                label="Seed"
                type="number"
                value={seed}
                onChange={(event) => setSeed(event.target.value)}
                hint="Leave empty to let the provider choose."
              />
            ) : (
              <p className="dim" style={{ margin: 0, fontSize: 'var(--fs-xs)' }}>
                This model does not declare seed support, so no seed is sent.
              </p>
            )}

            <Select
              label="Project"
              value={projectId}
              onChange={(event) => setProjectId(event.target.value)}
              placeholder="No project"
              options={(projects.data ?? []).map((project) => ({
                value: project.id,
                label: project.name,
              }))}
              hint="Assets created by this job are filed into the project."
            />

            <Select
              label="Preset"
              value={presetId}
              onChange={(event) => {
                const preset = (presets.data ?? []).find((item) => item.id === event.target.value);
                if (preset) applyPreset(preset);
                else setPresetId('');
              }}
              placeholder="No preset"
              options={(presets.data ?? []).map((preset) => ({
                value: preset.id,
                label: preset.name,
              }))}
            />
          </div>
        </Section>

        {kind === 'video' ? (
          <Section title="Video">
            <div className="stack stack--tight">
              <Select
                label="Duration"
                value={duration === null ? '' : String(duration)}
                onChange={(event) => setDuration(event.target.value ? Number(event.target.value) : null)}
                placeholder="Model default"
                options={(currentCapabilities?.durations_seconds ?? []).map((seconds) => ({
                  value: String(seconds),
                  label: `${seconds}s`,
                }))}
                disabled={!(currentCapabilities?.durations_seconds ?? []).length}
                hint={
                  (currentCapabilities?.durations_seconds ?? []).length
                    ? undefined
                    : 'The model profile declares no durations, so this control is disabled.'
                }
              />
              <Select
                label="Aspect ratio"
                value={aspectRatio}
                onChange={(event) => setAspectRatio(event.target.value)}
                placeholder="Model default"
                options={(currentCapabilities?.aspect_ratios ?? []).map((ratio) => ({
                  value: ratio,
                  label: ratio,
                }))}
                disabled={!(currentCapabilities?.aspect_ratios ?? []).length}
                hint={
                  (currentCapabilities?.aspect_ratios ?? []).length
                    ? undefined
                    : 'The model profile declares no aspect ratios.'
                }
              />
              <Select
                label="Resolution"
                value={resolution}
                onChange={(event) => setResolution(event.target.value)}
                placeholder="Model default"
                options={(currentCapabilities?.resolutions ?? []).map((value) => ({
                  value,
                  label: value,
                }))}
                disabled={!(currentCapabilities?.resolutions ?? []).length}
                hint={
                  (currentCapabilities?.resolutions ?? []).length
                    ? undefined
                    : 'The model profile declares no resolutions.'
                }
              />

              <Select
                label="Camera move"
                value={cameraMove}
                onChange={(event) => setCameraMove(event.target.value)}
                placeholder="None"
                options={cameraMoves.map((move) => ({
                  value: move.key,
                  label: move.is_native ? `${move.label} (native control)` : `${move.label} (prompt wording only)`,
                }))}
                disabled={!cameraMoves.length}
                hint={
                  cameraMoves.length
                    ? undefined
                    : 'This model profile declares no camera moves, so the control is disabled.'
                }
              />
              {cameraMove ? (
                <CameraMoveNote
                  move={cameraMoves.find((item) => item.key === cameraMove) ?? null}
                />
              ) : null}

              {currentCapabilities?.supports_audio ? (
                <Checkbox
                  label="Generate audio"
                  hint="This model declares audio support."
                  checked={generateAudio}
                  onChange={(event) => setGenerateAudio(event.target.checked)}
                />
              ) : (
                <p className="dim" style={{ margin: 0, fontSize: 'var(--fs-xs)' }}>
                  This model does not declare audio support, so the option is hidden.
                </p>
              )}

              <Select
                label="First frame"
                value={firstFrame}
                onChange={(event) => setFirstFrame(event.target.value)}
                placeholder="No first frame"
                options={(referenceAssets.data ?? []).map((asset) => ({
                  value: asset.id,
                  label: asset.name,
                }))}
                hint="An existing image asset used as the opening frame."
              />
              <Select
                label="Last frame"
                value={lastFrame}
                onChange={(event) => setLastFrame(event.target.value)}
                placeholder="No last frame"
                options={(referenceAssets.data ?? []).map((asset) => ({
                  value: asset.id,
                  label: asset.name,
                }))}
              />
            </div>
          </Section>
        ) : null}

        <Section title="References">
          <p className="muted" style={{ margin: 0 }}>
            Reference images are picked from your asset library. Only real assets the backend can serve appear here.
          </p>
          <div className="stack stack--tight">
            {(referenceAssets.data ?? []).slice(0, 8).map((asset) => {
              const selected = references.some((reference) => reference.asset_id === asset.id);
              const roles = capabilities.data?.reference_roles ?? [];
              return (
                <div key={asset.id} className="row row--between">
                  <span style={{ display: 'flex', alignItems: 'center', gap: 'var(--s2)' }}>
                    <span className="asset-tile" style={{ width: 40, height: 40, display: 'block' }}>
                      <AssetImage src={asset.thumbnail_url ?? asset.content_url} alt={asset.name} />
                    </span>
                    <span className="dim" style={{ fontSize: 'var(--fs-xs)' }}>
                      {truncate(asset.name, 22)}
                    </span>
                  </span>
                  <span className="row row--tight">
                    {roles.length ? (
                      <select
                        className="select"
                        style={{ minWidth: 110 }}
                        aria-label={`Role for ${asset.name}`}
                        value={references.find((item) => item.asset_id === asset.id)?.role ?? ''}
                        onChange={(event) => {
                          const role = event.target.value;
                          setReferences((current) =>
                            role
                              ? [
                                  ...current.filter((item) => item.asset_id !== asset.id),
                                  { asset_id: asset.id, role },
                                ]
                              : current.filter((item) => item.asset_id !== asset.id),
                          );
                        }}
                      >
                        <option value="">Not used</option>
                        {roles.map((role) => (
                          <option key={role.key} value={role.key}>
                            {role.label}
                          </option>
                        ))}
                      </select>
                    ) : (
                      <span className="dim" style={{ fontSize: 'var(--fs-xs)' }}>
                        no roles declared
                      </span>
                    )}
                    <IconButton
                      label={selected ? `Remove ${asset.name}` : `Add ${asset.name}`}
                      onClick={() => {
                        setReferences((current) =>
                          selected
                            ? current.filter((item) => item.asset_id !== asset.id)
                            : [...current, { asset_id: asset.id, role: 'reference' }],
                        );
                      }}
                    >
                      <Icon name={selected ? 'close' : 'plus'} size={13} />
                    </IconButton>
                  </span>
                </div>
              );
            })}
            {!(referenceAssets.data ?? []).length ? (
              <p className="dim" style={{ margin: 0, fontSize: 'var(--fs-xs)' }}>
                No image asset is in the library yet, so there is nothing to reference.
              </p>
            ) : null}
          </div>
        </Section>

        <div className="stack stack--tight">
          <Button variant="primary" block disabled={!canSubmit} onClick={() => void submit()}>
            {submitting ? 'Submitting…' : busy ? 'A job is already running' : `Generate ${kind}`}
          </Button>
          {!canSubmit ? (
            <p className="dim" style={{ margin: 0, fontSize: 'var(--fs-xs)' }}>
              {model === null
                ? 'Choose a model first.'
                : mode === null
                  ? 'Choose a mode the model profile declares.'
                  : prompt.trim().length === 0
                    ? 'Write a prompt before submitting.'
                    : busy
                      ? 'Wait for the running job to finish; the studio will not queue a second request silently.'
                      : 'This request cannot be submitted yet.'}
            </p>
          ) : null}
          <span className="dim" style={{ margin: 0, fontSize: 'var(--fs-xs)' }}>
            Submitting sends the prompt to your own provider connection. Cost is whatever the backend reports on the
            job afterwards — this client never estimates it.
          </span>
        </div>
    </>
  );

  return (
    <div className="page page--wide">
      <div className="page__header">
        <div>
          <h1 className="page__heading">{kind === 'image' ? 'Image Studio' : 'Video Studio'}</h1>
          <p className="page__subtitle">
            {kind === 'image'
              ? 'Write the prompt, pick a registered image model, and submit. Every control below exists because the model profile declares it.'
              : 'The same flow for video. Duration, aspect ratio, resolution and camera move stay disabled unless the model profile declares them.'}
          </p>
        </div>
        <div className="page__actions">
          <Link to="/models" className="btn btn--sm">
            Model registry
          </Link>
          <Link to="/history" className="btn btn--sm">
            History
          </Link>
          <button
            type="button"
            className="btn btn--sm inspector-trigger"
            aria-expanded={inspectorOpen}
            onClick={() => setInspectorOpen(true)}
          >
            <span className="btn__icon">
              <Icon name="sliders" size={13} />
            </span>
            Request settings
          </button>
        </div>
      </div>

      {!eligible.length ? (
        <div className="stack">
          <div className="panel panel--warn">
            <p className="panel__title">
              <Icon name="alert" size={13} /> No {kind} model is registered
            </p>
            <p style={{ margin: 0 }}>
              Register a {kind} model on a connection first. The capability profile you give it decides which controls
              this studio is allowed to enable.
            </p>
          </div>
          <Link to="/connections" className="btn btn--primary">
            Connect a provider
          </Link>
        </div>
      ) : (
        <div className="studio">
          <div className="studio__left">
            <ModelPicker
              models={models.data ?? []}
              value={modelId}
              onChange={setModelId}
              modality={kind}
            />

            {capabilityList.length ? (
              <div className="field">
                <span className="field__label">Mode</span>
                <Segmented
                  label="Generation mode"
                  value={mode}
                  onChange={setMode}
                  options={capabilityList.map((option) => ({
                    value: option.key,
                    label: option.label || option.key,
                    disabled: !option.supported,
                    title: option.supported
                      ? option.label
                      : `The model profile does not declare the "${option.key}" mode.`,
                  }))}
                />
                <p className="field__hint">
                  A mode is selectable only when the backend reports it as supported for this model.
                </p>
              </div>
            ) : null}

            <Section title="Prompt">
              <TextArea
                label="Prompt"
                tall
                value={prompt}
                onChange={(event) => setPrompt(event.target.value)}
                placeholder="Describe the frame you want. Be concrete about subject, light and lens."
                required
              />
              {supportsNegative ? (
                <TextArea
                  label="Negative prompt"
                  value={negativePrompt}
                  onChange={(event) => setNegativePrompt(event.target.value)}
                  hint="This model declares negative-prompt support."
                />
              ) : (
                <p className="dim" style={{ margin: 0, fontSize: 'var(--fs-xs)' }}>
                  This model does not declare negative-prompt support, so the field is hidden rather than sent and
                  ignored.
                </p>
              )}
              <PromptAssistant
                prompt={prompt}
                negativePrompt={negativePrompt}
                modelId={modelId}
                modality={kind}
                kind={kind}
                onUse={(suggestion) => setPrompt(suggestion)}
              />
            </Section>

            <Section title="Parameters">
              {schema.loading ? (
                <p className="muted" style={{ margin: 0 }}>Reading the parameter schema…</p>
              ) : schema.error ? (
                <ErrorNotice error={schema.error} onRetry={schema.reload} />
              ) : (
                <ParameterFields
                  specs={schema.data?.schema.parameters ?? model?.parameter_schema.parameters ?? []}
                  values={parameters}
                  onChange={(name, value) => setParameters((current) => ({ ...current, [name]: value }))}
                />
              )}
            </Section>
          </div>

          <div className="studio__center">
            {submitError ? <ErrorNotice error={submitError} /> : null}
            {watchError ? <ErrorNotice error={watchError} /> : null}

            {job ? (
              <>
                <div className="toolbar">
                  <div className="toolbar__group">
                    <Badge
                      tone={
                        job.state === 'succeeded' ? 'ok' : job.state === 'failed' ? 'danger' : 'accent'
                      }
                    >
                      {job.state}
                    </Badge>
                    <MockBadge isMock={job.is_mock} />
                    <span className="mono dim" style={{ fontSize: 'var(--fs-xs)' }}>
                      {job.id}
                    </span>
                  </div>
                  <div className="toolbar__spacer" />
                  <div className="toolbar__group">
                    <Link to={`/history/${job.id}`} className="btn btn--sm">
                      Open job
                    </Link>
                  </div>
                </div>
                {busy ? (
                  <ProgressBar value={job.progress} isEstimate={job.progress_is_estimate} label={`Job ${job.state}`} />
                ) : null}
                {job.error ? (
                  <div className="panel panel--danger" role="alert">
                    <p className="panel__title mono">{job.error.code}</p>
                    <p style={{ margin: 0 }}>{job.error.message}</p>
                    {job.error.hint ? <p className="muted" style={{ margin: 'var(--s1) 0 0' }}>{job.error.hint}</p> : null}
                  </div>
                ) : null}
                <MediaCanvas
                  item={current}
                  emptyLabel={busy ? 'Waiting for the provider' : 'The job returned no output'}
                  emptyHint={
                    busy
                      ? 'The canvas fills when the backend reports an output URL. Nothing is drawn before that.'
                      : 'No output URL came back for this job, so there is nothing to show.'
                  }
                />
                {outputs.length > 1 ? (
                  <div className="asset-grid">
                    {outputs.map((item) => (
                      <div key={item.id} className="asset-tile">
                        <div className="asset-tile__media">
                          {item.url ? <AssetImage src={item.url} alt={item.label} /> : null}
                        </div>
                        <div className="asset-tile__body">
                          <span className="asset-tile__name">{item.label}</span>
                          <span className="asset-tile__meta">{item.meta}</span>
                          <MockBadge isMock={item.isMock} />
                        </div>
                      </div>
                    ))}
                  </div>
                ) : null}
                {job.cost_estimate ? (
                  <p className="muted" style={{ margin: 0 }}>
                    {formatCost(job.cost_estimate)}
                    {job.cost_estimate.is_billable === false
                      ? ' · not billed by this platform — your provider account is charged.'
                      : ''}
                  </p>
                ) : (
                  <p className="dim" style={{ margin: 0, fontSize: 'var(--fs-xs)' }}>
                    The backend returned no cost information for this job.
                  </p>
                )}
              </>
            ) : (
              <MediaCanvas
                item={null}
                emptyLabel="Nothing generated yet"
                emptyHint="Submit a prompt and the canvas shows only what the backend actually returns."
              />
            )}
          </div>

          <div className="studio__right">{inspector}</div>
        </div>
      )}

      <Drawer open={inspectorOpen} onClose={() => setInspectorOpen(false)} title="Request settings">
        {inspector}
      </Drawer>
    </div>
  );
}

/**
 * A camera move is only described as camera control when the backend says the
 * model has native support. Otherwise the UI says exactly what it is: prompt
 * wording.
 */
function CameraMoveNote({ move }: { move: { label: string; phrase: string; is_native: boolean } | null }) {
  if (!move) return null;
  return (
    <div className={move.is_native ? 'panel panel--info' : 'panel panel--warn'}>
      <p className="panel__title">
        {move.is_native ? 'Native camera control' : 'Prompt wording, not camera control'}
      </p>
      <p style={{ margin: 0 }}>
        {move.is_native
          ? `The backend declares native support for "${move.label}" on this model.`
          : `This model has no native support for "${move.label}". The phrase below is added to the prompt as wording, which the model may ignore.`}
      </p>
      {move.phrase ? <p className="mono" style={{ margin: 'var(--s2) 0 0' }}>{move.phrase}</p> : null}
    </div>
  );
}

/**
 * The prompt assistant.
 *
 * The server answers with a deterministic rewrite and a list of exactly what it
 * changed. Nothing is sent to a model, so the panel says so: the badge reads
 * "local rules", never an AI claim. The suggestion is shown next to the original
 * and is applied only when the author presses the button — a rewrite never lands
 * in the prompt field on its own.
 */
function PromptAssistant({
  prompt,
  negativePrompt,
  modelId,
  modality,
  kind,
  onUse,
}: {
  prompt: string;
  negativePrompt: string;
  modelId: string | null;
  modality: string;
  kind: StudioKind;
  onUse: (suggestion: string) => void;
}) {
  const [suggestion, setSuggestion] = useState<string | null>(null);
  const [notes, setNotes] = useState<string[]>([]);
  const [provider, setProvider] = useState<string>('local-rules');
  const [operation, setOperation] = useState<'structure' | 'expand' | 'condense' | 'to_video'>('structure');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const ask = async (next: 'structure' | 'expand' | 'condense' | 'to_video' = operation) => {
    setOperation(next);
    setPending(true);
    setError(null);
    try {
      const result = await assistant.improve({
        prompt,
        negative_prompt: negativePrompt || undefined,
        model_id: modelId ?? undefined,
        modality,
        operation: next,
      });
      setSuggestion(result.suggestion);
      setNotes(result.notes ?? []);
      setProvider(result.provider ?? 'local-rules');
    } catch (err) {
      setError(err);
      setSuggestion(null);
      setNotes([]);
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="panel panel--info">
      <p className="panel__title">
        <Icon name="wand" size={13} /> Prompt assistant
      </p>
      <p style={{ margin: 0 }}>
        Reorganises your words using fixed craft vocabulary. It runs on this server, sends your prompt to no
        provider, and lists every change it made — so nothing appears in your prompt that you did not see first.
      </p>
      <div className="row row--tight" style={{ marginTop: 'var(--s2)' }}>
        <Button size="sm" onClick={() => void ask('structure')} disabled={pending || prompt.trim().length === 0}>
          {pending ? 'Working…' : 'Structure it'}
        </Button>
        <Button size="sm" onClick={() => void ask('expand')} disabled={pending || prompt.trim().length === 0}>
          Add craft detail
        </Button>
        <Button size="sm" onClick={() => void ask('condense')} disabled={pending || prompt.trim().length === 0}>
          Condense
        </Button>
        {kind === 'video' ? (
          <Button size="sm" onClick={() => void ask('to_video')} disabled={pending || prompt.trim().length === 0}>
            As a shot
          </Button>
        ) : null}
      </div>
      {prompt.trim().length === 0 ? (
        <span className="dim" style={{ fontSize: 'var(--fs-xs)' }}>Write a prompt first.</span>
      ) : null}
      {error ? <div style={{ marginTop: 'var(--s2)' }}><ErrorNotice error={error} /></div> : null}
        {suggestion ? (
          <div className="stack stack--tight" style={{ marginTop: 'var(--s2)' }}>
            <Badge tone="plain">
              {provider === 'local-rules' ? 'local rules - no model called' : provider}
            </Badge>
            <p className="dim" style={{ margin: 0, fontSize: 'var(--fs-xs)' }}>
              Preview only. Your prompt is unchanged until you press Use this prompt.
            </p>
            <p style={{ margin: 0, whiteSpace: 'pre-wrap' }}>{suggestion}</p>
            {notes.length ? (
              <ul className="dim" style={{ margin: 0, paddingLeft: '1.2em', fontSize: 'var(--fs-xs)' }}>
                {notes.map((note) => (
                  <li key={note}>{note}</li>
                ))}
              </ul>
            ) : null}
            <div className="row row--tight">
              <Button size="sm" onClick={() => onUse(suggestion)}>
                Use this prompt
              </Button>
              <Button
                size="sm"
                onClick={() => {
                  setSuggestion(null);
                  setNotes([]);
                }}
              >
                Discard
              </Button>
            </div>
          </div>
        ) : null}
    </div>
  );
}