import { useEffect, useMemo, useState } from 'react';
import { connections as connectionsApi } from '../../api/endpoints.ts';
import type {
  AdapterType,
  Capabilities,
  Connection,
  ConnectionTestResult,
  Modality,
  Model,
  ModelInput,
  ParameterSpec,
  PromptCatalogEntry,
} from '../../api/types.ts';
import { Badge, MockBadge } from '../../components/Badge.tsx';
import { Button } from '../../components/Button.tsx';
import { CodeBlock, KeyValueList, Section } from '../../components/Data.tsx';
import { Checkbox, Select, TextArea, TextInput } from '../../components/Field.tsx';
import { ErrorNotice } from '../../components/ErrorNotice.tsx';
import { Icon } from '../../components/Icon.tsx';
import { TagInput } from '../../components/Controls.tsx';
import { invalidate } from '../../state/query.ts';
import { useModeVocabulary, usePromptCatalog } from '../../state/promptLayer.ts';
import { useToasts } from '../../state/toasts.tsx';
import { formatDurationMs, formatDateTime } from '../../lib/util.ts';

/**
 * The five-step connection wizard. The API key is typed into one password field,
 * sent exactly once in the create (or rotate) request, and cleared from React
 * state the moment the backend answers. No read path ever puts it back on
 * screen; only the masked summary the backend returns is displayed.
 */
const STEPS = [
  { id: 'adapter', label: 'Adapter' },
  { id: 'endpoint', label: 'Endpoint and key' },
  { id: 'review', label: 'Review and create' },
  { id: 'test', label: 'Test the connection' },
  { id: 'model', label: 'Register a model' },
] as const;

type StepId = (typeof STEPS)[number]['id'];

