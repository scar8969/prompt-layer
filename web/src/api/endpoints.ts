import { ApiError, apiRequest, envelope } from './client.ts';
import type {
  Asset,
  AssetListResponse,
  AuthResponse,
  BillingInfo,
  CameraMoveOption,
  Capabilities,
  Character,
  CharacterReference,
  Connection,
  ConnectionCreateInput,
  ConnectionPatchInput,
  ConnectionTestResult,
  EditGenerationInput,
  ExtendGenerationInput,
  ImageGenerationInput,
  Job,
  JobEvent,
  JobListResponse,
  MeResponse,
  Model,
  ModelCapabilitiesResponse,
  ModelInput,
  ModelParametersResponse,
  ModelPatchInput,
  Preset,
  Project,
  PromptImprovement,
  PromptRecord,
  ReferenceRoleOption,
  SystemStatus,
  TransformGenerationInput,
  UsageResponse,
  User,
  VariationsInput,
  VideoGenerationInput,
  Workspace,
  WorkspaceSummary,
} from './types.ts';
import { arr, bool, isRecord, num, str, strList } from '../lib/util.ts';

/* ------------------------------------------------------------ normalisers */
/* The backend is the source of truth; these readers only coerce the JSON that
   arrived into the documented shape and never fabricate a missing field.      */

function readUser(raw: unknown): User {
  const r = isRecord(raw) ? raw : {};
  return {
    id: str(r.id) ?? '',
    email: str(r.email) ?? '',
    display_name: str(r.display_name) ?? str(r.email) ?? 'Unknown user',
    created_at: str(r.created_at) ?? undefined,
    is_active: bool(r.is_active) ?? undefined,
  };
}

function readWorkspace(raw: unknown): Workspace {
  const r = isRecord(raw) ? raw : {};
  return {
    id: str(r.id) ?? '',
    name: str(r.name) ?? 'Workspace',
    slug: str(r.slug) ?? '',
    role: (str(r.role) as Workspace['role']) ?? undefined,
    created_at: str(r.created_at) ?? undefined,
  };
}

function readBilling(raw: unknown): BillingInfo {
  const r = isRecord(raw) ? raw : {};
  return {
    mode: str(r.mode) ?? 'byok',
    platform_credits: bool(r.platform_credits) ?? false,
    note: str(r.note) ?? '',
  };
}

export function readCapabilities(raw: unknown): Capabilities {
  const r = isRecord(raw) ? raw : {};
  return {
    modality: str(r.modality) ?? undefined,
    modes: strList(r.modes),
    aspect_ratios: strList(r.aspect_ratios),
    resolutions: strList(r.resolutions),
    durations_seconds: arr(r.durations_seconds).filter((v): v is number => typeof v === 'number'),
    frame_rates: arr(r.frame_rates).filter((v): v is number => typeof v === 'number'),
    reference_roles: strList(r.reference_roles),
    max_reference_images: num(r.max_reference_images),
    max_outputs: num(r.max_outputs),
    supports_negative_prompt: bool(r.supports_negative_prompt) ?? undefined,
    supports_seed: bool(r.supports_seed) ?? undefined,
    supports_audio: bool(r.supports_audio) ?? undefined,
    supports_mask: bool(r.supports_mask) ?? undefined,
    native_camera_moves: strList(r.native_camera_moves),
    restrictions: strList(r.restrictions),
    notes: str(r.notes),
    max_input_bytes: num(r.max_input_bytes),
  };
}

export function readModel(raw: unknown): Model {
  const r = isRecord(raw) ? raw : {};
  const providerRaw = isRecord(r.provider) ? r.provider : {};
  const schemaRaw = isRecord(r.parameter_schema) ? r.parameter_schema : {};
  return {
    id: str(r.id) ?? '',
    connection_id: str(r.connection_id) ?? '',
    workspace_id: str(r.workspace_id) ?? '',
    model_key: str(r.model_key) ?? '',
    display_name: str(r.display_name) ?? str(r.model_key) ?? 'Unnamed model',
    modality: str(r.modality) ?? 'image',
    modes: strList(r.modes),
    capabilities: readCapabilities(r.capabilities),
    parameter_schema: { parameters: arr(schemaRaw.parameters).filter(isRecord) as never },
    defaults: isRecord(r.defaults) ? r.defaults : {},
    limits: isRecord(r.limits) ? r.limits : {},
    source: str(r.source) ?? 'user',
    is_enabled: bool(r.is_enabled) ?? true,
    is_favorite: bool(r.is_favorite) ?? false,
    is_default: bool(r.is_default) ?? false,
    created_at: str(r.created_at) ?? '',
    updated_at: str(r.updated_at) ?? '',
    provider: {
      connection_name: str(providerRaw.connection_name) ?? 'Unknown connection',
      adapter_type: str(providerRaw.adapter_type) ?? '',
      adapter_label: str(providerRaw.adapter_label) ?? str(providerRaw.adapter_type) ?? 'Unknown adapter',
      base_url: str(providerRaw.base_url),
      is_mock: bool(providerRaw.is_mock) ?? false,
      connection_enabled: bool(providerRaw.connection_enabled) ?? true,
    },
  };
}

