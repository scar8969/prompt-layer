import { useMemo, useState } from 'react';
import { onboarding } from '../../api/endpoints.ts';
import type { DiscoveredModel, DiscoveryPlan } from '../../api/types.ts';
import { Badge } from '../../components/Badge.tsx';
import { Button } from '../../components/Button.tsx';
import { Checkbox, TextInput } from '../../components/Field.tsx';
import { ErrorNotice } from '../../components/ErrorNotice.tsx';
import { Icon } from '../../components/Icon.tsx';
import { LoadingState } from '../../components/State.tsx';
import { useRouter } from '../../app/router.tsx';
import { invalidate } from '../../state/query.ts';
import { useToasts } from '../../state/toasts.tsx';

const FAMILY_LABEL: Record<string, string> = {
  chat: 'Chat',
  image: 'Image',
  video: 'Video',
  audio: 'Audio',
  embedding: 'Embedding',
  rerank: 'Rerank',
  moderation: 'Moderation',
  unknown: 'Unclassified',
};

const FAMILY_ORDER = ['chat', 'image', 'video', 'audio', 'embedding', 'rerank', 'moderation', 'unknown'];

/**
 * One key, one URL, every model the endpoint actually serves.
 *
 * The two steps are deliberately separate. "Test endpoint" reads the provider's
 * own model list and sends a one-token turn to each conversation model; it writes
 * nothing. "Register" stores the models the user ticked. The screen never says a
 * model works because it was listed — a model is marked available only when the
 * provider answered — and it does not claim that images or video were probed.
 */
