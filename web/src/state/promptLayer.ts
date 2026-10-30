import { prompt as promptApi } from '../api/endpoints.ts';
import type {
  ModeVocabulary,
  PromptCatalogEntry,
  PromptModel,
  PromptModelSummary,
} from '../api/types.ts';
import { useQuery, type QueryState } from './query.ts';

/**
 * The layer dataset, read through the studio.
 *
 * Everything here is a read of the server's own words: which models have
 * documented facts, what those facts are, and which endpoints this platform can
 * drive on a user's key. Nothing in this module decides that a model adds
 * nothing -- an absence of facts is returned as an absence and rendered as one.
 */

export function useModeVocabulary(): QueryState<ModeVocabulary> {
  return useQuery<ModeVocabulary>('prompt:modes', () => promptApi.modes(), { staleMs: 600_000 });
}

export interface PromptModelList {
  models: PromptModelSummary[];
  datasetVersion: string;
}

export function usePromptModels(kind?: 'image' | 'video' | 'chat'): QueryState<PromptModelList> {
  return useQuery<PromptModelList>(
    `prompt:models:${kind ?? 'all'}`,
    () => promptApi.models(kind),
    { staleMs: 120_000 },
  );
}

export function usePromptModel(slug: string | null): QueryState<PromptModel> {
  return useQuery<PromptModel>(
    slug ? `prompt:model:${slug}` : null,
    () => promptApi.model(slug as string),
    { staleMs: 120_000 },
  );
}

export interface PromptCatalog {
  endpoints: PromptCatalogEntry[];
  documented: number;
  undocumented: number;
  source: string;
}

export function usePromptCatalog(modality?: 'image' | 'video'): QueryState<PromptCatalog> {
  return useQuery<PromptCatalog>(
    `prompt:catalog:${modality ?? 'all'}`,
    () => promptApi.catalog(modality),
    { staleMs: 120_000 },
  );
}

/** The documented endpoint that matches a registered model's key, if any. */
export function findCatalogEntry(
  catalog: PromptCatalog | undefined,
  modelKey: string,
): PromptCatalogEntry | null {
  if (!catalog) return null;
  return catalog.endpoints.find((entry) => entry.endpoint === modelKey) ?? null;
}