function readConnection(raw: unknown): Connection {
  const r = isRecord(raw) ? raw : {};
  const credentialRaw = isRecord(r.credential) ? r.credential : {};
  return {
    id: str(r.id) ?? '',
    name: str(r.name) ?? 'Unnamed connection',
    adapter_type: str(r.adapter_type) ?? '',
    adapter_label: str(r.adapter_label) ?? str(r.adapter_type) ?? 'Unknown adapter',
    base_url: str(r.base_url),
    auth_scheme: str(r.auth_scheme),
    model_count: num(r.model_count) ?? 0,
    is_enabled: bool(r.is_enabled) ?? true,
    last_test_status: str(r.last_test_status),
    last_test_at: str(r.last_test_at),
    last_used_at: str(r.last_used_at),
    created_at: str(r.created_at) ?? '',
    credential: {
      present: bool(credentialRaw.present) ?? false,
      last4: str(credentialRaw.last4),
      key_id: str(credentialRaw.key_id),
      fingerprint: str(credentialRaw.fingerprint),
    },
    extra_header_names: strList(r.extra_header_names),
  };
}

function readAdapter(raw: unknown): SystemStatus['adapters'][number] {
  const r = isRecord(raw) ? raw : {};
  return {
    slug: str(r.slug) ?? '',
    label: str(r.label) ?? str(r.slug) ?? 'Unknown adapter',
    summary: str(r.summary) ?? '',
    is_mock: bool(r.is_mock) ?? false,
    discovers_models: bool(r.discovers_models) ?? false,
    supports_cancel: bool(r.supports_cancel) ?? undefined,
    auth_schemes: strList(r.auth_schemes),
    default_modes: strList(r.default_modes),
  };
}

function readSystemStatus(raw: unknown): SystemStatus {
  const r = isRecord(raw) ? raw : {};
  return {
    version: str(r.version) ?? 'unknown',
    environment: str(r.environment) ?? 'unknown',
    time_utc: str(r.time_utc) ?? '',
    database: str(r.database) ?? str(r.database_backend) ?? 'unknown',
    storage: str(r.storage) ?? str(r.storage_backend) ?? 'unknown',
    adapters: arr(r.adapters).map(readAdapter),
    mock_provider_enabled: bool(r.mock_provider_enabled) ?? false,
    billing: readBilling(r.billing),
    missing_configuration: strList(r.missing_configuration),
    parameter_types: strList(r.parameter_types),
    camera_moves: strList(r.camera_moves),
    reference_roles: strList(r.reference_roles),
    database_backend: str(r.database_backend) ?? undefined,
    storage_backend: str(r.storage_backend) ?? undefined,
  };
}

export function readAsset(raw: unknown): Asset {
  const r = isRecord(raw) ? raw : {};
  const id = str(r.id) ?? '';
  return {
    id,
    workspace_id: str(r.workspace_id) ?? '',
    project_id: str(r.project_id),
    job_id: str(r.job_id),
    kind: str(r.kind) ?? 'upload',
    name: str(r.name) ?? 'Untitled asset',
    content_type: str(r.content_type) ?? 'application/octet-stream',
    byte_size: num(r.byte_size) ?? 0,
    width: num(r.width),
    height: num(r.height),
    duration_seconds: num(r.duration_seconds),
    is_favorite: bool(r.is_favorite) ?? false,
    is_mock: bool(r.is_mock) ?? false,
    content_url: str(r.content_url) ?? `/api/assets/${id}/content`,
    thumbnail_url: str(r.thumbnail_url) ?? (id ? `/api/assets/${id}/thumbnail` : null),
    created_at: str(r.created_at) ?? '',
    source: str(r.source) ?? 'uploaded',
  };
}

export function readOutput(raw: unknown): Job['outputs'][number] {
  const r = isRecord(raw) ? raw : {};
  const id = str(r.id) ?? '';
  const assetId = str(r.asset_id);
  return {
    id,
    asset_id: assetId,
    url: str(r.url) ?? (assetId ? `/api/assets/${assetId}/content` : null),
    content_type: str(r.content_type),
    width: num(r.width),
    height: num(r.height),
    duration_seconds: num(r.duration_seconds),
    file_name: str(r.file_name),
    byte_size: num(r.byte_size),
    seed: num(r.seed),
    metadata: isRecord(r.metadata) ? r.metadata : null,
    is_mock: bool(r.is_mock) ?? false,
  };
}

