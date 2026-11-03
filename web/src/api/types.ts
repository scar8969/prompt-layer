// TypeScript mirror of docs/API_CONTRACT.md (v1).
//
// Every shape here is copied from that document by name. Nothing is invented:
// where the contract is silent the field is optional rather than assumed to exist.

/* ------------------------------------------------------------------ errors */

export type ApiErrorCode =
  | 'unauthenticated'
  | 'forbidden'
  | 'not_found'
  | 'validation_failed'
  | 'conflict'
  | 'rate_limit_exceeded'
  | 'capability_unsupported'
  | 'configuration_required'
  | 'auth_failed'
  | 'endpoint_not_found'
  | 'unsupported_operation'
  | 'invalid_model_id'
  | 'rate_limited'
  | 'timeout'
  | 'invalid_response'
  | 'network_restricted'
  | 'provider_error'
  | 'insufficient_balance'
  | 'output_download_failed'
  | 'storage_failed'
  | 'internal'
  // The client adds this one locally for transport failures; it is never returned
  // by the backend.
  | 'network_error';

export interface ApiErrorBody {
  code: ApiErrorCode | string;
  message: string;
  hint?: string | null;
  details?: Record<string, unknown> | null;
  request_id?: string | null;
}

/* --------------------------------------------------------------- core ids */

export interface User {
  id: string;
  email: string;
  display_name: string;
  /** The account row reports this, and nothing else about the account's state. */
  is_admin: boolean;
}

export interface WorkspaceSummary {
  id: string;
  name: string;
  slug: string;
  role: WorkspaceRole;
}

export type WorkspaceRole = 'owner' | 'admin' | 'editor' | 'viewer';

export interface Workspace {
  id: string;
  name: string;
  /** Kept optional: the API reports the name and the role, not a slug. */
  slug?: string;
  role?: WorkspaceRole;
}

export interface SessionPayload {
  csrf_token: string;
}

export interface MeResponse {
  user: User;
  workspace: Workspace;
  workspaces: WorkspaceSummary[];
  permissions: string[];
}

export interface AuthResponse {
  user: User;
  workspace: Workspace;
  session: SessionPayload;
}

/* ---------------------------------------------------------------- billing */

export interface BillingInfo {
  mode: string; // "byok" in every documented payload
  platform_credits: boolean;
  note: string;
}

export interface UsageRow {
  id: string;
  job_id: string | null;
  kind: string;
  model_id: string | null;
  provider: string | null;
  quantity: number;
  unit: string;
  is_estimate: boolean;
  is_billable: boolean;
  note: string | null;
  created_at: string;
  /** What the provider actually charged, when it reported an amount. */
  actual_cost: number | null;
  /** What the platform estimated before the call, when it estimated at all. */
  estimated_cost: number | null;
}

export interface UsageResponse {
  usage: UsageRow[];
  billing: BillingInfo;
}

/* ----------------------------------------------------------------- system */

export interface AdapterType {
  slug: string;
  label: string;
  summary: string;
  is_mock: boolean;
  discovers_models: boolean;
  supports_cancel?: boolean;
  auth_schemes?: string[];
  default_modes?: string[];
  /** The base URL this provider documents, pre-filled by the wizard. */
  default_base_url?: string;
  base_url_hint?: string;
  docs_url?: string;
  credential_hint?: string;
}

export interface SystemStatus {
  version: string;
  environment: string;
  time_utc: string;
  database: string;
  storage: string;
  adapters: AdapterType[];
  mock_provider_enabled: boolean;
  billing: BillingInfo;
  missing_configuration: string[];
  parameter_types: string[];
  camera_moves: string[];
  reference_roles: string[];
  database_backend?: string;
  storage_backend?: string;
}

/* ------------------------------------------------------------ connections */

export interface CredentialSummary {
  present: boolean;
  last4?: string | null;
  key_id?: string | null;
  fingerprint?: string | null;
}

export interface Connection {
  id: string;
  name: string;
  adapter_type: string;
  adapter_label: string;
  base_url: string | null;
  auth_scheme: string | null;
  model_count: number;
  is_enabled: boolean;
  last_test_status: string | null;
  last_test_at: string | null;
  last_used_at: string | null;
  created_at: string;
  credential: CredentialSummary;
  extra_header_names: string[];
}

