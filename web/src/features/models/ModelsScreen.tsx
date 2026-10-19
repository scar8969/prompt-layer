import { useMemo, useState } from 'react';
import type { Model } from '../../api/types.ts';
import { models as modelApi } from '../../api/endpoints.ts';
import { MockBadge } from '../../components/Badge.tsx';
import { Badge } from '../../components/Badge.tsx';
import { Button } from '../../components/Button.tsx';
import { FilterBar, Section } from '../../components/Data.tsx';
import { Select, TextInput } from '../../components/Field.tsx';
import { EmptyState, ErrorState, LoadingState } from '../../components/State.tsx';
import { Menu } from '../../components/Controls.tsx';
import { Icon } from '../../components/Icon.tsx';
import { Link } from '../../app/router.tsx';
import { capabilitySummary, useModels } from '../../state/models.ts';
import { invalidate } from '../../state/query.ts';
import { useSettings } from '../../state/settings.tsx';
import { useToasts } from '../../state/toasts.tsx';
import { formatDateTime } from '../../lib/util.ts';

/**
 * The model registry. Every row is a model record the backend holds, with the
 * capability profile attached to it. The placeholder badge comes straight from
 * provider.is_mock.
 */
export function ModelsScreen() {
  const toasts = useToasts();
  const settings = useSettings();
  const [modality, setModality] = useState('');
  const [enableFilter, setEnableFilter] = useState('');
  const [query, setQuery] = useState('');
  const [applied, setApplied] = useState('');
  const [nonce, setNonce] = useState(0);

  const models = useModels({
    modality: modality === 'image' || modality === 'video' ? modality : undefined,
    enabled: enableFilter === 'enabled' ? true : enableFilter === 'disabled' ? false : undefined,
    q: applied || undefined,
  });

  const list = models.data ?? [];
  const grouped = useMemo(() => {
    const map = new Map<string, Model[]>();
    for (const model of list) {
      const key = `${model.provider.adapter_label} · ${model.provider.connection_name}`;
      const bucket = map.get(key);
      if (bucket) bucket.push(model);
      else map.set(key, [model]);
    }
    return [...map.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [list]);

  const patch = async (model: Model, input: Parameters<typeof modelApi.patch>[1], label: string) => {
    try {
      await modelApi.patch(model.id, input);
      invalidate('models');
      models.reload();
      setNonce((n) => n + 1);
      toasts.push({ tone: 'success', title: label, text: model.display_name });
    } catch (err) {
      toasts.push({
        tone: 'error',
        title: 'That change was rejected',
        text: err instanceof Error ? err.message : 'Unknown error',
      });
    }
  };

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__heading">Models</h1>
          <p className="page__subtitle">
            Every model this workspace can call, with the capability profile the backend stores for it. What a model
            declares here is exactly what the studios are allowed to enable.
          </p>
        </div>
        <div className="page__actions">
          <Button size="sm" onClick={models.reload}>
            Refresh
          </Button>
          <Link to="/connections" className="btn btn--sm btn--primary">
            <span className="btn__icon">
              <Icon name="plus" size={13} />
            </span>
            Register a model
          </Link>
        </div>
      </div>

      <FilterBar label="Filter models">
        <div className="toolbar__group" style={{ flex: 1, minWidth: 200 }}>
          <TextInput
            label="Search models"
            type="search"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Enter') setApplied(query.trim());
            }}
            placeholder="Name or model key, then press Enter"
          />
        </div>
        <div className="toolbar__group">
          <Select
            label="Modality"
            value={modality}
            onChange={(event) => setModality(event.target.value)}
            options={[
              { value: '', label: 'Images and video' },
              { value: 'image', label: 'Images' },
              { value: 'video', label: 'Video' },
            ]}
          />
        </div>
        <div className="toolbar__group">
          <Select
            label="State"
            value={enableFilter}
            onChange={(event) => setEnableFilter(event.target.value)}
            options={[
              { value: '', label: 'Enabled and disabled' },
              { value: 'enabled', label: 'Enabled only' },
              { value: 'disabled', label: 'Disabled only' },
            ]}
          />
        </div>
        <div className="toolbar__spacer" />
        <Button size="sm" onClick={() => setApplied(query.trim())}>
          Search
        </Button>
      </FilterBar>

      {models.loading ? <LoadingState label="Reading the model registry…" /> : null}
      {models.error ? <ErrorState error={models.error} onRetry={models.reload} /> : null}

      {!models.loading && !models.error && !list.length ? (
        <EmptyState
          icon="chip"
          title="No model matches this view"
          body="Models are registered on a connection. Add one there, or clear the filters above."
          action={
            <Link to="/connections" className="btn btn--primary">
              Open API Connections
            </Link>
          }
        />
      ) : null}

      {grouped.map(([provider, entries]) => (
        <Section key={provider} title={provider}>
          <div className="table-wrap">
            <table className="table">
              <thead>
                <tr>
                  <th>Model</th>
                  <th>Modality</th>
                  <th>Modes</th>
                  <th>Capabilities</th>
                  <th>State</th>
                  <th>Updated</th>
                  <th style={{ width: 140 }}>Actions</th>
                </tr>
              </thead>
              <tbody>
                {entries.map((model) => (
                  <tr key={model.id}>
                    <td>
                      <div className="stack stack--tight">
                        <Link to={`/models/${model.id}`}>{model.display_name}</Link>
                        <span className="mono dim" style={{ fontSize: 'var(--fs-xs)' }}>
                          {model.model_key}
                        </span>
                        <span className="row row--tight">
                          <MockBadge isMock={model.provider.is_mock} />
                          {model.is_default ? <Badge tone="accent">default</Badge> : null}
                          {model.is_favorite ? <Badge tone="ok">favourite</Badge> : null}
                          {!model.provider.connection_enabled ? <Badge tone="warn">connection disabled</Badge> : null}
                        </span>
                      </div>
                    </td>
                    <td className="mono">{model.modality}</td>
                    <td>
                      <span className="row row--tight" style={{ flexWrap: 'wrap' }}>
                        {model.modes.length ? (
                          model.modes.map((mode) => (
                            <Badge key={mode} tone="plain">
                              {mode}
                            </Badge>
                          ))
                        ) : (
                          <span className="dim">none declared</span>
                        )}
                      </span>
                    </td>
                    <td className="dim" style={{ fontSize: 'var(--fs-xs)' }}>
                      {capabilitySummary(model)}
                    </td>
                    <td>
                      <Badge tone={model.is_enabled ? 'ok' : 'plain'}>
                        {model.is_enabled ? 'enabled' : 'disabled'}
                      </Badge>
                    </td>
                    <td className="dim" style={{ fontSize: 'var(--fs-xs)' }}>
                      {formatDateTime(model.updated_at)}
                    </td>
                    <td>
                      <Menu
                        label="Manage"
                        items={[
                          {
                            label: model.is_enabled ? 'Disable' : 'Enable',
                            onSelect: () =>
                              void patch(model, { is_enabled: !model.is_enabled }, model.is_enabled ? 'Model disabled' : 'Model enabled'),
                          },
                          {
                            label: model.is_favorite ? 'Remove favourite' : 'Favourite',
                            onSelect: () => void patch(model, { is_favorite: !model.is_favorite }, 'Favourite updated'),
                          },
                          {
                            label: 'Make default',
                            onSelect: () => {
                              settings.setDefaultModelId(model.id);
                              toasts.push({
                                tone: 'success',
                                title: 'Default model set',
                                text: `Studios will preselect ${model.display_name}.`,
                              });
                            },
                            hint: 'Stored as a local preference only.',
                          },
                          {
                            label: 'Open capability profile',
                            onSelect: () => {
                              window.location.hash = `#/models/${model.id}`;
                            },
                          },
                        ]}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>
      ))}
      <span className="visually-hidden" role="status">
        {nonce > 0 ? 'Model registry updated' : ''}
      </span>
    </div>
  );
}