export function readJob(raw: unknown): Job {
  const r = isRecord(raw) ? raw : {};
  const modelRaw = isRecord(r.model) ? r.model : null;
  const modelProviderRaw = modelRaw && isRecord(modelRaw.provider) ? modelRaw.provider : {};
  const costRaw = isRecord(r.cost_estimate) ? r.cost_estimate : null;
  const errorRaw = isRecord(r.error) ? r.error : null;
  return {
    id: str(r.id) ?? '',
    workspace_id: str(r.workspace_id) ?? '',
    project_id: str(r.project_id),
    model_id: str(r.model_id),
    model: modelRaw
      ? {
          model_key: str(modelRaw.model_key) ?? '',
          display_name: str(modelRaw.display_name) ?? str(modelRaw.model_key) ?? 'Unknown model',
          modality: str(modelRaw.modality) ?? '',
          provider: {
            connection_name: str(modelProviderRaw.connection_name) ?? '',
            adapter_label: str(modelProviderRaw.adapter_label) ?? '',
            is_mock: bool(modelProviderRaw.is_mock) ?? false,
          },
        }
      : null,
    mode: str(r.mode) ?? '',
    kind: str(r.kind) ?? '',
    state: str(r.state) ?? 'queued',
    prompt: str(r.prompt),
    negative_prompt: str(r.negative_prompt),
    parameters: isRecord(r.parameters) ? r.parameters : null,
    references: arr(r.references).filter(isRecord).map((ref) => ({
      asset_id: str(ref.asset_id) ?? undefined,
      url: str(ref.url) ?? undefined,
      role: str(ref.role) ?? 'reference',
    })),
    seed: num(r.seed),
    count: num(r.count),
    provider_job_id: str(r.provider_job_id),
    provider_status: str(r.provider_status),
    progress: num(r.progress),
    progress_is_estimate: bool(r.progress_is_estimate) ?? false,
    message: str(r.message),
    error: errorRaw
      ? {
          code: str(errorRaw.code) ?? 'internal',
          message: str(errorRaw.message) ?? '',
          hint: str(errorRaw.hint),
          details: isRecord(errorRaw.details) ? errorRaw.details : null,
        }
      : null,
    attempt: num(r.attempt),
    max_attempts: num(r.max_attempts),
    is_mock: bool(r.is_mock) ?? false,
    cost_estimate: costRaw
      ? {
          currency: str(costRaw.currency),
          amount: num(costRaw.amount),
          is_estimate: bool(costRaw.is_estimate) ?? false,
          is_billable: bool(costRaw.is_billable) ?? false,
          note: str(costRaw.note),
          breakdown: costRaw.breakdown ?? null,
        }
      : null,
    outputs: arr(r.outputs).map(readOutput),
    created_at: str(r.created_at) ?? '',
    started_at: str(r.started_at),
    finished_at: str(r.finished_at),
    elapsed_ms: num(r.elapsed_ms),
    parent_job_id: str(r.parent_job_id),
    relation: str(r.relation),
  };
}

function readProject(raw: unknown): Project {
  const r = isRecord(raw) ? raw : {};
  return {
    id: str(r.id) ?? '',
    workspace_id: str(r.workspace_id) ?? '',
    name: str(r.name) ?? 'Untitled project',
    description: str(r.description),
    asset_count: num(r.asset_count),
    job_count: num(r.job_count),
    is_favorite: bool(r.is_favorite) ?? false,
    is_archived: bool(r.is_archived) ?? false,
    created_at: str(r.created_at) ?? '',
    updated_at: str(r.updated_at) ?? undefined,
  };
}

function readPreset(raw: unknown): Preset {
  const r = isRecord(raw) ? raw : {};
  return {
    id: str(r.id) ?? '',
    workspace_id: str(r.workspace_id) ?? '',
    name: str(r.name) ?? 'Untitled preset',
    description: str(r.description),
    modality: str(r.modality) ?? 'image',
    category: str(r.category),
    prompt_template: str(r.prompt_template),
    negative_prompt_template: str(r.negative_prompt_template),
    aspect_ratio: str(r.aspect_ratio),
    model_preference: str(r.model_preference),
    parameters: isRecord(r.parameters) ? r.parameters : null,
    camera: isRecord(r.camera) ? r.camera : null,
    notes: str(r.notes),
    thumbnail_url: str(r.thumbnail_url),
    is_favorite: bool(r.is_favorite) ?? false,
    created_at: str(r.created_at) ?? '',
    updated_at: str(r.updated_at) ?? undefined,
  };
}

function readPrompt(raw: unknown): PromptRecord {
  const r = isRecord(raw) ? raw : {};
  return {
    id: str(r.id) ?? '',
    workspace_id: str(r.workspace_id) ?? undefined,
    text: str(r.text) ?? '',
    title: str(r.title),
    negative_prompt: str(r.negative_prompt),
    model_id: str(r.model_id),
    tags: strList(r.tags).length ? strList(r.tags) : null,
    variables: isRecord(r.variables) ? r.variables : null,
    usage_count: num(r.usage_count),
    created_at: str(r.created_at) ?? '',
    updated_at: str(r.updated_at) ?? undefined,
  };
}

function readCharacter(raw: unknown): Character {
  const r = isRecord(raw) ? raw : {};
  const attributes: Record<string, string> = {};
  if (isRecord(r.attributes)) {
    for (const [key, value] of Object.entries(r.attributes)) {
      const text = str(value);
      if (text) attributes[key] = text;
    }
  }
  return {
    id: str(r.id) ?? '',
    workspace_id: str(r.workspace_id) ?? '',
    name: str(r.name) ?? 'Unnamed character',
    description: str(r.description),
    attributes: Object.keys(attributes).length ? attributes : null,
    default_prompt: str(r.default_prompt),
    preferred_model_id: str(r.preferred_model_id),
    tags: strList(r.tags).length ? strList(r.tags) : null,
    reference_asset_ids: strList(r.reference_asset_ids).length ? strList(r.reference_asset_ids) : null,
    reference_count: num(r.reference_count),
    created_at: str(r.created_at) ?? '',
    updated_at: str(r.updated_at) ?? undefined,
  };
}