export interface ConnectionTestResult {
  outcome: string;
  ok: boolean;
  http_status: number | null;
  latency_ms: number | null;
  message: string;
  detail?: unknown;
  models_seen: number | null;
  checked_at: string;
}

export interface ConnectionCreateInput {
  name: string;
  adapter_type: string;
  base_url: string;
  credential: string;
  auth_scheme?: string | null;
  auth_header_name?: string | null;
  extra_headers?: Record<string, string>;
  config?: Record<string, unknown>;
  allowed_hosts?: string[];
  models?: ModelInput[];
}

export interface ConnectionPatchInput {
  name?: string;
  base_url?: string;
  is_enabled?: boolean;
  extra_headers?: Record<string, string>;
  config?: Record<string, unknown>;
  auth_scheme?: string | null;
  auth_header_name?: string | null;
}

/* --------------------------------------------------- one-key onboarding */

/**
 * One entry from an aggregator's model list, as the server classified it.
 *
 * `drivable` is the honest half: this build can route conversation, images and
 * video; an audio or embedding model is reported but has no runner here. A probe
 * is present only for conversation models, and only when the server actually
 * asked the provider.
 */
export interface DiscoveredModel {
  model_key: string;
  display_name: string;
  family: string;
  drivable: boolean;
  modality: string;
  modes: string[];
  reason: string;
  available?: boolean;
  probe?: {
    ok: boolean;
    latency_ms: number;
    status?: number;
    error?: string;
  };
}

export interface DiscoveryPlan {
  provider: { name: string; base_url: string; model_list_path: string };
  models: DiscoveredModel[];
  counts: Record<string, number>;
  default_chat: string;
  default_chat_requested: string;
  probe_summary?: { probed: number; available: number; failed: number; max_probes: number };
  warnings: string[];
  notes: Record<string, string>;
}

export interface OnboardingDiscoverInput {
  name: string;
  base_url: string;
  api_key: string;
  probe_chat?: boolean;
  allowed_hosts?: string[];
}

export interface OnboardingModelChoice {
  model_key: string;
  family: string;
  display_name?: string;
}

export interface OnboardingRegisterInput {
  name: string;
  base_url: string;
  api_key: string;
  models: OnboardingModelChoice[];
  default_chat?: string;
  allowed_hosts?: string[];
}

export interface OnboardingRegisterResult {
  connection: Connection;
  connections: Connection[];
  models: Model[];
  default_chat_model_id: string;
  default_chat: string;
  notes: Record<string, string>;
}

/* ------------------------------------------------------------- conversation */

export interface ChatTurn {
  role: 'system' | 'user' | 'assistant';
  content: string;
}

export interface ChatReply {
  model_id: string;
  model_key: string;
  message: ChatTurn;
  finish_reason: string;
  usage: Record<string, unknown>;
  latency_ms: number;
  provider: {
    connection_id: string;
    connection_name: string;
    base_url: string;
    is_mock: boolean;
  };
}

/* ----------------------------------------------------------------- models */

export interface ModelInput {
  model_key: string;
  display_name: string;
  modality: Modality | string;
  modes: string[];
  capabilities?: Partial<Capabilities> | Record<string, unknown>;
  parameter_schema?: ParameterSchema;
  defaults?: Record<string, unknown>;
  limits?: Record<string, unknown>;
  source?: string;
}

export type Modality = 'image' | 'video';

export interface ParameterSpec {
  name: string;
  type: string;
  label?: string;
  description?: string;
  default?: unknown;
  required?: boolean;
  minimum?: number | null;
  maximum?: number | null;
  step?: number | null;
  options?: string[] | null;
  option_labels?: Record<string, string> | null;
  item_type?: string | null;
  min_items?: number | null;
  max_items?: number | null;
  max_length?: number | null;
  pattern?: string | null;
  prompt_mapped?: boolean;
  api_key?: boolean;
  group?: string | null;
  advanced?: boolean;
}

export interface ParameterSchema {
  parameters: ParameterSpec[];
}

