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
  JobState,
  JobListResponse,
  MeResponse,
  Model,
  ModelCapabilitiesResponse,
  ModelInput,
  ModelParametersResponse,
  ModelPatchInput,
  ModeVocabulary,
  PreflightParam,
  Preset,
  Project,
  PromptCatalogEntry,
  PromptCatalogModel,
  PromptConditionalLayer,
  PromptImprovement,
  PromptLayerFact,
  PromptModel,
  PromptModelSummary,
  PromptParamSpec,
  PromptPreflight,
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

/* ------------------------------------------------- job status <-> job state */
/* Two vocabularies meet here and only here. The server stores the status of a
   job (`queued`, `validating`, `submitting`, `processing`, `downloading`,
   `completed`, `failed`, `cancelled`, `timed_out` -- the CHECK constraint in
   studio/migrations/0004_jobs.sql is the authority). The screens speak states
   (`succeeded`, which is what `completed` means to a person). Reading `state`
   off a payload that carries `status` made every job look queued forever: the
   poller never stopped and the submit button never came back. Two maps, one
   place, and a test that fails when the server grows a status this file has not
   translated. */

const JOB_STATE_FROM_STATUS: Record<string, JobState> = {
  draft: 'queued',
  queued: 'queued',
  validating: 'queued',
  submitting: 'processing',
  processing: 'processing',
  downloading: 'processing',
  completed: 'succeeded',
  failed: 'failed',
  cancelled: 'cancelled',
  timed_out: 'failed',
};

const JOB_STATUS_FROM_STATE: Record<string, string> = {
  queued: 'queued',
  processing: 'processing',
  running: 'processing',
  succeeded: 'completed',
  failed: 'failed',
  cancelled: 'cancelled',
  canceled: 'cancelled',
};

/** The screen's word for what the server reported. Unknown values pass through. */
export function jobStateFromStatus(status: unknown): JobState {
  const raw = str(status);
  if (!raw) return 'queued';
  return JOB_STATE_FROM_STATUS[raw] ?? raw;
}

/** The server's word for a filter the screen asked for. */
export function jobStatusFromState(state: string | undefined): string | undefined {
  if (!state) return undefined;
  return JOB_STATUS_FROM_STATE[state] ?? state;
}

/* ------------------------------------------------------------ normalisers */
/* The backend is the source of truth; these readers only coerce the JSON that
   arrived into the documented shape and never fabricate a missing field.      */

function readUser(raw: unknown): User {
  const r = isRecord(raw) ? raw : {};
  return {
    id: str(r.id) ?? '',
    email: str(r.email) ?? '',
    display_name: str(r.display_name) ?? str(r.email) ?? 'Unknown user',
    is_admin: bool(r.is_admin) ?? false,
  };
}