/* ------------------------------------------------------------------- auth */

export const auth = {
  async register(input: {
    email: string;
    password: string;
    display_name: string;
  }): Promise<AuthResponse> {
    const payload = await apiRequest<unknown>('/api/auth/register', {
      method: 'POST',
      body: input,
    });
    return {
      user: readUser(envelope(payload, 'user')),
      workspace: readWorkspace(envelope(payload, 'workspace')),
      session: {
        csrf_token: str(isRecord(payload) && isRecord(payload.session) ? payload.session.csrf_token : null) ?? '',
      },
    };
  },

  async login(input: { email: string; password: string }): Promise<AuthResponse> {
    const payload = await apiRequest<unknown>('/api/auth/login', { method: 'POST', body: input });
    return {
      user: readUser(envelope(payload, 'user')),
      workspace: readWorkspace(envelope(payload, 'workspace')),
      session: {
        csrf_token: str(isRecord(payload) && isRecord(payload.session) ? payload.session.csrf_token : null) ?? '',
      },
    };
  },

  async logout(): Promise<void> {
    await apiRequest<unknown>('/api/auth/logout', { method: 'POST' });
  },

  async me(): Promise<MeResponse> {
    const payload = await apiRequest<unknown>('/api/auth/me');
    return {
      user: readUser(envelope(payload, 'user')),
      workspace: readWorkspace(envelope(payload, 'workspace')),
      workspaces: arr(envelope(payload, 'workspaces')).map((raw): WorkspaceSummary => {
        const r = isRecord(raw) ? raw : {};
        return {
          id: str(r.id) ?? '',
          name: str(r.name) ?? 'Workspace',
          slug: str(r.slug) ?? '',
          role: (str(r.role) as WorkspaceSummary['role']) ?? 'viewer',
        };
      }),
      permissions: strList(envelope(payload, 'permissions')),
    };
  },

  async changePassword(input: { current_password: string; new_password: string }): Promise<void> {
    await apiRequest<unknown>('/api/auth/change-password', { method: 'POST', body: input });
  },

  async forgotPassword(input: { email: string }): Promise<{ delivery: string | null; resetToken: string | null }> {
    const payload = await apiRequest<unknown>('/api/auth/forgot-password', {
      method: 'POST',
      body: input,
    });
    const r = isRecord(payload) ? payload : {};
    return { delivery: str(r.delivery), resetToken: str(r.reset_token) };
  },

  async resetPassword(input: { token: string; new_password: string }): Promise<void> {
    await apiRequest<unknown>('/api/auth/reset-password', { method: 'POST', body: input });
  },
};

/* ----------------------------------------------------------------- system */

export const system = {
  async status(signal?: AbortSignal): Promise<SystemStatus> {
    const payload = await apiRequest<unknown>('/api/system/status', { signal });
    return readSystemStatus(envelope(payload, 'status'));
  },
};

/* ------------------------------------------------------------ connections */