export function ConnectionWizard({
  adapters,
  onDone,
  onCancel,
}: {
  adapters: AdapterType[];
  onDone: (connection: Connection) => void;
  onCancel: () => void;
}) {
  const toasts = useToasts();
  const [step, setStep] = useState<StepId>('adapter');

  const [adapterType, setAdapterType] = useState<string>('');
  const [name, setName] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [authScheme, setAuthScheme] = useState('');
  const [authHeader, setAuthHeader] = useState('');
  const [credential, setCredential] = useState('');
  const [allowedHosts, setAllowedHosts] = useState<string[]>([]);
  const [extraHeaders, setExtraHeaders] = useState<{ key: string; value: string }[]>([]);

  const [created, setCreated] = useState<Connection | null>(null);
  const [testResult, setTestResult] = useState<ConnectionTestResult | null>(null);
  const [testError, setTestError] = useState<unknown>(null);
  const [testing, setTesting] = useState(false);

  const [modelKey, setModelKey] = useState('');
  const [modelName, setModelName] = useState('');
  const [modality, setModality] = useState<Modality>('image');
  // Empty until the adapter's own default arrives: hardcoding a default mode
  // here was how three names that the service does not accept got into the UI.
  const [modes, setModes] = useState<string[]>([]);
  const [capabilities, setCapabilities] = useState<Partial<Capabilities>>({});
  const [schemaJson, setSchemaJson] = useState('{\n  "parameters": []\n}');
  const [registered, setRegistered] = useState<Model[]>([]);

  const [error, setError] = useState<unknown>(null);
  const [pending, setPending] = useState(false);

  const adapter = useMemo(() => adapters.find((item) => item.slug === adapterType) ?? null, [adapters, adapterType]);

  // The mode vocabulary and the documented model catalogue both come from the
  // backend. The wizard renders what the service validates instead of keeping a
  // second copy of the list, which is what let `generate`, `edit` and `transform`
  // sit in this form while the service refused every one of them.
  const vocabulary = useModeVocabulary();
  const catalog = usePromptCatalog();
  const modeOptions = useMemo(
    () => (modality === 'video' ? vocabulary.data?.video ?? [] : vocabulary.data?.image ?? []),
    [modality, vocabulary.data],
  );

  // Deliberately not filtered by the current modality: picking a video endpoint
  // is how the user tells the form it is a video model, so hiding those rows
  // until the modality is switched would be a trap. The label carries the kind.
  const documentedModels = useMemo(() => catalog.data?.endpoints ?? [], [catalog.data]);

  /**
   * Fill the form from the documented catalogue: the endpoint id, what it can
   * do, and the controls it documents. This is the "bring your own key" path --
   * the user supplies the key and picks a model by name, not by remembering an
   * endpoint identifier.
   */
  const applyCatalogEntry = (entry: PromptCatalogEntry) => {
    setModelKey(entry.endpoint);
    setModelName(entry.display);
    setModality(entry.modality === 'video' ? 'video' : 'image');
    setModes(entry.modes);
    setCapabilities({
      aspect_ratios: entry.aspect_ratios,
      resolutions: entry.resolutions,
      durations_seconds: entry.durations_seconds,
      supports_negative_prompt: entry.supports_negative_prompt,
      supports_seed: entry.supports_seed,
      max_outputs: Math.max(1, entry.max_outputs),
    });
  };

  useEffect(() => {
    if (!adapter) return;
    setAuthScheme(adapter.auth_schemes?.[0] ?? '');
    setModes((current) => (current.length ? current : adapter.default_modes ?? []));
    // A provider that publishes a base URL fills it in: asking somebody to
    // remember which host to type is how a BYOK form turns into a support
    // ticket. A URL the user has already typed is never overwritten.
    const documented = (adapter.default_base_url ?? '').trim();
    if (documented) setBaseUrl((current) => (current.trim() ? current : documented));
  }, [adapter?.slug]);

  const stepIndex = STEPS.findIndex((entry) => entry.id === step);

  const goNext = () => {
    const next = STEPS[stepIndex + 1];
    if (next) setStep(next.id);
  };
  const goBack = () => {
    const previous = STEPS[stepIndex - 1];
    if (previous) setStep(previous.id);
  };

  const createConnection = async () => {
    setPending(true);
    setError(null);
    try {
      const payload = {
        name: name.trim(),
        adapter_type: adapterType,
        base_url: baseUrl.trim(),
        credential,
        auth_scheme: authScheme || null,
        auth_header_name: authHeader.trim() || null,
        allowed_hosts: allowedHosts.length ? allowedHosts : undefined,
        extra_headers: Object.fromEntries(
          extraHeaders.filter((entry) => entry.key.trim() && entry.value.trim()).map((entry) => [entry.key.trim(), entry.value.trim()]),
        ),
      };
      const result = await connectionsApi.create(payload);
      // Drop the key from memory as soon as the server has it.
      setCredential('');
      setExtraHeaders((current) => current.map((entry) => ({ key: entry.key, value: '' })));
      setCreated(result.connection);
      invalidate('connections');
      invalidate('models');
      toasts.push({
        tone: 'success',
        title: 'Connection created',
        text: result.models.length
          ? `${result.models.length} model${result.models.length === 1 ? '' : 's'} were registered with it.`
          : 'No model was registered yet.',
      });
      goNext();
    } catch (err) {
      setError(err);
    } finally {
      setPending(false);
    }
  };

  const runTest = async () => {
    if (!created) return;
    setTesting(true);
    setTestError(null);
    try {
      const result = await connectionsApi.test(created.id);
      setTestResult(result);
      invalidate('connections');
    } catch (err) {
      setTestError(err);
    } finally {
      setTesting(false);
    }
  };

  const registerModel = async () => {
    if (!created) return;
    setPending(true);
    setError(null);
    try {
      let schema: { parameters: ParameterSpec[] } = { parameters: [] };
      if (schemaJson.trim()) {
        const parsed: unknown = JSON.parse(schemaJson);
        if (typeof parsed !== 'object' || parsed === null || !Array.isArray((parsed as { parameters?: unknown }).parameters)) {
          throw new Error('The parameter schema must be an object with a "parameters" array.');
        }
        schema = parsed as { parameters: ParameterSpec[] };
      }
      const input: ModelInput = {
        model_key: modelKey.trim(),
        display_name: modelName.trim() || modelKey.trim(),
        modality,
        modes,
        capabilities,
        parameter_schema: schema,
        source: 'user',
      };
      const model = await connectionsApi.addModel(created.id, input);
      setRegistered((current) => [...current, model]);
      invalidate('models');
      invalidate('connections');
      toasts.push({ tone: 'success', title: 'Model registered', text: model.display_name });
      setModelKey('');
      setModelName('');
    } catch (err) {
      setError(err);
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="stack">
      <ol className="steps">
        {STEPS.map((entry, index) => (
          <li
            key={entry.id}
            className="step"
            data-state={entry.id === step ? 'current' : index < stepIndex ? 'done' : undefined}
          >
            <span className="step__index" aria-hidden="true">
              {index < stepIndex ? <Icon name="check" size={12} /> : index + 1}
            </span>
            <span>{entry.label}</span>
          </li>
        ))}
      </ol>

      {error ? <ErrorNotice error={error} /> : null}

      {step === 'adapter' ? (
        <Section title="1 · Choose an adapter">
          <p className="muted" style={{ margin: 0 }}>
            The adapter decides how the base URL, the key and the model list are handled. A placeholder adapter is
            labelled as such — it never reaches a real provider.
          </p>
          <div className="grid grid--2">
            {adapters.map((item) => (
              <button
                key={item.slug}
                type="button"
                className="card"
                aria-pressed={adapterType === item.slug}
                style={{ borderColor: adapterType === item.slug ? 'var(--accent)' : undefined, textAlign: 'left' }}
                onClick={() => setAdapterType(item.slug)}
              >
                <div className="card__head">
                  <span className="card__title">{item.label}</span>
                  <MockBadge isMock={item.is_mock} />
                </div>
                <p className="muted" style={{ margin: 'var(--s2) 0 0' }}>
                  {item.summary}
                </p>
                <div className="row row--tight" style={{ marginTop: 'var(--s2)', flexWrap: 'wrap' }}>
                  <Badge tone={item.discovers_models ? 'ok' : 'plain'}>
                    {item.discovers_models ? 'discovers models' : 'manual model entry'}
                  </Badge>
                  {item.supports_cancel ? <Badge tone="plain">cancellable jobs</Badge> : null}
                  {(item.auth_schemes ?? []).map((scheme) => (
                    <Badge key={scheme} tone="plain">
                      {scheme}
                    </Badge>
                  ))}
                </div>
                <p className="mono dim" style={{ margin: 'var(--s2) 0 0', fontSize: 'var(--fs-xs)' }}>
                  {item.slug}
                </p>
              </button>
            ))}
          </div>
          {!adapters.length ? (
            <p className="muted" style={{ margin: 0 }}>
              The backend reported no adapter types. A connection cannot be created until it does.
            </p>
          ) : null}
        </Section>
      ) : null}

      {step === 'endpoint' ? (
        <Section title="2 · Endpoint and key">
          <div className="grid grid--2">
            <div className="stack stack--tight">
              <TextInput
                label="Connection name"
                value={name}
                onChange={(event) => setName(event.target.value)}
                hint="How this connection appears in the model picker."
                required
              />
              <TextInput
                label="API base URL"
                type="url"
                value={baseUrl}
                onChange={(event) => setBaseUrl(event.target.value)}
                placeholder={adapter?.default_base_url || 'https://api.example.com/v1'}
                hint={
                  <>
                    {adapter?.base_url_hint || 'The root the adapter appends its paths to.'}
                    {adapter?.docs_url ? (
                      <>
                        {' '}
                        <a href={adapter.docs_url} target="_blank" rel="noreferrer noopener">
                          Provider documentation
                        </a>
                      </>
                    ) : null}
                  </>
                }
                required
              />
              {(adapter?.auth_schemes ?? []).length ? (
                <Select
                  label="Auth scheme"
                  value={authScheme}
                  onChange={(event) => setAuthScheme(event.target.value)}
                  options={(adapter?.auth_schemes ?? []).map((scheme) => ({ value: scheme, label: scheme }))}
                />
              ) : (
                <TextInput
                  label="Auth scheme"
                  value={authScheme}
                  onChange={(event) => setAuthScheme(event.target.value)}
                  hint="Free-form; the backend validates it against the adapter."
                />
              )}
              <TextInput
                label="API key"
                type="password"
                autoComplete="off"
                value={credential}
                onChange={(event) => setCredential(event.target.value)}
                hint={
                  adapter?.credential_hint
                    ? `${adapter.credential_hint} It is sent once to the backend and stored sealed; the client never shows it again, never logs it and never writes it to storage.`
                    : 'Sent once to the backend and stored server-side. The client never shows it again, never logs it and never writes it to storage.'
                }
                required
              />
              <TextInput
                label="Custom auth header"
                value={authHeader}
                onChange={(event) => setAuthHeader(event.target.value)}
                hint="Only needed when the provider does not use the adapter's default header."
              />
            </div>

            <div className="stack stack--tight">
              <TagInput
                label="Allowed hosts"
                values={allowedHosts}
                onChange={setAllowedHosts}
                placeholder="api.example.com, then press Enter"
                hint="Optional allow-list the backend enforces for this connection."
              />
              <div className="field">
                <span className="field__label">Extra headers</span>
                <p className="field__hint" style={{ marginTop: 0 }}>
                  Non-secret headers only, for example an organisation id. The key never goes here.
                </p>
                {extraHeaders.map((entry, index) => (
                  <div className="row row--tight" key={index}>
                    <input
                      className="input"
                      placeholder="header name"
                      aria-label={`Extra header ${index + 1} name`}
                      value={entry.key}
                      onChange={(event) => {
                        const next = [...extraHeaders];
                        next[index] = { ...next[index], key: event.target.value };
                        setExtraHeaders(next);
                      }}
                    />
                    <input
                      className="input"
                      placeholder="value"
                      aria-label={`Extra header ${index + 1} value`}
                      value={entry.value}
                      onChange={(event) => {
                        const next = [...extraHeaders];
                        next[index] = { ...next[index], value: event.target.value };
                        setExtraHeaders(next);
                      }}
                    />
                    <Button
                      size="sm"
                      onClick={() => setExtraHeaders(extraHeaders.filter((_, position) => position !== index))}
                    >
                      Remove
                    </Button>
                  </div>
                ))}
                <Button size="sm" onClick={() => setExtraHeaders([...extraHeaders, { key: '', value: '' }])}>
                  Add header
                </Button>
              </div>
            </div>
          </div>
        </Section>
      ) : null}

      {step === 'review' ? (
        <Section title="3 · Review and create">
          <div className="grid grid--2">
            <KeyValueList
              rows={[
                { key: 'Adapter', value: adapter?.label ?? adapterType },
                { key: 'Connection name', value: name || 'not set' },
                { key: 'Base URL', value: <span className="mono">{baseUrl || 'not set'}</span> },
                { key: 'Auth scheme', value: authScheme || 'adapter default' },
                { key: 'Custom header', value: authHeader || 'not set' },
                { key: 'Allowed hosts', value: allowedHosts.join(', ') || 'no allow-list' },
                { key: 'Extra headers', value: extraHeaders.filter((entry) => entry.key).map((entry) => entry.key).join(', ') || 'none' },
                { key: 'API key', value: credential ? 'will be sent once, then dropped from the form' : 'not entered' },
              ]}
            />
            <div className="panel panel--info">
              <p className="panel__title">What happens when you press create</p>
              <p style={{ margin: 0 }}>
                The client POSTs the base URL, the key and the headers once. The backend stores the key and returns a
                masked summary — never the key itself. This form clears the key field afterwards.
              </p>
              {adapter?.is_mock ? (
                <div style={{ marginTop: 'var(--s2)' }}>
                  <Badge tone="mock">Local development placeholder</Badge>
                  <p className="muted" style={{ margin: 'var(--s2) 0 0' }}>
                    This adapter is the local placeholder provider. Anything it generates is badged as a placeholder on
                    every surface.
                  </p>
                </div>
              ) : null}
            </div>
          </div>
        </Section>
      ) : null}

      {step === 'test' ? (
        <Section title="4 · Test the connection">
          {!created ? (
            <p className="muted" style={{ margin: 0 }}>
              Create the connection first; a test needs an id.
            </p>
          ) : (
            <div className="stack stack--tight">
              <KeyValueList
                rows={[
                  { key: 'Connection', value: created.name },
                  { key: 'Credential', value: created.credential.present ? `on file ••••${created.credential.last4 ?? ''}` : 'not present' },
                  { key: 'Key id', value: created.credential.key_id ?? 'not reported' },
                  { key: 'Fingerprint', value: created.credential.fingerprint ?? 'not reported' },
                ]}
              />
              <div className="row row--tight">
                <Button variant="primary" onClick={() => void runTest()} disabled={testing}>
                  {testing ? 'Testing…' : 'Test the connection'}
                </Button>
                <span className="dim" style={{ fontSize: 'var(--fs-xs)' }}>
                  The backend calls the provider and reports exactly what it saw.
                </span>
              </div>
              {testError ? <ErrorNotice error={testError} onRetry={() => void runTest()} /> : null}
              {testResult ? (
                <div className={testResult.ok ? 'panel panel--info' : 'panel panel--danger'}>
                  <p className="panel__title">
                    <Badge tone={testResult.ok ? 'ok' : 'danger'}>{testResult.outcome}</Badge>
                  </p>
                  <p style={{ margin: 0 }}>{testResult.message}</p>
                  <p className="muted" style={{ margin: 'var(--s2) 0 0' }}>
                    HTTP {testResult.http_status ?? '—'} · latency {formatDurationMs(testResult.latency_ms)} ·{' '}
                    {testResult.models_seen ?? 0} models seen · checked {formatDateTime(testResult.checked_at)}
                  </p>
                  {testResult.detail ? (
                    <div style={{ marginTop: 'var(--s2)' }}>
                      <CodeBlock code={JSON.stringify(testResult.detail, null, 2)} label="detail" />
                    </div>
                  ) : null}
                </div>
              ) : null}
            </div>
          )}
        </Section>
      ) : null}

      {step === 'model' ? (
        <Section title="5 · Register a model">
          <p className="muted" style={{ margin: 0 }}>
            A model is only offered in a studio when it is registered here with an explicit profile. Everything ticked
            below becomes something the studio is allowed to enable.
          </p>

          <div className="panel">
            <p className="panel__title">Start from a documented model</p>
            <p className="muted" style={{ margin: 0 }}>
              These endpoints are described by documents in this repository: the identifier the provider expects, the
              modes it really serves, and the controls it really has. Picking one fills the form. A model that is not
              listed can still be registered by hand below — it simply has no facts on record, and the studio will say
              so rather than assume.
            </p>
            {catalog.loading ? <p className="muted" style={{ margin: 0 }}>Reading the catalogue…</p> : null}
            {catalog.error ? <ErrorNotice error={catalog.error} onRetry={catalog.reload} /> : null}
            {catalog.data ? (
              <>
                <Select
                  label="Documented endpoint"
                  value={modelKey}
                  onChange={(event) => {
                    const entry = documentedModels.find((item) => item.endpoint === event.target.value);
                    if (entry) applyCatalogEntry(entry);
                    else setModelKey(event.target.value);
                  }}
                  placeholder="Choose a documented endpoint…"
                  options={documentedModels.map((entry) => ({
                    value: entry.endpoint,
                    label: `${entry.display} · ${entry.modality} — ${entry.prompt_model ? `${entry.prompt_model.active_layer_count} prompt layer${entry.prompt_model.active_layer_count === 1 ? '' : 's'}` : 'no layer facts'}`,
                  }))}
                  hint={`${catalog.data.documented} of ${catalog.data.endpoints.length} endpoints have prompt-layer facts. Source: ${catalog.data.source}`}
                />
                {modelKey && !documentedModels.some((entry) => entry.endpoint === modelKey) ? (
                  <p className="field__hint" style={{ margin: 0 }}>
                    <span className="mono">{modelKey}</span> is not in the documented catalogue. That is allowed; the
                    provider will be asked for its own schema, and the Prompt Lab will report the model as undocumented.
                  </p>
                ) : null}
              </>
            ) : null}
          </div>

          <div className="grid grid--2">
            <div className="stack stack--tight">
              <TextInput
                label="Model key"
                value={modelKey}
                onChange={(event) => setModelKey(event.target.value)}
                placeholder="provider-model-id"
                hint="The identifier the provider expects in the request body."
                required
              />
              <TextInput
                label="Display name"
                value={modelName}
                onChange={(event) => setModelName(event.target.value)}
                placeholder="Friendly name shown in the studio"
              />
              <Select
                label="Modality"
                value={modality}
                onChange={(event) => setModality(event.target.value as Modality)}
                options={[
                  { value: 'image', label: 'Image' },
                  { value: 'video', label: 'Video' },
                ]}
              />
              <div className="field">
                <span className="field__label">Modes</span>
                <p className="field__hint" style={{ margin: 0 }}>
                  The vocabulary the service validates against, read from the backend. A mode this provider does not
                  document is not offered, and a mode you do not declare becomes a disabled control in the studio
                  rather than a failed job.
                </p>
                <div className="stack stack--tight">
                  {vocabulary.loading ? <p className="muted" style={{ margin: 0 }}>Reading the vocabulary…</p> : null}
                  {vocabulary.error ? <ErrorNotice error={vocabulary.error} onRetry={vocabulary.reload} /> : null}
                  {modeOptions.map((mode) => (
                    <Checkbox
                      key={mode}
                      label={vocabulary.data?.labels[mode] ?? mode}
                      hint={<span className="mono">{mode}</span>}
                      checked={modes.includes(mode)}
                      onChange={(event) => {
                        setModes((current) =>
                          event.target.checked ? [...current, mode] : current.filter((item) => item !== mode),
                        );
                      }}
                    />
                  ))}
                </div>
              </div>
            </div>

            <div className="stack stack--tight">
              <TagInput
                label="Aspect ratios"
                values={capabilities.aspect_ratios ?? []}
                onChange={(next) => setCapabilities((current) => ({ ...current, aspect_ratios: next }))}
              />
              <TagInput
                label="Resolutions"
                values={capabilities.resolutions ?? []}
                onChange={(next) => setCapabilities((current) => ({ ...current, resolutions: next }))}
              />
              <div className="field">
                <span className="field__label">Durations in seconds</span>
                <input
                  className="input"
                  value={(capabilities.durations_seconds ?? []).join(', ')}
                  aria-label="Durations in seconds"
                  placeholder="4, 6, 8"
                  onChange={(event) => {
                    const parsed = event.target.value
                      .split(',')
                      .map((part) => Number(part.trim()))
                      .filter((value) => Number.isFinite(value) && value > 0);
                    setCapabilities((current) => ({ ...current, durations_seconds: parsed }));
                  }}
                />
                <p className="field__hint">Comma separated. An empty list disables the duration control.</p>
              </div>
              <div className="stack stack--tight">
                <Checkbox
                  label="Supports negative prompt"
                  checked={capabilities.supports_negative_prompt === true}
                  onChange={(event) =>
                    setCapabilities((current) => ({ ...current, supports_negative_prompt: event.target.checked }))
                  }
                />
                <Checkbox
                  label="Supports seed"
                  checked={capabilities.supports_seed === true}
                  onChange={(event) => setCapabilities((current) => ({ ...current, supports_seed: event.target.checked }))}
                />
                <Checkbox
                  label="Supports mask"
                  checked={capabilities.supports_mask === true}
                  onChange={(event) => setCapabilities((current) => ({ ...current, supports_mask: event.target.checked }))}
                />
                <Checkbox
                  label="Supports audio"
                  checked={capabilities.supports_audio === true}
                  onChange={(event) => setCapabilities((current) => ({ ...current, supports_audio: event.target.checked }))}
                />
              </div>
              <TextArea
                label="Parameter schema (JSON)"
                value={schemaJson}
                onChange={(event) => setSchemaJson(event.target.value)}
                hint="Shape: {&quot;parameters&quot;:[{name,type,label,default,minimum,maximum,step,options}]}. The studios render exactly these."
              />
            </div>
          </div>

          <div className="row row--tight">
            <Button
              variant="primary"
              onClick={() => void registerModel()}
              disabled={!modelKey.trim() || !modes.length || pending || !created}
            >
              {pending ? 'Registering…' : 'Register this model'}
            </Button>
            <span className="dim" style={{ fontSize: 'var(--fs-xs)' }}>
              {!modes.length ? 'Pick at least one mode.' : 'A model with no declared mode cannot be selected in a studio.'}
            </span>
          </div>

          {registered.length ? (
            <div className="stack stack--tight">
              <span className="field__label">Registered in this session</span>
              {registered.map((model) => (
                <div className="row row--between" key={model.id}>
                  <span>
                    {model.display_name} <span className="mono dim">{model.model_key}</span>
                  </span>
                  <Badge tone="ok">{model.modes.join(', ')}</Badge>
                </div>
              ))}
            </div>
          ) : null}
        </Section>
      ) : null}

      <div className="row row--between">
        <Button onClick={onCancel}>Cancel the wizard</Button>
        <span className="row row--tight">
          <Button onClick={goBack} disabled={stepIndex === 0}>
            Back
          </Button>
          {step === 'review' ? (
            <Button
              variant="primary"
              onClick={() => void createConnection()}
              disabled={!adapterType || !name.trim() || !baseUrl.trim() || !credential || pending}
            >
              {pending ? 'Creating…' : 'Create the connection'}
            </Button>
          ) : step === 'model' ? (
            <Button
              variant="primary"
              onClick={() => {
                if (created) onDone(created);
              }}
            >
              Finish
            </Button>
          ) : (
            <Button
              variant="primary"
              onClick={goNext}
              disabled={step === 'adapter' ? !adapterType : step === 'endpoint' ? !name.trim() || !baseUrl.trim() || !credential : !created}
            >
              Continue
            </Button>
          )}
        </span>
      </div>

      {!created && stepIndex > 2 ? (
        <p className="dim" style={{ margin: 0, fontSize: 'var(--fs-xs)' }}>
          The connection has not been created yet, so the test and the model registration are unavailable.
        </p>
      ) : null}
    </div>
  );
}