function readWorkspace(raw: unknown): Workspace {
  const r = isRecord(raw) ? raw : {};
  return {
    id: str(r.id) ?? '',
    name: str(r.name) ?? 'Workspace',
    role: (str(r.role) as Workspace['role']) ?? undefined,
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
    // The registry flattens the connection into `connection_*` keys and a
    // `provider_slug`; it never nests a provider block, which is what this reader
    // used to look for -- so every model picker said "Unknown connection".
    provider: {
      connection_name: str(r.connection_name) ?? 'Unknown connection',
      adapter_type: str(r.connection_adapter_type) ?? str(r.adapter_type) ?? '',
      adapter_label: str(r.provider_name) ?? str(r.provider_slug) ?? str(r.connection_adapter_type) ?? 'Unknown adapter',
      base_url: str(r.base_url),
      is_mock: bool(r.is_mock) ?? false,
      connection_enabled: bool(r.connection_is_enabled) ?? true,
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
    // The row names its adapter type; the friendly label lives in the adapter
    // registry, so the slug is what a row can honestly report.
    adapter_label: str(r.provider_slug) ?? str(r.adapter_type) ?? 'Unknown adapter',
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
    storage: str(r.storage_backend) ?? 'unknown',
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
    // An asset does not know which job produced it, and it is not itself mock:
    // the job is. Its file name is `filename` and its type is `mime_type`.
    job_id: null,
    kind: str(r.kind) ?? 'upload',
    name: str(r.filename) ?? 'Untitled asset',
    content_type: str(r.mime_type) ?? 'application/octet-stream',
    byte_size: num(r.byte_size) ?? 0,
    width: num(r.width),
    height: num(r.height),
    duration_seconds: num(r.duration_seconds),
    is_favorite: bool(r.is_favorite) ?? false,
    is_mock: false,
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
  const ingestError = str(r.ingest_error);
  return {
    id,
    asset_id: assetId,
    // A stored output carries the provider's URL and the asset it became; the
    // size, dimensions and type live on the asset, not on this row.
    url: str(r.provider_url) ?? (assetId ? `/api/assets/${assetId}/content` : null),
    content_type: null,
    width: null,
    height: null,
    duration_seconds: null,
    file_name: null,
    byte_size: null,
    seed: null,
    metadata: ingestError ? { ingest_error: ingestError } : null,
    is_mock: false,
  };
}

export function readJob(raw: unknown): Job {
  const r = isRecord(raw) ? raw : {};
  const modelRaw = isRecord(r.model) ? r.model : null;
  // The provider of a job is the shaped connection the server attaches; the model
  // block names the model. Neither used to be read from the keys that carry them.
  const jobProvider = isRecord(r.provider) ? r.provider : {};
  const costRaw = isRecord(r.cost_estimate) ? r.cost_estimate : null;
  const providerError = isRecord(r.provider_error) ? r.provider_error : null;
  const errorCode = str(r.error_code);
  const errorMessage = str(r.error_message);
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
            connection_name: str(jobProvider.name) ?? '',
            adapter_label: str(jobProvider.provider_slug) ?? '',
            is_mock: bool(jobProvider.is_mock) ?? false,
          },
        }
      : null,
    mode: str(r.mode) ?? '',
    kind: str(r.modality) ?? '',
    state: jobStateFromStatus(r.status),
    prompt: str(r.prompt),
    negative_prompt: str(r.negative_prompt),
    parameters: isRecord(r.parameters) ? r.parameters : null,
    references: arr(r.references).filter(isRecord).map((ref) => ({
      asset_id: str(ref.asset_id) ?? undefined,
      url: str(ref.url) ?? undefined,
      role: str(ref.role) ?? 'reference',
    })),
    seed: null,
    count: num(r.output_count),
    provider_job_id: str(r.provider_job_ref),
    provider_status: null,
    progress: null,
    progress_is_estimate: false,
    message: str(r.status_message),
    // A failure is stored as three columns and the provider's own payload, not as
    // a nested `error` object. Built here so a failed job can say why it failed.
    error:
      errorCode || errorMessage
        ? {
            code: errorCode ?? 'provider_error',
            message: errorMessage ?? '',
            hint: str(r.error_hint),
            details: providerError,
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
    elapsed_ms: null,
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
    // The project row has a `status`, not job counts or flags. A job count would
    // need a query this payload does not make, so it is not invented here.
    job_count: null,
    is_favorite: false,
    is_archived: str(r.status) === 'archived',
    created_at: str(r.created_at) ?? '',
    updated_at: str(r.updated_at) ?? undefined,
  };
}