export const connections = {
  async list(signal?: AbortSignal): Promise<Connection[]> {
    const payload = await apiRequest<unknown>('/api/connections', { signal });
    return arr(envelope(payload, 'connections')).map(readConnection);
  },

  async types(signal?: AbortSignal): Promise<SystemStatus['adapters']> {
    const payload = await apiRequest<unknown>('/api/connections/types', { signal });
    return arr(envelope(payload, 'types')).map(readAdapter);
  },

  async get(id: string, signal?: AbortSignal): Promise<Connection> {
    const payload = await apiRequest<unknown>(`/api/connections/${encodeURIComponent(id)}`, { signal });
    return readConnection(envelope(payload, 'connection'));
  },

  async create(input: ConnectionCreateInput): Promise<{ connection: Connection; models: Model[] }> {
    const payload = await apiRequest<unknown>('/api/connections', { method: 'POST', body: input });
    return {
      connection: readConnection(envelope(payload, 'connection')),
      models: arr(isRecord(payload) ? payload.models : []).map(readModel),
    };
  },

  async patch(id: string, input: ConnectionPatchInput): Promise<Connection> {
    const payload = await apiRequest<unknown>(`/api/connections/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: input,
    });
    return readConnection(envelope(payload, 'connection'));
  },

  async remove(id: string): Promise<{ models_disabled: number; jobs_preserved: number }> {
    const payload = await apiRequest<unknown>(`/api/connections/${encodeURIComponent(id)}`, {
      method: 'DELETE',
    });
    const r = isRecord(payload) ? payload : {};
    return { models_disabled: num(r.models_disabled) ?? 0, jobs_preserved: num(r.jobs_preserved) ?? 0 };
  },

  async test(id: string): Promise<ConnectionTestResult> {
    const payload = await apiRequest<unknown>(`/api/connections/${encodeURIComponent(id)}/test`, {
      method: 'POST',
    });
    const r = isRecord(envelope(payload, 'test')) ? (envelope(payload, 'test') as Record<string, unknown>) : {};
    return {
      outcome: str(r.outcome) ?? 'unknown',
      ok: bool(r.ok) ?? false,
      http_status: num(r.http_status),
      latency_ms: num(r.latency_ms),
      message: str(r.message) ?? '',
      detail: r.detail ?? null,
      models_seen: num(r.models_seen),
      checked_at: str(r.checked_at) ?? '',
    };
  },

  async rotateKey(id: string, credential: string): Promise<Connection> {
    const payload = await apiRequest<unknown>(`/api/connections/${encodeURIComponent(id)}/rotate-key`, {
      method: 'POST',
      body: { credential },
    });
    return readConnection(envelope(payload, 'connection'));
  },

  async models(id: string, signal?: AbortSignal): Promise<Model[]> {
    const payload = await apiRequest<unknown>(`/api/connections/${encodeURIComponent(id)}/models`, {
      signal,
    });
    return arr(envelope(payload, 'models')).map(readModel);
  },

  async addModel(id: string, input: ModelInput): Promise<Model> {
    const payload = await apiRequest<unknown>(`/api/connections/${encodeURIComponent(id)}/models`, {
      method: 'POST',
      body: input,
    });
    return readModel(envelope(payload, 'model'));
  },
};

/* ----------------------------------------------------------------- models */

export interface ModelFilters {
  modality?: string;
  source?: string;
  favorite?: boolean;
  enabled?: boolean;
  q?: string;
}

export const models = {
  async list(filters: ModelFilters = {}, signal?: AbortSignal): Promise<Model[]> {
    const payload = await apiRequest<unknown>('/api/models', {
      signal,
      query: {
        modality: filters.modality,
        source: filters.source,
        favorite: filters.favorite === undefined ? undefined : String(filters.favorite),
        enabled: filters.enabled === undefined ? undefined : String(filters.enabled),
        q: filters.q,
      },
    });
    return arr(envelope(payload, 'models')).map(readModel);
  },

  async get(id: string, signal?: AbortSignal): Promise<Model> {
    const payload = await apiRequest<unknown>(`/api/models/${encodeURIComponent(id)}`, { signal });
    return readModel(envelope(payload, 'model'));
  },

  async capabilities(id: string, signal?: AbortSignal): Promise<ModelCapabilitiesResponse> {
    const payload = await apiRequest<unknown>(`/api/models/${encodeURIComponent(id)}/capabilities`, {
      signal,
    });
    const r = isRecord(envelope(payload, 'capabilities'))
      ? (envelope(payload, 'capabilities') as Record<string, unknown>)
      : {};
    return {
      capabilities: readCapabilities(r),
      modes: arr(isRecord(payload) ? payload.modes : []).map((raw) => {
        const m = isRecord(raw) ? raw : {};
        return { key: str(m.key) ?? '', label: str(m.label) ?? str(m.key) ?? '', supported: bool(m.supported) ?? false };
      }),
      camera_moves: arr(isRecord(payload) ? payload.camera_moves : []).map((raw): CameraMoveOption => {
        const c = isRecord(raw) ? raw : {};
        return {
          key: str(c.key) ?? '',
          label: str(c.label) ?? str(c.key) ?? '',
          phrase: str(c.phrase) ?? '',
          is_native: bool(c.is_native) ?? false,
        };
      }),
      reference_roles: arr(isRecord(payload) ? payload.reference_roles : []).map((raw): ReferenceRoleOption => {
        if (typeof raw === 'string') return { key: raw, label: raw };
        const v = isRecord(raw) ? raw : {};
        return {
          key: str(v.key) ?? '',
          label: str(v.label) ?? str(v.key) ?? '',
          description: str(v.description) ?? undefined,
        };
      }),
    };
  },

  async parameters(id: string, signal?: AbortSignal): Promise<ModelParametersResponse> {
    const payload = await apiRequest<unknown>(`/api/models/${encodeURIComponent(id)}/parameters`, {
      signal,
    });
    const schemaRaw = isRecord(envelope(payload, 'schema'))
      ? (envelope(payload, 'schema') as Record<string, unknown>)
      : {};
    return {
      schema: { parameters: arr(schemaRaw.parameters).filter(isRecord) as never },
      parameter_types: strList(isRecord(payload) ? payload.parameter_types : []),
    };
  },

  async patch(id: string, input: ModelPatchInput): Promise<Model> {
    const payload = await apiRequest<unknown>(`/api/models/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: input,
    });
    return readModel(envelope(payload, 'model'));
  },

  async remove(id: string): Promise<void> {
    await apiRequest<unknown>(`/api/models/${encodeURIComponent(id)}`, { method: 'DELETE' });
  },
};

/* ------------------------------------------------------------ generations */