export interface Capabilities {
  modality?: string;
  modes?: string[] | null;
  aspect_ratios?: string[] | null;
  resolutions?: string[] | null;
  durations_seconds?: number[] | null;
  frame_rates?: number[] | null;
  reference_roles?: string[] | null;
  max_reference_images?: number | null;
  max_outputs?: number | null;
  supports_negative_prompt?: boolean;
  supports_seed?: boolean;
  supports_audio?: boolean;
  supports_mask?: boolean;
  native_camera_moves?: string[] | null;
  restrictions?: string[] | null;
  notes?: string | null;
  max_input_bytes?: number | null;
}

export interface ModelProvider {
  connection_name: string;
  adapter_type: string;
  adapter_label: string;
  base_url: string | null;
  is_mock: boolean;
  connection_enabled: boolean;
}

export interface Model {
  id: string;
  connection_id: string;
  workspace_id: string;
  model_key: string;
  display_name: string;
  modality: Modality | string;
  modes: string[];
  capabilities: Capabilities;
  parameter_schema: ParameterSchema;
  defaults: Record<string, unknown>;
  limits: Record<string, unknown>;
  source: string;
  is_enabled: boolean;
  is_favorite: boolean;
  is_default: boolean;
  created_at: string;
  updated_at: string;
  provider: ModelProvider;
}

export interface ModelModeOption {
  key: string;
  label: string;
  supported: boolean;
}

export interface CameraMoveOption {
  key: string;
  label: string;
  phrase: string;
  is_native: boolean;
}

export interface ReferenceRoleOption {
  key: string;
  label: string;
  description?: string;
}

export interface ModelCapabilitiesResponse {
  capabilities: Capabilities;
  modes: ModelModeOption[];
  camera_moves: CameraMoveOption[];
  reference_roles: ReferenceRoleOption[];
}

export interface ModelParametersResponse {
  schema: ParameterSchema;
  parameter_types: string[];
}

export interface ModelPatchInput {
  display_name?: string;
  is_enabled?: boolean;
  is_favorite?: boolean;
  is_default?: boolean;
  modes?: string[];
  capabilities?: Partial<Capabilities> | Record<string, unknown>;
  parameter_schema?: ParameterSchema;
  defaults?: Record<string, unknown>;
}

/* ------------------------------------------------------------- jobs/gen */

export interface ReferenceInput {
  asset_id?: string;
  url?: string;
  role: string;
}

export interface ImageGenerationInput {
  model_id?: string;
  connection_id?: string;
  model_key?: string;
  mode: string;
  prompt: string;
  negative_prompt?: string | null;
  parameters?: Record<string, unknown>;
  references?: ReferenceInput[];
  mask_asset_id?: string | null;
  count?: number;
  seed?: number | null;
  project_id?: string | null;
  preset_id?: string | null;
  client_request_id?: string;
}

export interface VideoGenerationInput extends ImageGenerationInput {
  duration_seconds?: number | null;
  aspect_ratio?: string | null;
  resolution?: string | null;
  camera_move?: string | null;
  generate_audio?: boolean;
  first_frame_asset_id?: string | null;
  last_frame_asset_id?: string | null;
}

export interface VariationsInput {
  count: number;
  prompt?: string;
  seed?: number | null;
}

export interface EditGenerationInput {
  mode: 'inpaint' | 'outpaint';
  prompt: string;
  mask_asset_id?: string | null;
  expand?: { left: number; right: number; top: number; bottom: number };
  parameters?: Record<string, unknown>;
}

export interface ExtendGenerationInput {
  seconds: number;
  prompt?: string;
}

export interface TransformGenerationInput {
  mode: string;
  prompt: string;
  parameters?: Record<string, unknown>;
}

export interface AudioGenerationInput {
  /** voiceover speaks a script, dub replaces the speech, lip_sync matches a track. */
  mode: 'voiceover' | 'dub' | 'lip_sync' | 'audio';
  /** The words to speak. Required for voiceover and dub. */
  script?: string;
  /** Target language for a dub. */
  language?: string;
  /** The track the mouth must match. Required for lip_sync. */
  track_asset_id?: string;
  model_id?: string;
  parameters?: Record<string, unknown>;
}

export interface JobError {
  code: string;
  message: string;
  hint?: string | null;
  details?: Record<string, unknown> | null;
}

export interface JobModelRef {
  model_key: string;
  display_name: string;
  modality: string;
  provider: { connection_name: string; adapter_label: string; is_mock: boolean };
}