export function OnboardScreen() {
  const toasts = useToasts();
  const { navigate } = useRouter();
  const [name, setName] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [allowedHosts, setAllowedHosts] = useState('');
  const [probeChat, setProbeChat] = useState(true);
  const [plan, setPlan] = useState<DiscoveryPlan | null>(null);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [discovering, setDiscovering] = useState(false);
  const [registering, setRegistering] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const drivable = useMemo(() => plan?.models.filter((item) => item.drivable) ?? [], [plan]);
  const grouped = useMemo(() => {
    const out = new Map<string, DiscoveredModel[]>();
    for (const item of plan?.models ?? []) {
      const family = FAMILY_ORDER.includes(item.family) ? item.family : 'unknown';
      out.set(family, [...(out.get(family) ?? []), item]);
    }
    return FAMILY_ORDER.filter((family) => out.has(family)).map((family) => ({
      family,
      models: out.get(family) ?? [],
    }));
  }, [plan]);

  async function discover() {
    setError(null);
    setDiscovering(true);
    setPlan(null);
    try {
      const result = await onboarding.discover({
        name: name.trim() || 'Discovered provider',
        base_url: baseUrl.trim(),
        api_key: apiKey,
        probe_chat: probeChat,
        allowed_hosts: allowedHosts
          .split(',')
          .map((item) => item.trim())
          .filter(Boolean),
      });
      setPlan(result);
      setSelected(new Set(result.models.filter((item) => item.drivable).map((item) => item.model_key)));
      toasts.push({
        tone: 'success',
        title: 'Endpoint answered',
        text: `${result.models.length} model${result.models.length === 1 ? '' : 's'} listed. Nothing has been saved or spent.`,
      });
    } catch (problem) {
      setError(problem);
    } finally {
      setDiscovering(false);
    }
  }

  async function register() {
    if (!plan) return;
    const chosen = plan.models
      .filter((item) => selected.has(item.model_key) && item.drivable)
      .map((item) => ({ model_key: item.model_key, family: item.family, display_name: item.display_name }));
    if (!chosen.length) return;
    setError(null);
    setRegistering(true);
    try {
      const result = await onboarding.register({
        name: plan.provider.name || name.trim() || 'Discovered provider',
        base_url: plan.provider.base_url || baseUrl.trim(),
        api_key: apiKey,
        models: chosen,
        default_chat: plan.default_chat,
        allowed_hosts: allowedHosts
          .split(',')
          .map((item) => item.trim())
          .filter(Boolean),
      });
      invalidate('connections');
      invalidate('models');
      toasts.push({
        tone: 'success',
        title: 'Provider registered',
        text: `${result.models.length} model${result.models.length === 1 ? '' : 's'} in ${result.connections.length} connection${
          result.connections.length === 1 ? '' : 's'
        }. Nothing was generated.`,
      });
      navigate(result.default_chat_model_id ? '/chat' : '/models');
    } catch (problem) {
      setError(problem);
    } finally {
      setRegistering(false);
    }
  }

  const chosenCount = drivable.filter((item) => selected.has(item.model_key)).length;

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__heading">Add a provider from one key</h1>
          <p className="page__subtitle">
            Paste the API base URL and one key. The backend reads the endpoint's own model list, classifies every entry,
            and asks each conversation model to answer one token. Then you choose what to register.
          </p>
        </div>
      </div>

      <div className="panel">
        <p className="panel__title">
          <Icon name="key" size={13} /> Endpoint and key
        </p>
        <div className="stack">
          <TextInput
            label="Provider name"
            value={name}
            onChange={(event) => setName(event.target.value)}
            placeholder="PixStudio"
            hint="A label for the Connections screen. It is not sent to the provider."
          />
          <TextInput
            label="API base URL"
            value={baseUrl}
            onChange={(event) => setBaseUrl(event.target.value)}
            placeholder="https://api.example.com/v1"
            hint="The API root, not the website address. Both /v1 and the bare host work."
            required
          />
          <TextInput
            label="API key"
            type="password"
            value={apiKey}
            onChange={(event) => setApiKey(event.target.value)}
            autoComplete="off"
            placeholder="sk-..."
            hint="Sent once to the backend, sealed at rest, and never rendered back."
            required
          />
          <details>
            <summary className="muted">Advanced</summary>
            <div className="stack" style={{ marginTop: 'var(--s3)' }}>
              <TextInput
                label="Allowed hosts (comma separated)"
                value={allowedHosts}
                onChange={(event) => setAllowedHosts(event.target.value)}
                placeholder="127.0.0.1"
                hint="Only needed for a self-hosted endpoint on a private network or plain http. Public https needs nothing here."
              />
              <Checkbox
                label="Test conversation models with one token"
                hint="Cheap and bounded: at most 24 models, one token each. Unchecking means the report says 'not probed' rather than guessing."
                checked={probeChat}
                onChange={(event) => setProbeChat(event.target.checked)}
              />
            </div>
          </details>
        </div>
        <div className="row row--tight" style={{ marginTop: 'var(--s3)' }}>
          <Button
            variant="primary"
            onClick={discover}
            disabled={discovering || !baseUrl.trim() || !apiKey}
            disabledReason="The base URL and key are both required."
          >
            <span className="btn__icon">
              <Icon name="wand" size={14} />
            </span>
            {discovering ? 'Testing the endpoint…' : 'Test endpoint'}
          </Button>
          {plan ? (
            <Button
              variant="ghost"
              onClick={() => {
                setPlan(null);
                setSelected(new Set());
                setError(null);
              }}
            >
              Start over
            </Button>
          ) : null}
        </div>
        <p className="muted" style={{ margin: 'var(--s3) 0 0' }}>
          This step writes nothing and generates nothing. Only the one-token probes send a request.
        </p>
      </div>

      {discovering ? <LoadingState label="Reading the model list and probing conversation models…" /> : null}
      {error ? <ErrorNotice error={error} onRetry={plan ? register : discover} retryLabel={plan ? 'Try registering again' : 'Test again'} /> : null}

      {plan ? (
        <>
          <div className="panel">
            <p className="panel__title">
              <Icon name="info" size={13} /> What this endpoint reported
            </p>
            <div className="job__meta">
              <span className="mono">{plan.provider.base_url}</span>
              <span className="mono">{plan.provider.model_list_path}</span>
              <span>
                {plan.models.length} listed · {drivable.length} this build can drive
              </span>
              {plan.probe_summary ? (
                <span>
                  chat probes: {plan.probe_summary.available}/{plan.probe_summary.probed} answered
                </span>
              ) : (
                <span>chat probes: skipped</span>
              )}
            </div>
            <p className="muted" style={{ margin: 'var(--s2) 0 0' }}>
              Default conversation model: <span className="mono">{plan.default_chat || 'none'}</span>
              {plan.default_chat !== plan.default_chat_requested
                ? ` (${plan.default_chat_requested} was not listed)`
                : ''}
            </p>
            {plan.warnings.map((warning) => (
              <p key={warning} className="dim" style={{ margin: 'var(--s1) 0 0' }}>
                {warning}
              </p>
            ))}
            <p className="dim" style={{ margin: 'var(--s2) 0 0' }}>
              {plan.notes.probe}
            </p>
          </div>

          <div className="row row--tight">
            <Button size="sm" onClick={() => setSelected(new Set(drivable.map((item) => item.model_key)))}>
              Select all drivable
            </Button>
            <Button size="sm" onClick={() => setSelected(new Set())}>
              Select none
            </Button>
          </div>

          {grouped.map((group) => (
            <div className="panel" key={group.family}>
              <p className="panel__title">
                {FAMILY_LABEL[group.family] ?? group.family}{' '}
                <span className="dim">· {group.models.length}</span>
              </p>
              <div className="stack">
                {group.models.map((model) => (
                  <ModelRow
                    key={model.model_key}
                    model={model}
                    checked={selected.has(model.model_key)}
                    onToggle={() =>
                      setSelected((current) => {
                        const next = new Set(current);
                        if (next.has(model.model_key)) next.delete(model.model_key);
                        else next.add(model.model_key);
                        return next;
                      })
                    }
                  />
                ))}
              </div>
            </div>
          ))}

          <div className="panel">
            <div className="row row--tight" style={{ justifyContent: 'space-between', alignItems: 'center' }}>
              <div>
                <p className="panel__title" style={{ marginBottom: 'var(--s1)' }}>
                  Register {chosenCount} model{chosenCount === 1 ? '' : 's'}
                </p>
                <p className="muted" style={{ margin: 0 }}>
                  Registering is free. Generation and conversation spend your provider credit later.
                </p>
              </div>
              <Button
                variant="primary"
                onClick={register}
                disabled={registering || chosenCount === 0}
                disabledReason="Tick at least one model this build can drive."
              >
                <span className="btn__icon">
                  <Icon name="save" size={14} />
                </span>
                {registering ? 'Saving…' : `Register ${chosenCount || ''}`.trim()}
              </Button>
            </div>
          </div>
        </>
      ) : null}
    </div>
  );
}