export const generations = {
  async image(input: ImageGenerationInput): Promise<Job> {
    const payload = await apiRequest<unknown>('/api/generations/images', { method: 'POST', body: input });
    return readJob(envelope(payload, 'job'));
  },
  async video(input: VideoGenerationInput): Promise<Job> {
    const payload = await apiRequest<unknown>('/api/generations/videos', { method: 'POST', body: input });
    return readJob(envelope(payload, 'job'));
  },
  async variations(jobId: string, input: VariationsInput): Promise<Job> {
    const payload = await apiRequest<unknown>(
      `/api/generations/${encodeURIComponent(jobId)}/variations`,
      { method: 'POST', body: input },
    );
    return readJob(envelope(payload, 'job'));
  },
  async edit(jobId: string, input: EditGenerationInput): Promise<Job> {
    const payload = await apiRequest<unknown>(`/api/generations/${encodeURIComponent(jobId)}/edit`, {
      method: 'POST',
      body: input,
    });
    return readJob(envelope(payload, 'job'));
  },
  async extend(jobId: string, input: ExtendGenerationInput): Promise<Job> {
    const payload = await apiRequest<unknown>(`/api/generations/${encodeURIComponent(jobId)}/extend`, {
      method: 'POST',
      body: input,
    });
    return readJob(envelope(payload, 'job'));
  },
  async transform(jobId: string, input: TransformGenerationInput): Promise<Job> {
    const payload = await apiRequest<unknown>(`/api/generations/${encodeURIComponent(jobId)}/transform`, {
      method: 'POST',
      body: input,
    });
    return readJob(envelope(payload, 'job'));
  },
};

/* ------------------------------------------------------------------- jobs */

export interface JobFilters {
  state?: string;
  modality?: string;
  project_id?: string;
  model_id?: string;
  limit?: number;
  cursor?: string;
}

export const jobs = {
  async list(filters: JobFilters = {}, signal?: AbortSignal): Promise<JobListResponse> {
    const payload = await apiRequest<unknown>('/api/jobs', {
      signal,
      query: {
        state: filters.state,
        modality: filters.modality,
        project_id: filters.project_id,
        model_id: filters.model_id,
        limit: filters.limit,
        cursor: filters.cursor,
      },
    });
    return {
      jobs: arr(envelope(payload, 'jobs')).map(readJob),
      next_cursor: str(isRecord(payload) ? payload.next_cursor : null),
    };
  },

  async get(id: string, signal?: AbortSignal): Promise<Job> {
    const payload = await apiRequest<unknown>(`/api/jobs/${encodeURIComponent(id)}`, { signal });
    return readJob(envelope(payload, 'job'));
  },

  async events(id: string, signal?: AbortSignal): Promise<JobEvent[]> {
    const payload = await apiRequest<unknown>(`/api/jobs/${encodeURIComponent(id)}/events`, { signal });
    return arr(envelope(payload, 'events')).map((raw): JobEvent => {
      const r = isRecord(raw) ? raw : {};
      return {
        id: str(r.id) ?? '',
        kind: str(r.kind) ?? '',
        state: str(r.state),
        message: str(r.message),
        detail: r.detail ?? null,
        created_at: str(r.created_at) ?? '',
      };
    });
  },

  async cancel(id: string): Promise<Job> {
    const payload = await apiRequest<unknown>(`/api/jobs/${encodeURIComponent(id)}/cancel`, {
      method: 'POST',
    });
    return readJob(envelope(payload, 'job'));
  },

  async retry(id: string): Promise<Job> {
    const payload = await apiRequest<unknown>(`/api/jobs/${encodeURIComponent(id)}/retry`, {
      method: 'POST',
    });
    return readJob(envelope(payload, 'job'));
  },
};

/* ------------------------------------------------- projects and assets */