export type JobState =
  | 'queued'
  | 'processing'
  | 'succeeded'
  | 'failed'
  | 'cancelled'
  | 'canceled'
  | string;

export interface Output {
  id: string;
  asset_id: string | null;
  url: string | null;
  content_type: string | null;
  width: number | null;
  height: number | null;
  duration_seconds: number | null;
  file_name: string | null;
  byte_size: number | null;
  seed: number | null;
  metadata: Record<string, unknown> | null;
  is_mock: boolean;
}

export interface CostEstimate {
  currency?: string | null;
  amount?: number | null;
  is_estimate: boolean;
  is_billable: boolean;
  note?: string | null;
  breakdown?: unknown;
}

export interface Job {
  id: string;
  workspace_id: string;
  project_id: string | null;
  model_id: string | null;
  model: JobModelRef | null;
  mode: string;
  kind: string;
  state: JobState;
  prompt: string | null;
  negative_prompt: string | null;
  parameters: Record<string, unknown> | null;
  references: ReferenceInput[] | null;
  seed: number | null;
  count: number | null;
  provider_job_id: string | null;
  provider_status: string | null;
  progress: number | null;
  progress_is_estimate: boolean;
  message: string | null;
  error?: JobError | null;
  attempt: number | null;
  max_attempts: number | null;
  is_mock: boolean;
  cost_estimate: CostEstimate | null;
  outputs: Output[];
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  elapsed_ms: number | null;
  parent_job_id: string | null;
  relation: string | null;
}

export interface JobEvent {
  id: string;
  kind: string;
  state: string | null;
  message: string | null;
  detail: unknown;
  created_at: string;
}

/* -------------------------------------------------- projects and assets */

export interface Project {
  id: string;
  workspace_id: string;
  name: string;
  description: string | null;
  asset_count?: number | null;
  job_count?: number | null;
  is_favorite: boolean;
  is_archived: boolean;
  created_at: string;
  updated_at?: string;
}

export type AssetKind = 'image' | 'video' | 'upload' | 'mask' | string;

export interface Asset {
  id: string;
  workspace_id: string;
  project_id: string | null;
  job_id: string | null;
  kind: AssetKind;
  name: string;
  content_type: string;
  byte_size: number;
  width: number | null;
  height: number | null;
  duration_seconds: number | null;
  is_favorite: boolean;
  is_mock: boolean;
  content_url: string;
  thumbnail_url: string | null;
  created_at: string;
  source: 'generated' | 'uploaded' | string;
}

/* ---------------------------------------------------------------- library */

export interface PresetPreview {  preset_key: string;
  job_id: string;
  status: string;
  model_id: string;
  asset_id: string | null;
  url: string | null;
  thumbnail_url: string | null;
  error: string;
  created_at: string | null;
}

export interface Preset {
  /** `builtin:<key>` for a catalogue preset, a row id for a saved one. */
  id: string;
  /** Present on a catalogue preset; that is how the client tells the two apart. */
  key?: string | null;
  is_builtin?: boolean;
  /** Null on a catalogue preset: it is a document, not a row with a creation time. */
  workspace_id: string | null;
  name: string;
  description: string | null;
  modality: Modality | string;
  category: string | null;
  prompt_template: string | null;
  negative_prompt_template: string | null;
  tags?: string[] | null;
  aspect_ratio: string | null;
  model_preference: string | null;
  parameters: Record<string, unknown> | null;
  camera: Record<string, unknown> | null;
  notes: string | null;
  thumbnail_url?: string | null;
  /** A real render made on this workspace's own key, when one exists. */
  preview?: PresetPreview | null;
  is_favorite?: boolean;
  created_at: string | null;
  updated_at?: string | null;
}

export interface PromptRecord {
  id: string;
  workspace_id?: string;
  text: string;
  title: string | null;
  negative_prompt: string | null;
  model_id: string | null;
  tags: string[] | null;
  variables: Record<string, unknown> | null;
  usage_count?: number | null;
  created_at: string;
  updated_at?: string;
}

export interface CharacterReference {
  asset_id: string;
  role: string;
  url?: string | null;
}

