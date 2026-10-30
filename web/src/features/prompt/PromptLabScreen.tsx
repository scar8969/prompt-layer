import { useEffect, useMemo, useState } from 'react';
import { prompt as promptApi } from '../../api/endpoints.ts';
import type { PromptLayerFact, PromptModel, PromptPreflight } from '../../api/types.ts';
import { Badge } from '../../components/Badge.tsx';
import { Button } from '../../components/Button.tsx';
import { CodeBlock, KeyValueList, Section } from '../../components/Data.tsx';
import { Checkbox, Select, TextArea, TextInput } from '../../components/Field.tsx';
import { ErrorNotice } from '../../components/ErrorNotice.tsx';
import { ErrorState, LoadingState } from '../../components/State.tsx';
import { Link, useRouter } from '../../app/router.tsx';
import { usePromptModel, usePromptModels } from '../../state/promptLayer.ts';
import { titleCase, truncate } from '../../lib/util.ts';

/**
 * Prompt Lab: what a provider does to your prompt, before you spend anything.
 *
 * This screen is the merge of the two halves of the repository. It used to be a
 * separate static page with its own JSON functions; it is now one surface inside
 * the studio, reading the same dataset through the same session. Nothing here
 * guesses: an unknown model is labelled unknown, a layer is listed with the
 * parameter that turns it off, and every claim carries the URL it was read from.
 */

/** One switch a provider documents: sending this value turns a layer off. */
interface OffSwitch {
  id: string;
  key: string;
  value: unknown;
  layer: PromptLayerFact;
}

function offSwitches(model: PromptModel | undefined): OffSwitch[] {
  if (!model) return [];
  const seen = new Map<string, OffSwitch>();
  for (const layer of model.layers) {
    if (!layer.active || !layer.disable_with) continue;
    for (const [key, value] of Object.entries(layer.disable_with)) {
      const id = `${layer.id}:${key}`;
      if (!seen.has(id)) seen.set(id, { id, key, value, layer });
    }
  }
  return Array.from(seen.values());
}

function kindTone(kind: string): 'warn' | 'danger' | 'accent' | 'plain' {
  if (kind === 'rewrite') return 'danger';
  if (kind === 'expand') return 'warn';
  if (kind === 'augment' || kind === 'inject') return 'accent';
  return 'plain';
}

function LayerCard({ layer }: { layer: PromptLayerFact }) {
  return (
    <div className="card card--sunken" style={{ display: 'grid', gap: 6 }}>
      <div className="row row--tight" style={{ flexWrap: 'wrap' }}>
        <Badge tone={layer.active ? kindTone(layer.kind) : 'plain'}>
          {layer.active ? layer.kind : `off · ${layer.kind}`}
        </Badge>
        <span className="mono" style={{ fontSize: 'var(--fs-xs)' }}>
          {layer.id}
        </span>
        {layer.your_override ? <Badge tone="ok">your override</Badge> : null}
      </div>
      <p style={{ margin: 0 }}>{layer.detail}</p>
      {layer.disable_with ? (
        <p className="field__hint" style={{ margin: 0 }}>
          Off switch:{' '}
          {Object.entries(layer.disable_with).map(([key, value], index) => (
            <span key={key}>
              {index > 0 ? ', ' : ''}
              <span className="mono">
                {key}={JSON.stringify(value)}
              </span>
            </span>
          ))}
        </p>
      ) : (
        <p className="field__hint" style={{ margin: 0 }}>
          No documented switch. The provider applies this and does not offer a way to stop it.
        </p>
      )}
      {layer.source ? (
        <p className="field__hint mono" style={{ margin: 0, wordBreak: 'break-all' }}>
          {layer.source}
        </p>
      ) : null}
      {layer.evidence ? (
        <p className="dim" style={{ margin: 0, fontSize: 'var(--fs-xs)' }}>
          “{truncate(layer.evidence, 220)}”
        </p>
      ) : null}
    </div>
  );
}