export const projects = {
  async list(signal?: AbortSignal): Promise<Project[]> {
    const payload = await apiRequest<unknown>('/api/projects', { signal });
    return arr(envelope(payload, 'projects')).map(readProject);
  },
  async create(input: { name: string; description?: string }): Promise<Project> {
    const payload = await apiRequest<unknown>('/api/projects', { method: 'POST', body: input });
    return readProject(envelope(payload, 'project'));
  },
  async get(id: string, signal?: AbortSignal): Promise<{ project: Project; assets: Asset[] }> {
    const payload = await apiRequest<unknown>(`/api/projects/${encodeURIComponent(id)}`, { signal });
    return {
      project: readProject(envelope(payload, 'project')),
      assets: arr(isRecord(payload) ? payload.assets : []).map(readAsset),
    };
  },
  async patch(
    id: string,
    input: { name?: string; description?: string; is_favorite?: boolean; is_archived?: boolean },
  ): Promise<Project> {
    const payload = await apiRequest<unknown>(`/api/projects/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: input,
    });
    return readProject(envelope(payload, 'project'));
  },
  async remove(id: string): Promise<{ assets_unassigned: number }> {
    const payload = await apiRequest<unknown>(`/api/projects/${encodeURIComponent(id)}`, {
      method: 'DELETE',
    });
    return { assets_unassigned: num(isRecord(payload) ? payload.assets_unassigned : null) ?? 0 };
  },
  async addAssets(id: string, assetIds: string[]): Promise<Project> {
    const payload = await apiRequest<unknown>(`/api/projects/${encodeURIComponent(id)}/assets`, {
      method: 'POST',
      body: { asset_ids: assetIds },
    });
    return readProject(envelope(payload, 'project'));
  },
  async jobs(id: string, signal?: AbortSignal): Promise<Job[]> {
    const payload = await apiRequest<unknown>('/api/jobs', {
      signal,
      query: { project_id: id, limit: 100 },
    });
    return arr(envelope(payload, 'jobs')).map(readJob);
  },
};

export const assets = {
  async list(
    filters: { project_id?: string; kind?: string; favorite?: boolean; q?: string; limit?: number; cursor?: string } = {},
    signal?: AbortSignal,
  ): Promise<AssetListResponse> {
    const payload = await apiRequest<unknown>('/api/assets', {
      signal,
      query: {
        project_id: filters.project_id,
        kind: filters.kind,
        favorite: filters.favorite === undefined ? undefined : String(filters.favorite),
        q: filters.q,
        limit: filters.limit,
        cursor: filters.cursor,
      },
    });
    return {
      assets: arr(envelope(payload, 'assets')).map(readAsset),
      next_cursor: str(isRecord(payload) ? payload.next_cursor : null),
    };
  },
  async upload(input: {
    file_name: string;
    content_type: string;
    data_base64: string;
    project_id?: string | null;
    kind?: string;
  }): Promise<Asset> {
    const payload = await apiRequest<unknown>('/api/assets', { method: 'POST', body: input });
    return readAsset(envelope(payload, 'asset'));
  },
  async get(id: string, signal?: AbortSignal): Promise<Asset> {
    const payload = await apiRequest<unknown>(`/api/assets/${encodeURIComponent(id)}`, { signal });
    return readAsset(envelope(payload, 'asset'));
  },
  async patch(
    id: string,
    input: { name?: string; project_id?: string | null; is_favorite?: boolean },
  ): Promise<Asset> {
    const payload = await apiRequest<unknown>(`/api/assets/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: input,
    });
    return readAsset(envelope(payload, 'asset'));
  },
  async remove(id: string): Promise<void> {
    await apiRequest<unknown>(`/api/assets/${encodeURIComponent(id)}`, { method: 'DELETE' });
  },
  async blob(url: string, signal?: AbortSignal): Promise<Blob> {
    return apiRequest<Blob>(url, { raw: true, absolute: true, signal });
  },
};

/* ---------------------------------------------------------------- library */

export const presets = {
  async list(
    filters: { modality?: string; category?: string; q?: string } = {},
    signal?: AbortSignal,
  ): Promise<Preset[]> {
    const payload = await apiRequest<unknown>('/api/presets', {
      signal,
      query: { modality: filters.modality, category: filters.category, q: filters.q },
    });
    return arr(envelope(payload, 'presets')).map(readPreset);
  },
  async create(input: Partial<Preset> & { name: string; modality: string }): Promise<Preset> {
    const payload = await apiRequest<unknown>('/api/presets', { method: 'POST', body: input });
    return readPreset(envelope(payload, 'preset'));
  },
  async get(id: string, signal?: AbortSignal): Promise<Preset> {
    const payload = await apiRequest<unknown>(`/api/presets/${encodeURIComponent(id)}`, { signal });
    return readPreset(envelope(payload, 'preset'));
  },
  async patch(id: string, input: Partial<Preset>): Promise<Preset> {
    const payload = await apiRequest<unknown>(`/api/presets/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: input,
    });
    return readPreset(envelope(payload, 'preset'));
  },
  async remove(id: string): Promise<void> {
    await apiRequest<unknown>(`/api/presets/${encodeURIComponent(id)}`, { method: 'DELETE' });
  },
  async favorite(id: string, favorite: boolean): Promise<Preset> {
    const payload = await apiRequest<unknown>(`/api/presets/${encodeURIComponent(id)}/favorite`, {
      method: 'POST',
      body: { favorite },
    });
    return readPreset(envelope(payload, 'preset'));
  },
};

export const prompts = {
  async list(
    filters: { q?: string; model_id?: string } = {},
    signal?: AbortSignal,
  ): Promise<PromptRecord[]> {
    const payload = await apiRequest<unknown>('/api/prompts', {
      signal,
      query: { q: filters.q, model_id: filters.model_id },
    });
    return arr(envelope(payload, 'prompts')).map(readPrompt);
  },
  async create(input: {
    text: string;
    title?: string;
    negative_prompt?: string;
    model_id?: string;
    tags?: string[];
    variables?: Record<string, unknown>;
  }): Promise<PromptRecord> {
    const payload = await apiRequest<unknown>('/api/prompts', { method: 'POST', body: input });
    return readPrompt(envelope(payload, 'prompt'));
  },
  async patch(id: string, input: Partial<PromptRecord>): Promise<PromptRecord> {
    const payload = await apiRequest<unknown>(`/api/prompts/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: input,
    });
    return readPrompt(envelope(payload, 'prompt'));
  },
  async remove(id: string): Promise<void> {
    await apiRequest<unknown>(`/api/prompts/${encodeURIComponent(id)}`, { method: 'DELETE' });
  },
};

