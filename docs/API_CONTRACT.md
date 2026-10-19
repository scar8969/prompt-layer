# Studio API contract (v1)

This file is the agreement between the HTTP layer in studio/http.py and the React
client in web/src. It is written before either side so that both are built against
the same shapes. Where the implementation and this file disagree, the implementation
is wrong.

## Envelope

Every response is JSON. A success response is an object whose payload lives under a
key named after the resource: user, workspace, workspaces, permissions, connection,
connections, test, types, model, models, schema, job, jobs, events, project, projects,
asset, assets, preset, presets, prompt, prompts, character, characters, usage, status.
Simple acknowledgements are {ok: true, ...}.

Every failure uses HTTP 4xx/5xx and this body, produced by ApiError.to_payload:

    {"error": {"code": "not_found", "message": "...", "hint": "...",
                  "details": {...}, "request_id": "..."}}

Codes the client must handle by name: unauthenticated, forbidden, not_found,
validation_failed, conflict, rate_limit_exceeded, capability_unsupported,
configuration_required, auth_failed, endpoint_not_found, unsupported_operation,
invalid_model_id, rate_limited, timeout, invalid_response, network_restricted,
provider_error, insufficient_balance, output_download_failed, storage_failed,
internal.

## Auth and CSRF

Session cookie studio_session (httpOnly). CSRF token is returned in the login/register
response body as session.csrf_token and must be sent as the X-CSRF-Token header on
every non-GET request. The client keeps it in memory only.

## System

GET /api/system/status -> {status:{version, environment, time_utc, database, storage,
  adapters:[{slug,label,summary,is_mock,discovers_models,supports_cancel,auth_schemes,
  default_modes}], mock_provider_enabled, billing:{mode:"byok", platform_credits:false,
  note}, missing_configuration:[...], parameter_types:[...], camera_moves:[...],
  reference_roles:[...], database_backend, storage_backend}}

## Auth

POST /api/auth/register {email, password, display_name} -> {user, workspace, session:{csrf_token}}
POST /api/auth/login    {email, password}                  -> {user, workspace, session:{csrf_token}}
POST /api/auth/logout                                       -> {ok:true}
GET  /api/auth/me       -> {user, workspace, workspaces:[{id,name,slug,role}], permissions:[...]}
POST /api/auth/change-password {current_password, new_password} -> {ok:true}
POST /api/auth/forgot-password {email} -> {ok:true, delivery:"manual"} (dev returns reset_token)
POST /api/auth/reset-password  {token, new_password} -> {ok:true}

## Connections (BYOK / BYOM)

GET    /api/connections -> {connections:[{id, name, adapter_type, adapter_label, base_url,
         auth_scheme, model_count, is_enabled, last_test_status, last_test_at, last_used_at,
         created_at, credential:{present, last4, key_id, fingerprint}, extra_header_names:[]}]}
GET    /api/connections/types -> {types:[...]}   (same shape as adapters above)
POST   /api/connections {name, adapter_type, base_url, credential, auth_scheme,
         auth_header_name, extra_headers, config, allowed_hosts, models:[ModelInput]}
         -> {connection, models:[Model]}
GET    /api/connections/{id} -> {connection}
PATCH  /api/connections/{id} {name?, base_url?, is_enabled?, extra_headers?, config?,
         auth_scheme?, auth_header_name?} -> {connection}
DELETE /api/connections/{id} -> {ok:true, models_disabled:n, jobs_preserved:n}
POST   /api/connections/{id}/test -> {test:{outcome, ok, http_status, latency_ms, message,
         detail, models_seen, checked_at}}
POST   /api/connections/{id}/rotate-key {credential} -> {connection}
GET    /api/connections/{id}/models -> {models:[...]}
POST   /api/connections/{id}/models {ModelInput} -> {model}

ModelInput = {model_key, display_name, modality, modes:[string], capabilities:{...},
  parameter_schema:{parameters:[ParameterSpec]}, defaults:{}, limits:{}, source}

ParameterSpec = {name, type, label, description, default, required, minimum, maximum,
  step, options:[], option_labels:{}, item_type, min_items, max_items, max_length,
  pattern, prompt_mapped, api_key, group, advanced}

Capabilities = {modality, modes, aspect_ratios, resolutions, durations_seconds,
  frame_rates, reference_roles, max_reference_images, max_outputs,
  supports_negative_prompt, supports_seed, supports_audio, supports_mask,
  native_camera_moves, restrictions, notes, max_input_bytes}

## Models

GET   /api/models?modality=&source=&favorite=&enabled=&q= -> {models:[Model]}
GET   /api/models/{id} -> {model}
GET   /api/models/{id}/capabilities -> {capabilities, modes:[{key,label,supported}],
        camera_moves:[{key,label,phrase,is_native}], reference_roles:[...]}
GET   /api/models/{id}/parameters -> {schema:{parameters:[...]}, parameter_types:[...]}
PATCH /api/models/{id} {display_name?, is_enabled?, is_favorite?, is_default?,
        modes?, capabilities?, parameter_schema?, defaults?} -> {model}
DELETE /api/models/{id} -> {ok:true}

Model = {id, connection_id, workspace_id, model_key, display_name, modality, modes,
  capabilities, parameter_schema, defaults, limits, source, is_enabled, is_favorite,
  is_default, created_at, updated_at, provider:{connection_name, adapter_type,
  adapter_label, base_url, is_mock, connection_enabled}}