export function PromptLabScreen() {
  const { route, navigate } = useRouter();
  const models = usePromptModels();
  const [slug, setSlug] = useState<string>('');
  const [prompt, setPrompt] = useState('');
  const [systemMessage, setSystemMessage] = useState('');
  const [imageCount, setImageCount] = useState(0);
  const [overrides, setOverrides] = useState<Record<string, unknown>>({});
  const [result, setResult] = useState<PromptPreflight | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [pending, setPending] = useState(false);

  const requested = route.params.get('model');
  const model = usePromptModel(slug || null);

  useEffect(() => {
    if (slug || !requested) return;
    setSlug(requested);
  }, [requested, slug]);

  useEffect(() => {
    // Changing model invalidates the assembled answer: it describes a different
    // provider, and leaving it on screen would read like a current result.
    setResult(null);
    setError(null);
    setOverrides({});
  }, [slug]);

  const switches = useMemo(() => offSwitches(model.data), [model.data]);
  const activeLayers = (model.data?.layers ?? []).filter((layer) => layer.active);
  const isChat = model.data?.kind === 'chat';

  const assemble = async () => {
    if (!slug || !prompt.trim()) return;
    setPending(true);
    setError(null);
    try {
      const preflight = await promptApi.preflight({
        model: slug,
        prompt,
        options: overrides,
        system_message: systemMessage.trim() || undefined,
        image_count: imageCount,
      });
      setResult(preflight);
    } catch (err) {
      setError(err);
      setResult(null);
    } finally {
      setPending(false);
    }
  };

  if (models.loading) return <LoadingState label="Reading the layer dataset…" />;
  if (models.error) return <ErrorState error={models.error} onRetry={models.reload} />;

  const rows = models.data?.models ?? [];

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__heading">Prompt Lab</h1>
          <p className="page__subtitle">
            Every hosted endpoint rewrites, expands, screens or pads your prompt before the model runs, and the
            studio UI never shows it. Pick a documented model, paste the prompt you intend to send, and read
            exactly what arrives upstream — plus the parameter that gives your wording back.
          </p>
        </div>
        <Badge tone="plain">dataset {models.data?.datasetVersion || 'unknown'}</Badge>
      </div>

      <Section
        title="1 · The model"
        description="This list is the dataset, not a capability claim: a model that is absent here is undocumented, and the preflight will say so rather than guess."
      >
        <div className="grid grid--2">
          <Select
            label="Documented model"
            value={slug}
            onChange={(event) => setSlug(event.target.value)}
            placeholder="Choose a model…"
            options={rows.map((item) => ({
              value: item.slug,
              label: `${item.display} — ${item.active_layer_count}/${item.layer_count} layers active`,
            }))}
            hint={
              rows.length
                ? `${rows.length} models have documented facts.`
                : 'The dataset reported no models. That is a backend problem, not a missing feature.'
            }
          />
          <div className="field">
            <span className="field__label">What this model adds</span>
            {model.loading ? <p className="muted" style={{ margin: 0 }}>Reading…</p> : null}
            {model.error ? <ErrorNotice error={model.error} onRetry={model.reload} /> : null}
            {model.data ? (
              <p style={{ margin: 0 }}>
                {model.data.active_layer_count ?? activeLayers.length} of {model.data.layers.length} documented
                layers are active by default.{' '}
                {switches.length
                  ? `${switches.length} of them can be switched off from this screen.`
                  : 'None of them documents a switch, so the only control you have is the wording.'}
              </p>
            ) : (
              <p className="muted" style={{ margin: 0 }}>
                Nothing is selected yet.
              </p>
            )}
          </div>
        </div>

        {model.data ? (
          <div className="stack stack--tight">
            <KeyValueList
              rows={[
                { key: 'Endpoint', value: <span className="mono">{model.data.endpoint}</span> },
                { key: 'Provider', value: model.data.provider || '—' },
                { key: 'Kind', value: titleCase(model.data.kind) },
                {
                  key: 'Accepts an image',
                  value: model.data.accepts_image ? 'Yes' : 'No — text only',
                },
                { key: 'Sources', value: `${model.data.sources.length} document(s)` },
              ]}
            />
            <div className="row row--tight" style={{ flexWrap: 'wrap' }}>
              <Button onClick={() => navigate(`/image`)} size="sm">
                Generate with a registered model
              </Button>
              <Link to="/models" className="btn btn--sm btn--ghost">
                Registered models
              </Link>
            </div>
          </div>
        ) : null}
      </Section>

      <Section
        title="2 · Your prompt"
        description="The switches below are the provider's own parameters. Sending one of them is the only documented way to keep your wording."
      >
        <TextArea
          label="Prompt"
          tall
          value={prompt}
          onChange={(event) => setPrompt(event.target.value)}
          placeholder="A lighthouse on a basalt cliff at dusk, 35mm, long exposure"
          hint={`${prompt.length} characters. The preflight reports the exact characters that travel upstream, including anything the provider adds.`}
        />
        <div className="grid grid--2">
          {isChat ? (
            <TextInput
              label="System message (optional)"
              value={systemMessage}
              onChange={(event) => setSystemMessage(event.target.value)}
              hint="Leave empty to see the chat template's own default, which is what the provider would inject."
            />
          ) : (
            <TextInput
              label="Reference images (optional)"
              type="number"
              min={0}
              max={8}
              value={imageCount}
              onChange={(event) => setImageCount(Math.max(0, Math.min(8, Number(event.target.value) || 0)))}
              hint="How many images travel with the prompt. Only endpoints that accept an image use this."
            />
          )}
        </div>

        {switches.length ? (
          <div className="field">
            <span className="field__label">Switches the provider documents</span>
            <div className="stack stack--tight">
              {switches.map((item) => {
                const checked = Object.prototype.hasOwnProperty.call(overrides, item.key) && overrides[item.key] === item.value;
                return (
                  <Checkbox
                    key={item.id}
                    label={`Turn off ${item.layer.id} — send ${item.key}=${JSON.stringify(item.value)}`}
                    hint={truncate(item.layer.detail, 160)}
                    checked={checked}
                    onChange={(event) =>
                      setOverrides((current) => {
                        const next = { ...current };
                        if (event.target.checked) next[item.key] = item.value;
                        else delete next[item.key];
                        return next;
                      })
                    }
                  />
                );
              })}
            </div>
          </div>
        ) : null}

        <div className="row row--tight">
          <Button
            variant="primary"
            onClick={() => void assemble()}
            disabled={!slug || !prompt.trim() || pending}
            disabledReason="Pick a model and write a prompt first."
          >
            {pending ? 'Assembling…' : 'Assemble the preflight'}
          </Button>
          {error ? <span className="dim">The last attempt failed; the error is below.</span> : null}
        </div>
        {error ? <ErrorNotice error={error} /> : null}
      </Section>

      {result ? (
        <>
          <Section
            title="3 · What actually arrives upstream"
            description={
              result.unknown_model
                ? 'No facts on record for this model. The layers below are the generic ones, marked unconfirmed — do not read this as a model that adds nothing.'
                : `Assembled from dataset ${result.version}, endpoint ${result.endpoint}.`
            }
            actions={
              <Badge tone={result.warnings.length ? 'warn' : 'ok'}>
                {result.layers.filter((layer) => layer.active).length} active · {result.warnings.length} warning
                {result.warnings.length === 1 ? '' : 's'}
              </Badge>
            }
          >
            {result.warnings.length ? (
              <div className="panel panel--warn">
                <p className="panel__title">What the provider will do to this prompt</p>
                <ul style={{ margin: 0, paddingLeft: 18 }}>
                  {result.warnings.map((warning) => (
                    <li key={warning}>{warning}</li>
                  ))}
                </ul>
              </div>
            ) : (
              <p className="muted" style={{ margin: 0 }}>
                No rewriting layer is active for this prompt and model. The resolved parameters below still apply.
              </p>
            )}

            <div className="grid grid--2">
              <div className="stack stack--tight">
                <span className="field__label">Prompt as it leaves you</span>
                <pre className="code">{result.prompt.original || '(empty)'}</pre>
              </div>
              <div className="stack stack--tight">
                <span className="field__label">
                  Prompt as the endpoint receives it
                  {result.prompt.rewritten_upstream ? ' — rewritten upstream' : ''}
                </span>
                <pre className="code">{result.prompt.forwarded_as || '(empty)'}</pre>
              </div>
            </div>

            <KeyValueList
              rows={[
                { key: 'Estimated prompt tokens', value: String(result.estimate.prompt_tokens) },
                { key: 'Characters added by layers', value: String(result.estimate.added_chars) },
                { key: 'Note', value: result.estimate.note || '—' },
              ]}
            />
          </Section>

          <Section
            title="Every layer on this request"
            description="Active layers are applied; inactive ones are listed so you can see what the switch bought you."
          >
            <div className="stack stack--tight">
              {result.layers.map((layer) => (
                <LayerCard key={layer.id} layer={layer} />
              ))}
            </div>
          </Section>

          {result.conditional_layers.length ? (
            <Section
              title="Conditional layers"
              description="These apply to a different call shape than the one you just assembled. They are listed so the count is not mistaken for the whole picture."
            >
              <div className="stack stack--tight">
                {result.conditional_layers.map((layer) => (
                  <div className="card card--sunken" key={layer.id}>
                    <div className="row row--tight" style={{ flexWrap: 'wrap' }}>
                      <Badge tone="plain">{layer.kind}</Badge>
                      <span className="mono" style={{ fontSize: 'var(--fs-xs)' }}>
                        {layer.id}
                      </span>
                      <span className="dim" style={{ fontSize: 'var(--fs-xs)' }}>
                        applies to {layer.applies_to}
                      </span>
                    </div>
                    <p style={{ margin: '6px 0 0' }}>{layer.detail}</p>
                  </div>
                ))}
              </div>
            </Section>
          ) : null}

          <Section
            title="Resolved parameters"
            description="Provider default unless you sent an override. Nothing here is invented: an absent value means the field is not sent at all."
          >
            <div className="table-wrap">
              <table className="table table--compact">
                <thead>
                  <tr>
                    <th>Parameter</th>
                    <th>Value</th>
                    <th>Source</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(result.params).map(([name, param]) => (
                    <tr key={name}>
                      <td className="mono">{name}</td>
                      <td className="mono">{param.value === null ? 'null' : JSON.stringify(param.value)}</td>
                      <td>
                        <Badge tone={param.source === 'your override' ? 'ok' : 'plain'}>{param.source}</Badge>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Section>

          <Section title="Raw payload" description="What a request built from this answer would carry.">
            <CodeBlock
              code={JSON.stringify(
                {
                  model: result.model,
                  endpoint: result.endpoint,
                  prompt: result.prompt.forwarded_as,
                  messages: result.messages,
                  params: Object.fromEntries(
                    Object.entries(result.params).map(([name, param]) => [name, param.value]),
                  ),
                },
                null,
                2,
              )}
              label="request"
            />
            {Object.keys(result.full_control_patch).length ? (
              <CodeBlock
                code={JSON.stringify(result.full_control_patch, null, 2)}
                label="full-control patch (the documented settings that keep your wording)"
              />
            ) : null}
          </Section>
        </>
      ) : null}
    </div>
  );
}