function readPreset(raw: unknown): Preset {
  const r = isRecord(raw) ? raw : {};
  return {
    id: str(r.id) ?? '',
    // One shape for catalogue and saved presets: `slug` is the dataset key,
    // `kind` is the modality, `defaults` holds the settings, `camera_settings`
    // the move. The reader used to look for `key`, `modality`, `parameters` and
    // `camera`, none of which any route sends.
    key: str(r.slug),
    is_builtin: bool(r.is_builtin) ?? false,
    workspace_id: str(r.workspace_id),
    name: str(r.name) ?? 'Untitled preset',
    description: str(r.description),
    modality: str(r.kind) ?? 'image',
    category: str(r.category),
    prompt_template: str(r.prompt_template),
    negative_prompt_template: str(r.negative_prompt_template),
    aspect_ratio: isRecord(r.defaults) ? str(r.defaults.aspect_ratio) : null,
    model_preference: null,
    parameters: isRecord(r.defaults) ? r.defaults : null,
    camera: isRecord(r.camera_settings) ? r.camera_settings : null,
    notes: str(r.notes),
    thumbnail_url: str(r.thumbnail_url),
    is_favorite: false,
    created_at: str(r.created_at),
    updated_at: str(r.updated_at) ?? undefined,
  };
}

function readPrompt(raw: unknown): PromptRecord {
  const r = isRecord(raw) ? raw : {};
  return {
    id: str(r.id) ?? '',
    workspace_id: str(r.workspace_id) ?? undefined,
    // A saved prompt is stored as `body` / `negative_body` and counts its uses in
    // `use_count`; the reader used to ask for `text`, `negative_prompt` and
    // `usage_count`, so the library showed empty prompts and zero uses.
    text: str(r.body) ?? '',
    title: str(r.title),
    negative_prompt: str(r.negative_body),
    model_id: null,
    tags: strList(r.tags).length ? strList(r.tags) : null,
    variables: isRecord(r.variables) ? r.variables : null,
    usage_count: num(r.use_count),
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
    default_prompt: isRecord(r.prompt_details) ? str(r.prompt_details.default_prompt) : null,
    preferred_model_id: strList(r.preferred_models)[0] ?? null,
    tags: strList(r.tags).length ? strList(r.tags) : null,
    reference_asset_ids:
      arr(r.references)
        .filter(isRecord)
        .map((reference) => str(reference.asset_id) ?? '')
        .filter((value) => value.length > 0) || null,
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
        // The screen filters by state; the API filters by status.
        state: jobStatusFromState(filters.state),
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
        state: jobStateFromStatus(r.state ?? r.status),
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

/* ------------------------------------------------ write bodies: view -> wire */
/* A form is written against the view model, which is the shape a screen reads.
   The create and patch routes accept the *stored* names, and refuse an unknown
   key by name rather than ignoring it. These three functions are the one place
   the two are reconciled; every write goes through one of them. */

function presetBody(input: Partial<Preset> & { name?: string; modality?: string }): Record<string, unknown> {
  const body: Record<string, unknown> = {};
  if (input.name !== undefined) body.name = input.name;
  if (input.modality !== undefined) body.kind = input.modality;
  if (input.description !== undefined) body.description = input.description;
  if (input.category !== undefined) body.category = input.category;
  if (input.prompt_template !== undefined) body.prompt_template = input.prompt_template;
  if (input.negative_prompt_template !== undefined) body.negative_prompt_template = input.negative_prompt_template;
  // The frame a look was designed for is a property of the preset, and the row
  // has nowhere to put it except `defaults`. Folding it in here keeps the built-in
  // catalogue (which does the same on the server) and a saved preset identical.
  if (input.parameters !== undefined || input.aspect_ratio !== undefined) {
    const defaults: Record<string, unknown> = { ...(input.parameters ?? {}) };
    if (input.aspect_ratio) defaults.aspect_ratio = input.aspect_ratio;
    body.defaults = defaults;
  }
  if (input.camera !== undefined) body.camera_settings = input.camera;
  if (input.tags !== undefined) body.tags = input.tags;
  if (input.thumbnail_url !== undefined) body.thumbnail_url = input.thumbnail_url;
  return body;
}

function promptBody(input: {
  text?: string | null;
  title?: string | null;
  negative_prompt?: string | null;
  tags?: string[] | null;
  variables?: Record<string, unknown> | null;
}): Record<string, unknown> {
  const body: Record<string, unknown> = {};
  if (input.title !== undefined) body.title = input.title;
  if (input.text !== undefined) body.body = input.text;
  if (input.negative_prompt !== undefined) body.negative_body = input.negative_prompt;
  if (input.tags !== undefined) body.tags = input.tags;
  if (input.variables !== undefined) body.variables = input.variables;
  return body;
}

function characterBody(input: {
  name?: string;
  description?: string | null;
  attributes?: Record<string, string> | null;
  default_prompt?: string | null;
  preferred_model_id?: string | null;
  tags?: string[] | null;
}): Record<string, unknown> {
  const body: Record<string, unknown> = {};
  if (input.name !== undefined) body.name = input.name;
  if (input.description !== undefined) body.description = input.description;
  if (input.attributes !== undefined) body.attributes = input.attributes;
  if (input.tags !== undefined) body.tags = input.tags;
  if (input.default_prompt !== undefined) body.prompt_details = { default_prompt: input.default_prompt };
  if (input.preferred_model_id !== undefined) {
    body.preferred_models = input.preferred_model_id ? [input.preferred_model_id] : [];
  }
  return body;
}

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
    const payload = await apiRequest<unknown>('/api/presets', { method: 'POST', body: presetBody(input) });
    return readPreset(envelope(payload, 'preset'));
  },
  async get(id: string, signal?: AbortSignal): Promise<Preset> {
    const payload = await apiRequest<unknown>(`/api/presets/${encodeURIComponent(id)}`, { signal });
    return readPreset(envelope(payload, 'preset'));
  },
  async patch(id: string, input: Partial<Preset>): Promise<Preset> {
    const payload = await apiRequest<unknown>(`/api/presets/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: presetBody(input),
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
    const payload = await apiRequest<unknown>('/api/prompts', { method: 'POST', body: promptBody(input) });
    return readPrompt(envelope(payload, 'prompt'));
  },
  async patch(id: string, input: Partial<PromptRecord>): Promise<PromptRecord> {
    const payload = await apiRequest<unknown>(`/api/prompts/${encodeURIComponent(id)}`, {
      method: 'PATCH',
      body: promptBody(input),
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
    const payload = await apiRequest<unknown>('/api/characters', {
      method: 'POST',
      body: characterBody(input),
    });
    const created = readCharacter(envelope(payload, 'character'));
    // References are their own route: the create body takes a character, not a
    // gallery, and sending them here would be refused as an unknown field.
    for (const assetId of input.reference_asset_ids ?? []) {
      await apiRequest<unknown>(`/api/characters/${encodeURIComponent(created.id)}/references`, {
        method: 'POST',
        body: { asset_id: assetId, role: 'reference' },
      });
    }
    return created;
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
      body: characterBody(input),
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
        // A usage row records what a provider charged: `actual_cost` when it
        // reported one, `estimated_cost` when it did not, and `units` for the
        // count. The reader used to ask for `quantity`, `unit`, `is_estimate`
        // and `is_billable`, which the row does not carry.
        const actual = num(r.actual_cost);
        const estimated = num(r.estimated_cost);
        return {
          id: str(r.id) ?? '',
          job_id: str(r.job_id),
          kind: str(r.modality) ?? '',
          model_id: str(r.model_id),
          provider: str(r.billing_party),
          quantity: num(r.units) ?? 0,
          unit: 'request',
          is_estimate: actual === null && estimated !== null,
          is_billable: actual !== null,
          note: null,
          created_at: str(r.created_at) ?? '',
          actual_cost: actual,
          estimated_cost: estimated,
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

/* ------------------------------------------------------------- prompt layer */
/* The layer dataset, served by the same application as everything else. These
   readers only coerce; the server decides what is active, what a layer is, and
   which parameter switches it off. */

function readPromptParamSpecs(raw: unknown): Record<string, PromptParamSpec> {
  const source = isRecord(raw) ? raw : {};
  const out: Record<string, PromptParamSpec> = {};
  for (const [name, value] of Object.entries(source)) {
    const spec = isRecord(value) ? value : {};
    out[name] = {
      default: spec.default,
      options: Array.isArray(spec.options) ? spec.options.map((item) => String(item)) : null,
    };
  }
  return out;
}

function readPromptLayer(raw: unknown): PromptLayerFact {
  const r = isRecord(raw) ? raw : {};
  const disable = isRecord(r.disable_with) ? r.disable_with : null;
  const override = isRecord(r.your_override) ? r.your_override : null;
  return {
    id: str(r.id) ?? '',
    kind: str(r.kind) ?? 'unknown',
    active: bool(r.active) ?? false,
    detail: str(r.detail) ?? '',
    disable_with: disable,
    source: str(r.source) ?? '',
    evidence: str(r.evidence) ?? null,
    your_override: override,
  };
}

function readPromptSummary(raw: unknown): PromptModelSummary {
  const r = isRecord(raw) ? raw : {};
  return {
    slug: str(r.slug) ?? '',
    display: str(r.display) ?? str(r.slug) ?? 'Unnamed model',
    provider: str(r.provider) ?? '',
    kind: str(r.kind) ?? 'image',
    accepts_image: bool(r.accepts_image) ?? false,
    layer_count: num(r.layer_count) ?? 0,
    active_layer_count: num(r.active_layer_count) ?? 0,
  };
}

function readPromptModel(raw: unknown): PromptModel {
  const r = isRecord(raw) ? raw : {};
  return {
    ...readPromptSummary(raw),
    endpoint: str(r.endpoint) ?? '',
    params: readPromptParamSpecs(r.params),
    layers: arr(r.layers).map(readPromptLayer),
    sources: strList(r.sources),
  };
}

function readCatalogModel(raw: unknown): PromptCatalogModel | null {
  if (!isRecord(raw)) return null;
  return {
    slug: str(raw.slug) ?? '',
    display: str(raw.display) ?? str(raw.slug) ?? 'Unnamed model',
    kind: str(raw.kind) ?? 'image',
    layer_count: num(raw.layer_count) ?? 0,
    active_layer_count: num(raw.active_layer_count) ?? 0,
    params: readPromptParamSpecs(raw.params),
    layers: arr(raw.layers).map(readPromptLayer),
  };
}

function readCatalogEntry(raw: unknown): PromptCatalogEntry {
  const r = isRecord(raw) ? raw : {};
  return {
    endpoint: str(r.endpoint) ?? '',
    display: str(r.display) ?? str(r.endpoint) ?? 'Unnamed endpoint',
    modality: str(r.modality) ?? 'image',
    modes: strList(r.modes),
    aspect_ratios: strList(r.aspect_ratios),
    resolutions: strList(r.resolutions),
    durations_seconds: arr(r.durations_seconds).filter((item): item is number => typeof item === 'number'),
    max_outputs: num(r.max_outputs) ?? 1,
    supports_negative_prompt: bool(r.supports_negative_prompt) ?? false,
    supports_seed: bool(r.supports_seed) ?? false,
    restrictions: strList(r.restrictions),
    notes: strList(r.notes),
    prompt_model: readCatalogModel(r.prompt_model),
  };
}

function readPreflightParams(raw: unknown): Record<string, PreflightParam> {
  const source = isRecord(raw) ? raw : {};
  const out: Record<string, PreflightParam> = {};
  for (const [name, value] of Object.entries(source)) {
    const item = isRecord(value) ? value : {};
    out[name] = {
      value: item.value,
      source: str(item.source) ?? 'unknown',
      provider_default: item.provider_default,
    };
  }
  return out;
}

export function readPreflight(raw: unknown): PromptPreflight {
  const r = isRecord(raw) ? raw : {};
  const forwarded = isRecord(r.prompt) ? r.prompt : {};
  const estimate = isRecord(r.estimate) ? r.estimate : {};
  return {
    model: str(r.model) ?? '',
    display: str(r.display) ?? str(r.model) ?? '',
    provider: str(r.provider) ?? '',
    endpoint: str(r.endpoint) ?? '',
    unknown_model: bool(r.unknown_model) ?? false,
    known_model: bool(r.known_model) ?? false,
    prompt: {
      original: str(forwarded.original) ?? '',
      forwarded_as: str(forwarded.forwarded_as) ?? '',
      rewritten_upstream: bool(forwarded.rewritten_upstream) ?? false,
      image_count: num(forwarded.image_count) ?? 0,
    },
    messages: Array.isArray(r.messages)
      ? r.messages.filter(isRecord).map((item) => ({
          role: str(item.role) ?? '',
          content: str(item.content) ?? '',
        }))
      : null,
    params: readPreflightParams(r.params),
    layers: arr(r.layers).map(readPromptLayer),
    conditional_layers: arr(r.conditional_layers).filter(isRecord).map(
      (item): PromptConditionalLayer => ({
        id: str(item.id) ?? '',
        kind: str(item.kind) ?? '',
        applies_to: str(item.applies_to) ?? '',
        scope: str(item.scope),
        detail: str(item.detail) ?? '',
        source: str(item.source) ?? '',
      }),
    ),
    warnings: strList(r.warnings),
    full_control_patch: isRecord(r.full_control_patch) ? r.full_control_patch : {},
    estimate: {
      prompt_tokens: num(estimate.prompt_tokens) ?? 0,
      added_chars: num(estimate.added_chars) ?? 0,
      note: str(estimate.note) ?? '',
    },
    supports_compile: bool(r.supports_compile) ?? false,
    compile_styles: strList(r.compile_styles),
    version: str(r.version) ?? '',
  };
}

export const prompt = {
  async models(kind?: string, signal?: AbortSignal): Promise<{ models: PromptModelSummary[]; datasetVersion: string }> {
    const payload = await apiRequest<unknown>('/api/prompt/models', { signal, query: { kind } });
    return {
      models: arr(envelope(payload, 'models')).map(readPromptSummary),
      datasetVersion: str(isRecord(payload) ? payload.dataset_version : null) ?? '',
    };
  },
  async modes(signal?: AbortSignal): Promise<ModeVocabulary> {
    const payload = await apiRequest<unknown>('/api/prompt/modes', { signal });
    const record = isRecord(payload) ? payload : {};
    const labels: Record<string, string> = {};
    if (isRecord(record.labels)) {
      for (const [key, value] of Object.entries(record.labels)) labels[key] = String(value);
    }
    return { image: strList(record.image), video: strList(record.video), labels };
  },
  async model(slug: string, signal?: AbortSignal): Promise<PromptModel> {
    const payload = await apiRequest<unknown>(`/api/prompt/models/${encodeURIComponent(slug)}`, { signal });
    return readPromptModel(envelope(payload, 'model'));
  },
  async catalog(
    modality?: string,
    signal?: AbortSignal,
  ): Promise<{ endpoints: PromptCatalogEntry[]; documented: number; undocumented: number; source: string }> {
    const payload = await apiRequest<unknown>('/api/prompt/catalog', { signal, query: { modality } });
    const record = isRecord(payload) ? payload : {};
    return {
      endpoints: arr(envelope(payload, 'endpoints')).map(readCatalogEntry),
      documented: num(record.documented) ?? 0,
      undocumented: num(record.undocumented) ?? 0,
      source: str(record.source) ?? '',
    };
  },
  async preflight(
    input: {
      model: string;
      prompt: string;
      options?: Record<string, unknown>;
      system_message?: string;
      image_count?: number;
    },
    signal?: AbortSignal,
  ): Promise<PromptPreflight> {
    const payload = await apiRequest<unknown>('/api/prompt/preflight', {
      method: 'POST',
      body: input,
      signal,
    });
    return readPreflight(envelope(payload, 'preflight'));
  },
};