export interface Character {
  id: string;
  workspace_id: string;
  name: string;
  description: string | null;
  attributes: Record<string, string> | null;
  default_prompt: string | null;
  preferred_model_id: string | null;
  tags: string[] | null;
  reference_asset_ids: string[] | null;
  reference_count?: number | null;
  created_at: string;
  updated_at?: string;
}

/* ------------------------------------------------------------------ canvas */

export type CanvasNodeKind = 'generate' | 'note';
export type CanvasEdgeRole = 'input' | 'reference';

export interface CanvasNode {
  id: string;
  kind: CanvasNodeKind;
  label: string;
  position: { x: number; y: number };
  /** note only */
  text?: string;
  /** generate only */
  prompt?: string;
  negative_prompt?: string;
  model_id?: string;
  mode?: string;
  parameters?: Record<string, unknown>;
  count?: number;
}

export interface CanvasEdge {
  from: string;
  to: string;
  role: CanvasEdgeRole;
  reference_role?: string;
}

export interface CanvasStage {
  node_id: string;
  label: string;
  kind: string;
}

export interface CanvasSummary {
  node_count: number;
  edge_count: number;
  generate_count: number;
  note_count: number;
  order: string[];
  stages: CanvasStage[];
}

export interface Canvas {
  id: string;
  workspace_id: string;
  created_by_user_id: string;
  name: string;
  description: string;
  nodes: CanvasNode[];
  edges: CanvasEdge[];
  /** Monotonic revision. A save that carries the revision it read cannot clobber a newer one. */
  revision?: number;
  updated_by_user_id?: string | null;
  created_at: string;
  updated_at: string;
  summary?: CanvasSummary;
}

export type CanvasNodeState = 'pending' | 'queued' | 'done' | 'failed' | 'skipped';

export interface CanvasNodeStateEntry {
  status: CanvasNodeState | string;
  job_id: string;
  asset_id: string;
  error: string;
}

export interface CanvasRun {
  id: string;
  canvas_id: string;
  workspace_id: string;
  status: 'queued' | 'running' | 'completed' | 'failed' | 'cancelled' | string;
  node_states: Record<string, CanvasNodeStateEntry>;
  order: string[];
  error: string;
  started_at: string | null;
  finished_at: string | null;
  created_at: string;
  updated_at: string;
  next_node?: string;
  node_order?: string[];
}

/* -------------------------------------------------------------- marketing */

export interface MarketingFormat {
  key: string;
  label: string;
  aspect_ratio: string;
  headline: string;
  caption: string;
  prompt: string;
}

export interface MarketingBrief {
  product: string;
  url: string;
  site_name: string;
  description: string;
  features: string[];
  image_reference: string;
  audience: string;
  benefit: string;
  tone: string;
  hook: string;
  hooks: { tone: string; text: string }[];
  formats: MarketingFormat[];
  warnings: string[];
  sources: Record<string, string>;
  notes: Record<string, string>;
}

export interface MarketingRender {
  format: string;
  label: string;
  aspect_ratio: string;
  headline: string;
  caption: string;
  prompt: string;
  job_id: string;
}

export interface MarketingCampaign {
  brief: MarketingBrief;
  model_id: string;
  project_id: string | null;
  renders: MarketingRender[];
}

/* -------------------------------------------------------------- responses */

export interface AssetListResponse {
  assets: Asset[];
  next_cursor: string | null;
}

export interface JobListResponse {
  jobs: Job[];
  next_cursor: string | null;
}

/* ------------------------------------------------- optional assistant API */

// The documented client surface mentions a prompt-improvement assistant. The
/**
 * A rewrite suggestion from the server, together with what it changed.
 *
 * The assistant runs on fixed rules inside the backend and calls no model, so
 * provider is local-rules and is_mock stays false: this is the whole behaviour
 * of the feature, not a placeholder standing in for something real. notes lists
 * every addition and move so the UI can show them beside the preview; nothing
 * reaches the prompt field without an explicit click.
 */
export interface PromptImprovement {
  suggestion: string;
  negative_prompt?: string | null;
  notes?: string[] | null;
  provider?: string | null;
  /** The prompt as it was submitted, for a side-by-side preview. */
  original?: string | null;
  /** False when the rewrite is identical to the input; the UI says so. */
  changed?: boolean | null;
  operation?: string | null;
  is_mock?: boolean;
}

/* --------------------------------------------------------- prompt layer */

