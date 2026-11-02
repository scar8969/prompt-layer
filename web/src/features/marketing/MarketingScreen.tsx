import { useMemo, useState } from 'react';
import { marketing as marketingApi, models as modelsApi } from '../../api/endpoints.ts';
import type { MarketingBrief, MarketingCampaign, Model } from '../../api/types.ts';
import { Badge } from '../../components/Badge.tsx';
import { Button } from '../../components/Button.tsx';
import { TextArea, TextInput, Select } from '../../components/Field.tsx';
import { Icon } from '../../components/Icon.tsx';
import { Link } from '../../app/router.tsx';
import { useQuery } from '../../state/query.ts';
import { useToasts } from '../../state/toasts.tsx';

/**
 * Marketing Studio.
 *
 * Two visible halves, because they are two different things: **building the
 * brief** is deterministic text work over what the page states and what the author
 * types, and **rendering** is one real generation per format on the user's own
 * key. The screen never presents model-written copy as if it read the page.
 */
export function MarketingScreen() {
  const toasts = useToasts();
  const [url, setUrl] = useState('');
  const [title, setTitle] = useState('');
  const [description, setDescription] = useState('');
  const [audience, setAudience] = useState('');
  const [benefit, setBenefit] = useState('');
  const [tone, setTone] = useState('direct');
  const [formats, setFormats] = useState<string[]>([]);
  const [modelId, setModelId] = useState<string | null>(null);
  const [brief, setBrief] = useState<MarketingBrief | null>(null);
  const [campaign, setCampaign] = useState<MarketingCampaign | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);

  const meta = useQuery('marketing:formats', () => marketingApi.formats(), { staleMs: 300_000 });
  const models = useQuery<Model[]>('models?modality=image&enabled=1&marketing', () => modelsApi.list({ modality: 'image', enabled: true }), {
    staleMs: 30_000,
  });
  const tones = meta.data?.tones?.length ? meta.data.tones : ['direct', 'warm', 'editorial', 'playful', 'technical'];
  const available = meta.data?.formats ?? [];

  const selectedFormats = formats.length ? formats : available.map((item) => item.key);
  const eligibleModels = models.data ?? [];

  const facts = useMemo(
    () => ({
      url: url.trim() || undefined,
      title: title.trim() || undefined,
      description: description.trim() || undefined,
      audience: audience.trim() || undefined,
      benefit: benefit.trim() || undefined,
      tone,
    }),
    [url, title, description, audience, benefit, tone],
  );

  const build = async () => {
    setBusy(true);
    setError(null);
    setCampaign(null);
    try {
      const result = await marketingApi.brief(facts);
      setBrief(result.brief);
      if (!formats.length) setFormats(result.brief.formats.map((item) => item.key));
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  const render = async () => {
    if (!modelId) return;
    setBusy(true);
    setError(null);
    try {
      const result = await marketingApi.campaign({ ...facts, model_id: modelId, formats: selectedFormats });
      setBrief(result.brief);
      setCampaign(result);
      toasts.push({
        tone: 'success',
        title: 'Campaign queued',
        text: `${result.renders.length} real generation(s) on your own key.`,
      });
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__heading">Marketing Studio</h1>
          <p className="page__subtitle">
            A product page read deterministically, then one real generation per format on your own key.
            The copy is composed from what the page states and what you type; no model writes it.
          </p>
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(320px, 420px) 1fr', gap: 'var(--s4)', alignItems: 'start' }}>
        <section className="panel" style={{ padding: 'var(--s4)' }}>
          <p className="panel__title" style={{ marginBottom: 10 }}>
            1 · Read the product
          </p>
          <div className="stack stack--tight">
            <TextInput
              label="Product URL"
              value={url}
              onChange={(event) => setUrl(event.target.value)}
              placeholder="https://example.com/product"
              hint="Read through the same egress policy as every outbound call. Leave empty to describe the product by hand."
            />
            <TextInput label="Product name" value={title} onChange={(event) => setTitle(event.target.value)} />
            <TextArea
              label="Description"
              rows={3}
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              hint="Used verbatim. The brief reports where each field came from."
            />
            <TextInput
              label="Audience"
              value={audience}
              onChange={(event) => setAudience(event.target.value)}
              placeholder="people who read at night"
            />
            <TextInput
              label="Benefit"
              value={benefit}
              onChange={(event) => setBenefit(event.target.value)}
              placeholder="light that fades like sunset"
              hint="The claim the ad makes. Left empty, the brief warns instead of inventing one."
            />
            <Select
              label="Tone"
              value={tone}
              onChange={(event) => setTone(event.target.value)}
              options={tones.map((item) => ({ value: item, label: item }))}
              hint="Changes the hook sentence, not the stated facts."
            />
            <Button variant="primary" onClick={() => void build()} disabled={busy}>
              {busy ? 'Reading…' : 'Build the brief'}
            </Button>
            {error ? (
              <p style={{ margin: 0, color: 'var(--danger)' }}>
                {error instanceof Error ? error.message : 'The brief could not be built.'}
              </p>
            ) : null}
          </div>
        </section>

        <section className="stack">
          {!brief ? (
            <div className="panel" style={{ minHeight: 240, display: 'grid', placeItems: 'center' }}>
              <p className="dim" style={{ margin: 0 }}>
                Build a brief to see the hook, the formats and what the page actually stated.
              </p>
            </div>
          ) : (
            <>
              <div className="panel" style={{ padding: 'var(--s4)' }}>
                <div className="row row--tight" style={{ justifyContent: 'space-between' }}>
                  <p className="panel__title" style={{ margin: 0 }}>
                    2 · The brief
                  </p>
                  <Badge tone="plain">{brief.tone}</Badge>
                </div>
                <h2 style={{ margin: '10px 0 6px', fontSize: 20 }}>{brief.hook || brief.product}</h2>
                <p style={{ margin: 0, color: 'var(--text-muted)' }}>{brief.description || 'No description stated.'}</p>

                {brief.warnings.length ? (
                  <div className="panel panel--warn" style={{ marginTop: 12 }}>
                    <p className="panel__title" style={{ marginBottom: 6 }}>
                      <Icon name="alert" size={13} /> What this brief does not claim
                    </p>
                    <ul style={{ margin: 0, paddingLeft: 18 }}>
                      {brief.warnings.map((warning) => (
                        <li key={warning} style={{ fontSize: 13 }}>
                          {warning}
                        </li>
                      ))}
                    </ul>
                  </div>
                ) : null}

                <dl className="hf-kv" style={{ marginTop: 14 }}>
                  <dt>Product</dt>
                  <dd>{brief.product}</dd>
                  <dt>Features</dt>
                  <dd>{brief.features.join(' · ') || '—'}</dd>
                  <dt>Sources</dt>
                  <dd>
                    {Object.entries(brief.sources).map(([field, source]) => `${field}: ${source}`).join(' · ') || '—'}
                  </dd>
                  <dt>Image reference</dt>
                  <dd style={{ wordBreak: 'break-all' }}>{brief.image_reference || '—'}</dd>
                </dl>
              </div>

              <div className="panel" style={{ padding: 'var(--s4)' }}>
                <p className="panel__title" style={{ marginBottom: 8 }}>
                  3 · Formats and the model
                </p>
                <div className="chip-row" role="group" aria-label="Formats to render">
                  {available.map((format) => {
                    const on = selectedFormats.includes(format.key);
                    return (
                      <button
                        key={format.key}
                        type="button"
                        className="chip"
                        aria-pressed={on}
                        onClick={() =>
                          setFormats((current) => {
                            const next = current.length ? current : available.map((item) => item.key);
                            return next.includes(format.key)
                              ? next.filter((key) => key !== format.key)
                              : [...next, format.key];
                          })
                        }
                      >
                        {format.label} <span className="dim">{format.aspect_ratio}</span>
                      </button>
                    );
                  })}
                </div>

                <div className="row row--tight" style={{ marginTop: 12, flexWrap: 'wrap', gap: 8, alignItems: 'end' }}>
                  <div style={{ flex: 1, minWidth: 220 }}>
                    <Select
                      label="Model"
                      value={modelId ?? ''}
                      onChange={(event) => setModelId(event.target.value || null)}
                      placeholder={eligibleModels.length ? 'Choose an image model…' : 'No image model connected'}
                      options={eligibleModels.map((model) => ({ value: model.id, label: model.display_name }))}
                      hint="Rendering uses your own provider key. Nothing is queued until you press the button."
                    />
                  </div>
                  <Button variant="primary" onClick={() => void render()} disabled={!modelId || busy}>
                    <span className="btn__icon">
                      <Icon name="sparkle" size={13} />
                    </span>
                    {busy ? 'Queueing…' : 'Render the campaign'}
                  </Button>
                </div>
              </div>

              {campaign ? (
                <div className="panel" style={{ padding: 'var(--s4)' }}>
                  <p className="panel__title" style={{ marginBottom: 8 }}>
                    4 · Queued renders
                  </p>
                  <div className="stack stack--tight">
                    {campaign.renders.map((item) => (
                      <div key={item.format} className="row row--tight" style={{ justifyContent: 'space-between', gap: 10 }}>
                        <div>
                          <strong style={{ fontSize: 13 }}>{item.label}</strong>
                          <p className="dim" style={{ margin: 0, fontSize: 12 }}>
                            {item.headline}
                          </p>
                        </div>
                        <Link to={`/history/${item.job_id}`} className="btn btn--sm">
                          Job {item.job_id.slice(0, 8)}
                        </Link>
                      </div>
                    ))}
                  </div>
                  <p className="dim" style={{ margin: '10px 0 0', fontSize: 'var(--fs-xs)' }}>
                    The facts and the prompts above are the ones queued; the History screen reports what the
                    provider actually did with them.
                  </p>
                </div>
              ) : null}

              <div className="panel" style={{ padding: 'var(--s4)' }}>
                <p className="panel__title" style={{ marginBottom: 8 }}>
                  Every prompt in this campaign
                </p>
                <div className="stack stack--tight">
                  {brief.formats
                    .filter((format) => selectedFormats.includes(format.key))
                    .map((format) => (
                      <div key={format.key}>
                        <p className="dim" style={{ margin: '0 0 4px', fontSize: 11, textTransform: 'uppercase', letterSpacing: '.08em' }}>
                          {format.label} · {format.aspect_ratio}
                        </p>
                        <pre className="hf-prompt" style={{ margin: 0 }}>
                          {format.prompt}
                        </pre>
                      </div>
                    ))}
                </div>
              </div>
            </>
          )}
        </section>
      </div>
    </div>
  );
}
