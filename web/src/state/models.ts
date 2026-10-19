import { useMemo } from 'react';
import { connections, models as modelApi } from '../api/endpoints.ts';
import type { Connection, Model, ModelCapabilitiesResponse, ModelParametersResponse, Modality } from '../api/types.ts';
import { useQuery, type QueryState } from './query.ts';

/**
 * The model registry, read once and shared. Capabilities always come from the
 * backend; nothing here decides on its own that a mode is available.
 */

export interface ModelFilters {
  modality?: Modality | '';
  source?: string;
  favorite?: boolean;
  enabled?: boolean;
  q?: string;
}

export function modelFilterKey(filters: ModelFilters): string {
  const parts = Object.entries(filters)
    .filter(([, value]) => value !== undefined && value !== '' && value !== false)
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([key, value]) => `${key}=${String(value)}`);
  return `models?${parts.join('&')}`;
}

export function useModels(filters: ModelFilters = {}): QueryState<Model[]> {
  const key = modelFilterKey(filters);
  return useQuery<Model[]>(key, () => modelApi.list(filters), { staleMs: 10_000 });
}

export function useConnections(): QueryState<Connection[]> {
  return useQuery<Connection[]>('connections', () => connections.list(), { staleMs: 10_000 });
}

export function useModelCapabilities(modelId: string | null): QueryState<ModelCapabilitiesResponse> {
  return useQuery<ModelCapabilitiesResponse>(
    modelId ? `model:${modelId}:capabilities` : null,
    () => modelApi.capabilities(modelId as string),
    { staleMs: 60_000 },
  );
}

export function useModelParameters(modelId: string | null): QueryState<ModelParametersResponse> {
  return useQuery<ModelParametersResponse>(
    modelId ? `model:${modelId}:parameters` : null,
    () => modelApi.parameters(modelId as string),
    { staleMs: 60_000 },
  );
}

export function useModel(modelId: string | null): QueryState<Model> {
  return useQuery<Model>(modelId ? `model:${modelId}` : null, () => modelApi.get(modelId as string), {
    staleMs: 30_000,
  });
}

export interface ModelOptionGroup {
  label: string;
  options: Model[];
}

/** Models grouped by provider so the picker never loses the connection name. */
export function groupByProvider(models: Model[]): ModelOptionGroup[] {
  const groups = new Map<string, Model[]>();
  for (const model of models) {
    const label = `${model.provider.adapter_label || model.provider.adapter_type} · ${
      model.provider.connection_name || 'connection'
    }${model.provider.is_mock ? ' (local development placeholder)' : ''}`;
    const list = groups.get(label);
    if (list) list.push(model);
    else groups.set(label, [model]);
  }
  return [...groups.entries()]
    .map(([label, options]) => ({ label, options }))
    .sort((a, b) => a.label.localeCompare(b.label));
}

/**
 * Mode support, read straight from the model's declared modes. A mode that the
 * model did not declare is "unsupported", never "probably fine".
 */
export function modeSupport(model: Model | null, capabilities: ModelCapabilitiesResponse | null) {
  const declared = new Set<string>([
    ...(model?.modes ?? []),
    ...(model?.capabilities.modes ?? []),
  ]);
  const listed = capabilities?.modes ?? [];
  return {
    isSupported: (mode: string): boolean => {
      if (listed.length) {
        const entry = listed.find((item) => item.key === mode);
        if (entry) return entry.supported;
      }
      return declared.has(mode);
    },
    reason: (mode: string): string =>
      `The model profile for this model does not declare the "${mode}" mode, so the control is disabled.`,
    labels: new Map(listed.map((item) => [item.key, item.label])),
  };
}

/** A compact, honest one-line summary of what a model can do. */
export function capabilitySummary(model: Model): string {
  const capabilities = model.capabilities;
  const parts: string[] = [];
  const modes = capabilities.modes ?? [];
  const ratios = capabilities.aspect_ratios ?? [];
  const durations = capabilities.durations_seconds ?? [];
  if (modes.length) parts.push(modes.join(', '));
  if (ratios.length) parts.push(`${ratios.length} aspect ratios`);
  if (durations.length) parts.push(`${durations.length} durations`);
  if (capabilities.supports_seed) parts.push('seed');
  if (capabilities.supports_negative_prompt) parts.push('negative prompt');
  if (capabilities.supports_mask) parts.push('mask');
  return parts.length ? parts.join(' · ') : 'The model profile lists no capabilities.';
}

export function useDefaultModelId(): {
  model: Model | null;
  loading: boolean;
  error: unknown;
} {
  const state = useModels({ enabled: true });
  const preferred = useMemo(() => {
    const list = state.data ?? [];
    return list.find((model) => model.is_default) ?? list.find((model) => model.is_favorite) ?? list[0] ?? null;
  }, [state.data]);
  return { model: preferred, loading: state.loading, error: state.error };
}