/**
 * One documented fact about a provider endpoint. `active` is the server's
 * answer, never an inference here; `disable_with` names the parameter and value
 * that switches the layer off, when the provider documents such a switch.
 */
export interface PromptLayerFact {
  id: string;
  kind: string;
  active: boolean;
  detail: string;
  disable_with: Record<string, unknown> | null;
  source: string;
  evidence?: string | null;
  your_override?: Record<string, unknown> | null;
}

/** The generation-mode vocabulary, served by the backend that validates it. */
export interface ModeVocabulary {
  image: string[];
  video: string[];
  labels: Record<string, string>;
}

export interface PromptParamSpec {
  default?: unknown;
  options?: string[] | null;
}

export interface PromptCatalogModel {
  slug: string;
  display: string;
  kind: string;
  layer_count: number;
  active_layer_count: number;
  params: Record<string, PromptParamSpec>;
  layers: PromptLayerFact[];
}

/**
 * A model the layer dataset describes. Absence from this list is not a
 * capability claim: it means no document in this repository describes it, and
 * the preflight response says so rather than inventing defaults.
 */
export interface PromptModelSummary {
  slug: string;
  display: string;
  provider: string;
  kind: string;
  accepts_image: boolean;
  layer_count: number;
  active_layer_count: number;
}

export interface PromptModel extends PromptModelSummary {
  endpoint: string;
  params: Record<string, PromptParamSpec>;
  layers: PromptLayerFact[];
  sources: string[];
}

/**
 * One endpoint this platform can drive on a user's own key, joined to the layer
 * facts. `prompt_model` is null when no document describes the endpoint; the UI
 * shows that as unknown, never as a model that adds nothing.
 */
export interface PromptCatalogEntry {
  endpoint: string;
  display: string;
  modality: string;
  modes: string[];
  aspect_ratios: string[];
  resolutions: string[];
  durations_seconds: number[];
  max_outputs: number;
  supports_negative_prompt: boolean;
  supports_seed: boolean;
  restrictions: string[];
  notes: string[];
  prompt_model: PromptCatalogModel | null;
}

export interface PreflightParam {
  value: unknown;
  source: string;
  provider_default?: unknown;
}

export interface PromptConditionalLayer {
  id: string;
  kind: string;
  applies_to: string;
  scope?: string | null;
  detail: string;
  source: string;
}

export interface PromptPreflight {
  model: string;
  display: string;
  provider: string;
  endpoint: string;
  unknown_model: boolean;
  known_model: boolean;
  prompt: {
    original: string;
    forwarded_as: string;
    rewritten_upstream: boolean;
    image_count: number;
  };
  messages: { role: string; content: string }[] | null;
  params: Record<string, PreflightParam>;
  layers: PromptLayerFact[];
  conditional_layers: PromptConditionalLayer[];
  warnings: string[];
  full_control_patch: Record<string, unknown>;
  estimate: { prompt_tokens: number; added_chars: number; note: string };
  supports_compile: boolean;
  compile_styles: string[];
  version: string;
}


/* ------------------------------------------------------------ the panel */

/** One choice on the Director's Panel: the words it contributes, and why. */
export interface CinemaOption {
  key: string;
  label: string;
  adds: string;
  note: string;
}

export interface CinemaControl {
  name: string;
  label: string;
  kind: 'choice' | 'text';
  options: CinemaOption[];
}

export interface CinemaGroup {
  key: string;
  label: string;
  summary: string;
  controls: CinemaControl[];
}

export interface CinemaVocabulary {
  groups: CinemaGroup[];
  default_negative: string;
  segment_order: { key: string; label: string }[];
  option_count: number;
}

/** One clause of the compiled prompt, and the control that produced it. */
export interface CompiledSegment {
  key: string;
  label: string;
  text: string;
  source: string;
  enabled: boolean;
}

export interface CinemaWarning {
  level: string;
  text: string;
}

export interface CompiledBrief {
  prompt: string;
  negative_prompt: string;
  segments: CompiledSegment[];
  omitted: string[];
  parameters: Record<string, unknown>;
  warnings: CinemaWarning[];
  modality: string;
  aspect_ratio: string;
  storyboard_hint: string;
}

/** The panel's whole state: every control, by name. */
export type CinemaBrief = Record<string, string | string[]>;