function ModelRow({
  model,
  checked,
  onToggle,
}: {
  model: DiscoveredModel;
  checked: boolean;
  onToggle: () => void;
}) {
  return (
    <div className="card" style={{ opacity: model.drivable ? 1 : 0.7 }}>
      <div className="row row--tight" style={{ alignItems: 'flex-start' }}>
        <Checkbox
          label=""
          aria-label={`Register ${model.model_key}`}
          checked={checked}
          disabled={!model.drivable}
          onChange={onToggle}
        />
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="card__head" style={{ marginBottom: 'var(--s1)' }}>
            <span className="card__title">{model.display_name || model.model_key}</span>
            <Badge tone={model.drivable ? 'ok' : 'plain'}>{FAMILY_LABEL[model.family] ?? model.family}</Badge>
            <ProbeBadge model={model} />
          </div>
          <p className="mono dim" style={{ margin: '0 0 var(--s1)' }}>
            {model.model_key}
          </p>
          <p className="muted" style={{ margin: 0 }}>
            {model.reason}
          </p>
        </div>
      </div>
    </div>
  );
}

function ProbeBadge({ model }: { model: DiscoveredModel }) {
  if (model.family !== 'chat') return <Badge tone="plain">not probed</Badge>;
  if (!model.probe) return <Badge tone="plain">not probed</Badge>;
  if (model.probe.ok) {
    return <Badge tone="ok">answered · {model.probe.latency_ms} ms</Badge>;
  }
  const reason = model.probe.error ?? (model.probe.status ? `HTTP ${model.probe.status}` : 'no answer');
  return <Badge tone="warn">failed · {reason}</Badge>;
}