export const characters = {
  async list(signal?: AbortSignal): Promise<Character[]> {
    const payload = await apiRequest<unknown>('/api/characters', { signal });
    return arr(envelope(payload, 'characters')).map(readCharacter);
  },
  async create(input: {
    name: string;
    description?: string;
    attributes?: Record<string, string>;
    default_prompt?: string;
    preferred_model_id?: string | null;
    tags?: string[];
    reference_asset_ids?: string[];
  }): Promise<Character> {
    const payload = await apiRequest<unknown>('/api/characters', { method: 'POST', body: input });
    return readCharacter(envelope(payload, 'character'));
  },
  async get(
    id: string,
    signal?: AbortSignal,
  ): Promise<{ character: Character; references: CharacterReference[] }> {
    const payload = await apiRequest<unknown>(`/api/characters/${encodeURIComponent(id)}`, { signal });
    const refs = arr(isRecord(payload) ? payload.references : []).map((raw): CharacterReference => {
      if (typeof raw === 'string') return { asset_id: raw, role: 'reference' };
      const r = isRecord(raw) ? raw : {};
      return { asset_id: str(r.asset_id) ?? '', role: str(r.role) ?? 'reference', url: str(r.url) };
    });
    return { character: readCharacter(envelope(payload, 'character')), references: refs };
  },
  async patch(id: string, input: Partial<Character>): Promise<Character> {
    const payload = await apiRequest<unknown>(`/api/characters/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: input,
    });
    return readCharacter(envelope(payload, 'character'));
  },
  async remove(id: string): Promise<void> {
    await apiRequest<unknown>(`/api/characters/${encodeURIComponent(id)}`, { method: 'DELETE' });
  },
  async addReference(id: string, input: { asset_id: string; role: string }): Promise<Character> {
    const payload = await apiRequest<unknown>(`/api/characters/${encodeURIComponent(id)}/references`, {
      method: 'POST',
      body: input,
    });
    return readCharacter(envelope(payload, 'character'));
  },
};

export const usage = {
  async list(limit?: number, signal?: AbortSignal): Promise<UsageResponse> {
    const payload = await apiRequest<unknown>('/api/usage', { signal, query: { limit } });
    return {
      usage: arr(envelope(payload, 'usage')).map((raw) => {
        const r = isRecord(raw) ? raw : {};
        return {
          id: str(r.id) ?? '',
          job_id: str(r.job_id),
          kind: str(r.kind) ?? '',
          model_id: str(r.model_id),
          provider: str(r.provider),
          quantity: num(r.quantity) ?? 0,
          unit: str(r.unit) ?? 'request',
          is_estimate: bool(r.is_estimate) ?? false,
          is_billable: bool(r.is_billable) ?? false,
          note: str(r.note),
          created_at: str(r.created_at) ?? '',
        };
      }),
      billing: readBilling(isRecord(payload) ? payload.billing : null),
    };
  },
};

/* ------------------------------------------------------- prompt assistant */

// One path. The server used to serve none and this module probed three invented
// ones, which turned a missing feature into three 404s and a vague error. The
// route now exists, so there is nothing to probe: a failure here is a real
// failure and its message is the server's.
const IMPROVE_PATHS = ['/api/prompts/improve'];

async function readImprovement(payload: unknown): Promise<PromptImprovement> {
  const candidates = ['improvement', 'suggestion', 'prompt', 'result'];
  for (const key of candidates) {
    if (isRecord(payload) && isRecord(payload[key])) {
      const r = payload[key] as Record<string, unknown>;
      const text = str(r.suggestion) ?? str(r.text) ?? str(r.prompt) ?? str(r.improved);
      if (text) {
        return {
          suggestion: text,
          negative_prompt: str(r.negative_prompt),
          // notes is a list on the wire. A single string is accepted too so an
          // older response shape does not vanish from the review list.
          notes: Array.isArray(r.notes)
            ? r.notes.filter((item): item is string => typeof item === 'string')
            : str(r.notes)
              ? [str(r.notes) as string]
              : null,
          provider: str(r.provider),
          is_mock: bool(r.is_mock) ?? false,
          original: str(r.original),
          changed: bool(r.changed),
          operation: str(r.operation),
        };
      }
    }
  }
  const direct = str(isRecord(payload) ? payload.suggestion : null) ?? str(isRecord(payload) ? payload.text : null);
  if (direct) return { suggestion: direct, is_mock: bool(isRecord(payload) ? payload.is_mock : null) ?? false };
  throw new ApiError({
    code: 'invalid_response',
    message: 'The prompt assistant answered with a body the client could not read as a suggestion.',
    hint: 'Expected a suggestion/text/prompt field in the response.',
  });
}

export const assistant = {
  async improve(input: {
    prompt: string;
    negative_prompt?: string;
    model_id?: string;
    modality?: string;
    operation?: "structure" | "expand" | "condense" | "to_video";
  }): Promise<PromptImprovement> {
    let lastError: unknown = null;
    for (const path of IMPROVE_PATHS) {
      try {
        const payload = await apiRequest<unknown>(path, { method: 'POST', body: input });
        return await readImprovement(payload);
      } catch (error) {
        lastError = error;
        if (error instanceof ApiError && error.code !== 'endpoint_not_found' && error.status !== 404) {
          throw error;
        }
      }
    }
    throw lastError instanceof ApiError
      ? lastError
      : new ApiError({
          code: 'configuration_required',
          message: 'The prompt assistant did not answer.',
          hint: 'Check that the server is running, then try again.',
        });
  },
};
