import { useEffect, useState } from 'react';
import { models as modelApi } from '../../api/endpoints.ts';
import type { Model } from '../../api/types.ts';
import { Badge, CapabilityBadge, MockBadge } from '../../components/Badge.tsx';
import { Button } from '../../components/Button.tsx';
import { CodeBlock, KeyValueList, Section } from '../../components/Data.tsx';
import { Checkbox, TextInput } from '../../components/Field.tsx';
import { ErrorNotice } from '../../components/ErrorNotice.tsx';
import { ErrorState, LoadingState } from '../../components/State.tsx';
import { DestructiveAction } from '../../components/Confirm.tsx';
import { Switch } from '../../components/Controls.tsx';
import { Link, useRouter } from '../../app/router.tsx';
import { useModel, useModelCapabilities, useModelParameters } from '../../state/models.ts';
import { useModeVocabulary, usePromptCatalog } from '../../state/promptLayer.ts';
import { invalidate } from '../../state/query.ts';
import { useToasts } from '../../state/toasts.tsx';
import { formatDateTime, titleCase } from '../../lib/util.ts';

/**
 * One model's full profile: what it declares, what the backend resolves for it
 * at generation time, and the parameter schema the studios render.
 */
export function ModelDetailScreen({ modelId }: { modelId: string }) {
  const { navigate } = useRouter();
  const toasts = useToasts();
  const model = useModel(modelId);
  const capabilities = useModelCapabilities(modelId);
  const parameters = useModelParameters(modelId);
  const [displayName, setDisplayName] = useState('');
  const [modes, setModes] = useState<string[]>([]);
  const [error, setError] = useState<unknown>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    if (!model.data) return;
    setDisplayName(model.data.display_name);
    setModes(model.data.modes);
  }, [model.data?.id]);

  if (model.loading) return <LoadingState label="Reading the model…" />;
  if (model.error) return <ErrorState error={model.error} onRetry={model.reload} />;
  if (!model.data) return <ErrorState error={new Error('The backend returned no model.')} />;

  const record: Model = model.data;
  const capabilityData = capabilities.data;
  const resolved = capabilityData?.capabilities ?? record.capabilities;

  const save = async () => {
    setPending(true);
    setError(null);
    try {
      await modelApi.patch(record.id, {
        display_name: displayName.trim() || record.display_name,
        modes,
      });
      invalidate('models');
      model.reload();
      capabilities.reload();
      toasts.push({ tone: 'success', title: 'Model updated', text: displayName });
    } catch (err) {
      setError(err);
    } finally {
      setPending(false);
    }
  };

  const remove = async () => {
    await modelApi.remove(record.id);
    invalidate('models');
    toasts.push({ tone: 'info', title: 'Model removed', text: record.display_name });
    navigate('/models');
  };

  // The vocabulary comes from the service that validates the PATCH, not from a
  // second list kept in this file: three of the names this form used to offer
  // (`generate`, `edit`, `transform`) were rejected on save.
  const vocabulary = useModeVocabulary();
  const allModes = (record.modality === 'video' ? vocabulary.data?.video : vocabulary.data?.image) ?? [];
  const declared = new Set(record.modes);

  // What this model does to a prompt, when a document in this repository
  // describes its endpoint. The join is the endpoint id, so it survives a model
  // registered by hand against a documented endpoint.
  const catalog = usePromptCatalog();
  const promptFacts = (catalog.data?.endpoints ?? []).find((entry) => entry.endpoint === record.model_key) ?? null;

  return (
    <div className="page page--wide">
      <div className="page__header">
        <div>
          <div className="row row--tight" style={{ flexWrap: 'wrap' }}>
            <Badge tone={record.is_enabled ? 'ok' : 'plain'}>{record.is_enabled ? 'enabled' : 'disabled'}</Badge>
            <MockBadge isMock={record.provider.is_mock} />
            {record.is_default ? <Badge tone="accent">default</Badge> : null}
          </div>
          <h1 className="page__heading">{record.display_name}</h1>
          <p className="page__subtitle">
            {record.provider.adapter_label} · {record.provider.connection_name} · <span className="mono">{record.model_key}</span>
          </p>
        </div>
        <div className="page__actions">
          <Link to="/models" className="btn btn--sm">
            All models
          </Link>
          <Link to={`/connections/${record.connection_id}`} className="btn btn--sm">
            Open connection
          </Link>
          <DestructiveAction
            label="Remove model"
            title="Remove this model?"
            body={
              <p style={{ margin: 0 }}>
                The model is unregistered. Jobs that already ran against it stay in history, and the connection itself is
                not touched.
              </p>
            }
            confirmLabel="Remove model"
            onConfirm={remove}
          />
        </div>
      </div>

      <div className="grid grid--2">
        <Section title="Identity">
          <KeyValueList
            rows={[
              { key: 'Modality', value: <span className="mono">{record.modality}</span> },
              { key: 'Source', value: record.source },
              { key: 'Connection id', value: <span className="mono">{record.connection_id}</span> },
              {
                key: 'Connection state',
                value: record.provider.connection_enabled ? 'enabled' : 'disabled — generations will fail until it is re-enabled',
              },
              { key: 'Base URL', value: <span className="mono">{record.provider.base_url ?? 'not reported'}</span> },
              { key: 'Created', value: formatDateTime(record.created_at) },
              { key: 'Updated', value: formatDateTime(record.updated_at) },
            ]}
          />
        </Section>

        <Section title="Edit">
          <div className="stack stack--tight">
            <TextInput
              label="Display name"
              value={displayName}
              onChange={(event) => setDisplayName(event.target.value)}
            />
            <div className="field">
              <span className="field__label">Declared modes</span>
              <p className="field__hint" style={{ marginTop: 0 }}>
                A studio control is enabled only for a mode declared here. Unticking a mode disables its control
                everywhere.
              </p>
              <div className="stack stack--tight">
                {allModes.map((mode) => (
                  <Checkbox
                    key={mode}
                    label={titleCase(mode)}
                    checked={declared.has(mode)}
                    onChange={(event) => {
                      setModes((current) =>
                        event.target.checked ? [...current, mode] : current.filter((item) => item !== mode),
                      );
                    }}
                  />
                ))}
              </div>
            </div>
            {error ? <ErrorNotice error={error} /> : null}
            <Button variant="primary" onClick={() => void save()} disabled={pending}>
              {pending ? 'Saving…' : 'Save changes'}
            </Button>
          </div>
        </Section>
      </div>

      <Section title="Flags">
        <div className="grid grid--2">
          <Switch
            label="Model enabled"
            hint="A disabled model is hidden from the studios."
            checked={record.is_enabled}
            onChange={async (next) => {
              try {
                await modelApi.patch(record.id, { is_enabled: next });
                invalidate('models');
                model.reload();
              } catch (err) {
                setError(err);
              }
            }}
          />
          <Switch
            label="Favourite"
            hint="Favourites sort first in the registry."
            checked={record.is_favorite}
            onChange={async (next) => {
              try {
                await modelApi.patch(record.id, { is_favorite: next });
                invalidate('models');
                model.reload();
              } catch (err) {
                setError(err);
              }
            }}
          />
          <Switch
            label="Workspace default"
            hint="The backend's own default flag for this workspace."
            checked={record.is_default}
            onChange={async (next) => {
              try {
                await modelApi.patch(record.id, { is_default: next });
                invalidate('models');
                model.reload();
              } catch (err) {
                setError(err);
              }
            }}
          />
        </div>
      </Section>

      <Section title="Capability profile">
        {capabilities.loading ? <p className="muted" style={{ margin: 0 }}>Resolving capabilities…</p> : null}
        {capabilities.error ? <ErrorNotice error={capabilities.error} onRetry={capabilities.reload} /> : null}
        <div className="grid grid--2">
          <KeyValueList
            rows={[
              {
                key: 'Modes',
                value: capabilityData?.modes.length ? (
                  <span className="row row--tight" style={{ flexWrap: 'wrap' }}>
                    {capabilityData.modes.map((mode) => (
                      <CapabilityBadge key={mode.key} ok={mode.supported} label={mode.label || mode.key} />
                    ))}
                  </span>
                ) : (
                  record.modes.join(', ') || 'none declared'
                ),
              },
              {
                key: 'Aspect ratios',
                value: (resolved.aspect_ratios ?? []).join(', ') || 'none declared',
              },
              {
                key: 'Resolutions',
                value: (resolved.resolutions ?? []).join(', ') || 'none declared',
              },
              {
                key: 'Durations',
                value: (resolved.durations_seconds ?? []).map((value) => `${value}s`).join(', ') || 'none declared',
              },
              {
                key: 'Frame rates',
                value: (resolved.frame_rates ?? []).map((value) => `${value} fps`).join(', ') || 'none declared',
              },
              {
                key: 'Reference roles',
                value: capabilityData?.reference_roles.length
                  ? capabilityData.reference_roles.map((role) => role.label || role.key).join(', ')
                  : (resolved.reference_roles ?? []).join(', ') || 'none declared',
              },
              { key: 'Max reference images', value: resolved.max_reference_images ?? 'not declared' },
              { key: 'Max outputs', value: resolved.max_outputs ?? 'not declared' },
              { key: 'Max input bytes', value: resolved.max_input_bytes ?? 'not declared' },
            ]}
          />
          <div className="stack stack--tight">
            <div className="row row--tight" style={{ flexWrap: 'wrap' }}>
              <CapabilityBadge ok={resolved.supports_negative_prompt === true} label="negative prompt" />
              <CapabilityBadge ok={resolved.supports_seed === true} label="seed" />
              <CapabilityBadge ok={resolved.supports_mask === true} label="mask" />
              <CapabilityBadge ok={resolved.supports_audio === true} label="audio" />
            </div>

            <div className="field">
              <span className="field__label">Camera moves</span>
              {capabilityData?.camera_moves.length ? (
                <div className="stack stack--tight">
                  {capabilityData.camera_moves.map((move) => (
                    <div key={move.key} className="row row--between">
                      <span>{move.label || move.key}</span>
                      <Badge tone={move.is_native ? 'ok' : 'warn'}>
                        {move.is_native ? 'native camera control' : 'prompt wording only'}
                      </Badge>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="dim" style={{ margin: 0, fontSize: 'var(--fs-xs)' }}>
                  No camera move is declared, so the video studio disables the control.
                </p>
              )}
            </div>

            {resolved.restrictions?.length ? (
              <div className="field">
                <span className="field__label">Restrictions</span>
                <ul className="muted" style={{ margin: 0, paddingLeft: 'var(--s4)' }}>
                  {resolved.restrictions.map((restriction) => (
                    <li key={restriction}>{restriction}</li>
                  ))}
                </ul>
              </div>
            ) : null}

            {resolved.notes ? (
              <p className="muted" style={{ margin: 0 }}>
                {resolved.notes}
              </p>
            ) : null}
          </div>
        </div>
      </Section>

      <Section title="Parameter schema">
        {parameters.loading ? <p className="muted" style={{ margin: 0 }}>Reading the schema…</p> : null}
        {parameters.error ? <ErrorNotice error={parameters.error} onRetry={parameters.reload} /> : null}
        {(() => {
          const specs = parameters.data?.schema.parameters ?? record.parameter_schema.parameters;
          if (!specs.length) {
            return (
              <p className="muted" style={{ margin: 0 }}>
                This model declares no tunable parameters. The studios will send the prompt alone.
              </p>
            );
          }
          return (
            <div className="table-wrap">
              <table className="table table--compact">
                <thead>
                  <tr>
                    <th>Parameter</th>
                    <th>Type</th>
                    <th>Default</th>
                    <th>Range / options</th>
                    <th>Notes</th>
                  </tr>
                </thead>
                <tbody>
                  {specs.map((spec) => (
                    <tr key={spec.name}>
                      <td>
                        <span className="mono">{spec.name}</span>
                        {spec.required ? <Badge tone="warn">required</Badge> : null}
                      </td>
                      <td className="mono">{spec.type}</td>
                      <td className="mono">{spec.default === undefined ? '—' : JSON.stringify(spec.default)}</td>
                      <td className="dim">
                        {spec.options?.length
                          ? spec.options.join(', ')
                          : spec.minimum !== undefined || spec.maximum !== undefined
                            ? `${spec.minimum ?? '−∞'} … ${spec.maximum ?? '∞'} step ${spec.step ?? 1}`
                            : '—'}
                      </td>
                      <td className="dim">
                        {[
                          spec.label,
                          spec.prompt_mapped ? 'mapped from the prompt' : null,
                          spec.api_key ? 'server-side credential' : null,
                          spec.advanced ? 'advanced' : null,
                        ]
                          .filter(Boolean)
                          .join(' · ') || '—'}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          );
        })()}
      </Section>

      <Section
        title="What this model does to your prompt"
        description="Read from the layer dataset, joined on the endpoint identifier. No entry here means no document in this repository describes the endpoint — a gap in what we know, not a promise that the provider adds nothing."
        actions={
          promptFacts?.prompt_model ? (
            <Link
              to={`/prompt?model=${encodeURIComponent(promptFacts.prompt_model.slug)}`}
              className="btn btn--sm"
            >
              Open in Prompt Lab
            </Link>
          ) : null
        }
      >
        {catalog.loading ? <p className="muted" style={{ margin: 0 }}>Reading the layer dataset…</p> : null}
        {catalog.error ? <ErrorNotice error={catalog.error} onRetry={catalog.reload} /> : null}
        {catalog.data && !promptFacts ? (
          <p className="muted" style={{ margin: 0 }}>
            <span className="mono">{record.model_key}</span> is not in the documented catalogue. The Prompt Lab will
            assemble the generic layers for it and say they are unconfirmed.
          </p>
        ) : null}
        {promptFacts && !promptFacts.prompt_model ? (
          <p className="muted" style={{ margin: 0 }}>
            The endpoint is in the catalogue, but no layer document covers its prompt behaviour. The studio will not
            guess: preflight reports this endpoint as undocumented.
          </p>
        ) : null}
        {promptFacts?.prompt_model ? (
          <div className="stack stack--tight">
            <KeyValueList
              rows={[
                {
                  key: 'Documented layers',
                  value: `${promptFacts.prompt_model.active_layer_count} active of ${promptFacts.prompt_model.layer_count}`,
                },
                { key: 'Dataset entry', value: <span className="mono">{promptFacts.prompt_model.slug}</span> },
                { key: 'Endpoint', value: <span className="mono">{promptFacts.endpoint}</span> },
              ]}
            />
            {promptFacts.prompt_model.layers.map((layer) => (
              <div className="card card--sunken" key={layer.id} style={{ display: 'grid', gap: 4 }}>
                <div className="row row--tight" style={{ flexWrap: 'wrap' }}>
                  <Badge tone={layer.active ? 'warn' : 'plain'}>{layer.active ? layer.kind : `off · ${layer.kind}`}</Badge>
                  <span className="mono" style={{ fontSize: 'var(--fs-xs)' }}>
                    {layer.id}
                  </span>
                </div>
                <p style={{ margin: 0 }}>{layer.detail}</p>
                {layer.disable_with ? (
                  <p className="field__hint" style={{ margin: 0 }}>
                    Off switch:{' '}
                    <span className="mono">
                      {Object.entries(layer.disable_with)
                        .map(([key, value]) => `${key}=${JSON.stringify(value)}`)
                        .join(', ')}
                    </span>
                  </p>
                ) : null}
              </div>
            ))}
            {promptFacts.restrictions.length ? (
              <div className="panel panel--warn">
                <p className="panel__title">Restrictions this endpoint documents</p>
                <ul style={{ margin: 0, paddingLeft: 18 }}>
                  {promptFacts.restrictions.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
            ) : null}
          </div>
        ) : null}
      </Section>

      <Section title="Raw records">
        <CodeBlock code={JSON.stringify(record.defaults, null, 2)} label="defaults" />
        <CodeBlock code={JSON.stringify(record.limits, null, 2)} label="limits" />
      </Section>
    </div>
  );
}