## Generations

POST /api/generations/images {model_id?|(connection_id, model_key), mode, prompt,
       negative_prompt, parameters, references:[{asset_id?|url, role}], mask_asset_id,
       count, seed, project_id, preset_id, client_request_id} -> {job}
POST /api/generations/videos {same, plus duration_seconds, aspect_ratio, resolution,
       camera_move, generate_audio, first_frame_asset_id, last_frame_asset_id} -> {job}
POST /api/generations/{job_id}/variations {count, prompt?, seed?} -> {job}
POST /api/generations/{job_id}/edit {mode:"inpaint"|"outpaint", prompt, mask_asset_id,
       expand:{left,right,top,bottom}, parameters} -> {job}
POST /api/generations/{job_id}/extend {seconds, prompt} -> {job}
POST /api/generations/{job_id}/transform {mode, prompt, parameters} -> {job}

## Jobs

GET  /api/jobs?state=&modality=&project_id=&model_id=&limit=&cursor= -> {jobs:[Job], next_cursor}
GET  /api/jobs/{id} -> {job}
GET  /api/jobs/{id}/events -> {events:[{id, kind, state, message, detail, created_at}]}
POST /api/jobs/{id}/cancel -> {job}
POST /api/jobs/{id}/retry  -> {job}   (creates a new job; never duplicates a billable one)

Job = {id, workspace_id, project_id, model_id, model:{model_key, display_name, modality,
  provider:{connection_name, adapter_label, is_mock}}, mode, kind, state, prompt,
  negative_prompt, parameters, references, seed, count, provider_job_id, provider_status,
  progress, progress_is_estimate, message, error:{code,message,hint,details}?, attempt,
  max_attempts, is_mock, cost_estimate, outputs:[Output], created_at, started_at,
  finished_at, elapsed_ms, parent_job_id, relation}

Output = {id, asset_id, url, content_type, width, height, duration_seconds, file_name,
  byte_size, seed, metadata, is_mock}

## Projects and assets

GET    /api/projects -> {projects:[Project]}
POST   /api/projects {name, description} -> {project}
GET    /api/projects/{id} -> {project, assets:[Asset]}
PATCH  /api/projects/{id} {name?, description?, is_favorite?, is_archived?} -> {project}
DELETE /api/projects/{id} -> {ok:true, assets_unassigned:n}
POST   /api/projects/{id}/assets {asset_ids:[...]} -> {project}

GET    /api/assets?project_id=&kind=&favorite=&q=&limit=&cursor= -> {assets:[Asset], next_cursor}
POST   /api/assets {file_name, content_type, data_base64, project_id?, kind?} -> {asset}
       (max upload bytes from configuration; rejected with validation_failed above it)
GET    /api/assets/{id} -> {asset}
PATCH  /api/assets/{id} {name?, project_id?, is_favorite?} -> {asset}
DELETE /api/assets/{id} -> {ok:true}
GET    /api/assets/{id}/content   -> bytes (Content-Type from the stored signature)
GET    /api/assets/{id}/thumbnail -> bytes

Asset = {id, workspace_id, project_id, job_id, kind:"image"|"video"|"upload"|"mask",
  name, content_type, byte_size, width, height, duration_seconds, is_favorite, is_mock,
  content_url, thumbnail_url, created_at, source:"generated"|"uploaded"}

## Library

GET    /api/presets?modality=&category=&q= -> {presets:[Preset]}
POST   /api/presets {name, description, modality, category, prompt_template,
       negative_prompt_template, aspect_ratio, model_preference, parameters, camera, notes} -> {preset}
GET    /api/presets/{id} -> {preset}
PATCH  /api/presets/{id} -> {preset}
DELETE /api/presets/{id} -> {ok:true}
POST   /api/presets/{id}/favorite {favorite} -> {preset}

GET    /api/prompts?q=&model_id= -> {prompts:[Prompt]}
POST   /api/prompts {text, title?, negative_prompt?, model_id?, tags?, variables?} -> {prompt}
PATCH  /api/prompts/{id} -> {prompt}
DELETE /api/prompts/{id} -> {ok:true}

GET    /api/characters -> {characters:[Character]}
POST   /api/characters {name, description, attributes, default_prompt, preferred_model_id,
       tags, reference_asset_ids} -> {character}
GET    /api/characters/{id} -> {character, references:[...]}
PATCH  /api/characters/{id} -> {character}
DELETE /api/characters/{id} -> {ok:true}
POST   /api/characters/{id}/references {asset_id, role} -> {character}

GET    /api/usage?limit= -> {usage:[{id, job_id, kind, model_id, provider, quantity, unit,
       is_estimate, is_billable, note, created_at}], billing:{mode:"byok",
       platform_credits:false, note}}

## Honesty rules the client must respect

1. If model.provider.is_mock or job.is_mock is true, the UI shows a test-mode badge on
   the model, the job and the output. Never present mock output as a real generation.
2. cost_estimate.is_estimate is always shown with the word estimate. When
   is_billable is false the UI says the request is billed by the user's own provider.
3. A job in state queued or processing shows no percentage unless
   progress_is_estimate is true, in which case it is labelled approximate.
4. Modes absent from model.modes are disabled with the reason from capabilities, never
   hidden silently and never enabled optimistically.
5. A camera move whose is_native is false is labelled "prompt wording" and never
   presented as a provider parameter.
