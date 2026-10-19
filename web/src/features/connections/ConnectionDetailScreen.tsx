import { useEffect, useState } from 'react';
import { connections as connectionsApi, models as modelsApi } from '../../api/endpoints.ts';
import type { Connection, ConnectionTestResult, Model } from '../../api/types.ts';
import { Badge, MockBadge } from '../../components/Badge.tsx';
import { Button, IconButton } from '../../components/Button.tsx';
import { CodeBlock, KeyValueList, Section } from '../../components/Data.tsx';
import { TextInput } from '../../components/Field.tsx';
import { ErrorNotice } from '../../components/ErrorNotice.tsx';
import { ErrorState, LoadingState } from '../../components/State.tsx';
import { DestructiveAction } from '../../components/Confirm.tsx';
import { Switch } from '../../components/Controls.tsx';
import { Icon } from '../../components/Icon.tsx';
import { Link, useRouter } from '../../app/router.tsx';
import { invalidate } from '../../state/query.ts';
import { useToasts } from '../../state/toasts.tsx';
import { useStatus } from '../../state/session.tsx';
import { formatDateTime, formatDurationMs } from '../../lib/util.ts';

/**
 * One connection. Testing, key rotation, extra headers and the models
 * registered on it. The rotation field is the only place a new secret can be
 * typed, and it is cleared before the response is even inspected.
 */
export function ConnectionDetailScreen({ connectionId }: { connectionId: string }) {
  const { navigate } = useRouter();
  const toasts = useToasts();
  const { status } = useStatus();
  const [connection, setConnection] = useState<Connection | null>(null);
  const [models, setModels] = useState<Model[] | null>(null);
  const [loadError, setLoadError] = useState<unknown>(null);
  const [actionError, setActionError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [nonce, setNonce] = useState(0);

  const [name, setName] = useState('');
  const [baseUrl, setBaseUrl] = useState('');
  const [authScheme, setAuthScheme] = useState('');
  const [authHeader, setAuthHeader] = useState('');
  const [headers, setHeaders] = useState<{ key: string; value: string }[]>([]);
  const [savePending, setSavePending] = useState(false);

  const [rotating, setRotating] = useState(false);
  const [newCredential, setNewCredential] = useState('');
  const [rotatePending, setRotatePending] = useState(false);

  const [testResult, setTestResult] = useState<ConnectionTestResult | null>(null);
  const [testing, setTesting] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    void (async () => {
      try {
        const [record, modelList] = await Promise.all([
          connectionsApi.get(connectionId),
          connectionsApi.models(connectionId),
        ]);
        if (cancelled) return;
        setConnection(record);
        setModels(modelList);
        setName(record.name);
        setBaseUrl(record.base_url ?? '');
        setAuthScheme(record.auth_scheme ?? '');
        setAuthHeader('');
        setHeaders(record.extra_header_names.map((headerName) => ({ key: headerName, value: '' })));
        setLoadError(null);
      } catch (err) {
        if (!cancelled) setLoadError(err);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [connectionId, nonce]);

  const adapter = status?.adapters.find((item) => item.slug === connection?.adapter_type) ?? null;

  if (loading) return <LoadingState label="Reading the connection…" />;
  if (loadError) return <ErrorState error={loadError} onRetry={() => setNonce((n) => n + 1)} />;
  if (!connection) return <ErrorState error={new Error('The backend returned no connection.')} />;

  const save = async () => {
    setSavePending(true);
    setActionError(null);
    const extraHeaders: Record<string, string> = {};
    for (const entry of headers) {
      if (entry.key.trim() && entry.value.trim()) extraHeaders[entry.key.trim()] = entry.value.trim();
    }
    try {
      const updated = await connectionsApi.patch(connection.id, {
        name: name.trim() || connection.name,
        base_url: baseUrl.trim() || undefined,
        auth_scheme: authScheme || null,
        auth_header_name: authHeader.trim() || null,
        extra_headers: extraHeaders,
      });
      setConnection(updated);
      invalidate('connections');
      toasts.push({ tone: 'success', title: 'Connection updated', text: updated.name });
    } catch (err) {
      setActionError(err);
    } finally {
      setSavePending(false);
    }
  };

  const rotate = async () => {
    setRotatePending(true);
    setActionError(null);
    try {
      const updated = await connectionsApi.rotateKey(connection.id, newCredential);
      setNewCredential('');
      setConnection(updated);
      setRotating(false);
      invalidate('connections');
      toasts.push({ tone: 'success', title: 'Key rotated', text: 'The backend stored the new key.' });
    } catch (err) {
      setActionError(err);
    } finally {
      setRotatePending(false);
    }
  };

  const runTest = async () => {
    setTesting(true);
    setActionError(null);
    try {
      const result = await connectionsApi.test(connection.id);
      setTestResult(result);
      invalidate('connections');
    } catch (err) {
      setActionError(err);
    } finally {
      setTesting(false);
    }
  };

  return (
    <div className="page page--wide">
      <div className="page__header">
        <div>
          <div className="row row--tight">
            <Badge tone={connection.is_enabled ? 'ok' : 'plain'}>
              {connection.is_enabled ? 'enabled' : 'disabled'}
            </Badge>
            <Badge tone="plain">{connection.adapter_label}</Badge>
            <MockBadge isMock={adapter?.is_mock} />
          </div>
          <h1 className="page__heading">{connection.name}</h1>
          <p className="page__subtitle">
            {connection.credential.present
              ? `A key is on file: ••••${connection.credential.last4 ?? '????'}. The client only ever sees this mask.`
              : 'No key is on file for this connection.'}
          </p>
        </div>
        <div className="page__actions">
          <Link to="/connections" className="btn btn--sm">
            All connections
          </Link>
          <Button size="sm" onClick={() => void runTest()} disabled={testing}>
            {testing ? 'Testing…' : 'Test connection'}
          </Button>
          <DestructiveAction
            label="Delete connection"
            title="Delete this connection?"
            body={
              <p style={{ margin: 0 }}>
                The stored key is deleted with it. Models registered here are disabled and existing jobs are preserved.
              </p>
            }
            confirmLabel="Delete connection"
            onConfirm={async () => {
              const result = await connectionsApi.remove(connection.id);
              invalidate('connections');
              invalidate('models');
              toasts.push({
                tone: 'info',
                title: 'Connection deleted',
                text: `${result.models_disabled} models disabled, ${result.jobs_preserved} jobs preserved.`,
              });
              navigate('/connections');
            }}
          />
        </div>
      </div>

      {actionError ? <ErrorNotice error={actionError} /> : null}

      {testResult ? (
        <div className={testResult.ok ? 'panel panel--info' : 'panel panel--danger'}>
          <p className="panel__title">
            <Badge tone={testResult.ok ? 'ok' : 'danger'}>{testResult.outcome}</Badge>
          </p>
          <p style={{ margin: 0 }}>{testResult.message}</p>
          <p className="muted" style={{ margin: 'var(--s2) 0 0' }}>
            HTTP {testResult.http_status ?? '—'} · latency {formatDurationMs(testResult.latency_ms)} ·{' '}
            {testResult.models_seen ?? 0} models seen · {formatDateTime(testResult.checked_at)}
          </p>
          {testResult.detail ? (
            <div style={{ marginTop: 'var(--s2)' }}>
              <CodeBlock code={JSON.stringify(testResult.detail, null, 2)} label="detail" />
            </div>
          ) : null}
        </div>
      ) : null}

      <div className="grid grid--2">
        <Section title="Stored record">
          <KeyValueList
            rows={[
              { key: 'Identifier', value: <span className="mono">{connection.id}</span> },
              { key: 'Adapter', value: connection.adapter_label },
              { key: 'Auth scheme', value: connection.auth_scheme ?? 'adapter default' },
              { key: 'Credential present', value: connection.credential.present ? 'yes' : 'no' },
              { key: 'Last four', value: connection.credential.last4 ?? 'not reported' },
              { key: 'Key id', value: connection.credential.key_id ?? 'not reported' },
              { key: 'Fingerprint', value: connection.credential.fingerprint ?? 'not reported' },
              { key: 'Models', value: String(connection.model_count) },
              { key: 'Last test', value: connection.last_test_status ?? 'never run' },
              { key: 'Last test at', value: formatDateTime(connection.last_test_at) },
              { key: 'Created', value: formatDateTime(connection.created_at) },
            ]}
          />
        </Section>

        <Section title="Edit">
          <div className="stack stack--tight">
            <TextInput label="Name" value={name} onChange={(event) => setName(event.target.value)} />
            <TextInput
              label="Base URL"
              value={baseUrl}
              onChange={(event) => setBaseUrl(event.target.value)}
            />
            <TextInput
              label="Auth scheme"
              value={authScheme}
              onChange={(event) => setAuthScheme(event.target.value)}
              hint="Left blank means the adapter default."
            />
            <TextInput
              label="Custom auth header name"
              value={authHeader}
              onChange={(event) => setAuthHeader(event.target.value)}
              hint="Sent once; the backend does not report it back for security."
            />
            <div className="field">
              <span className="field__label">Extra headers</span>
              <p className="field__hint" style={{ marginTop: 0 }}>
                The backend reports which header names exist but not their values, so values have to be re-entered to be
                changed. Existing names are shown below.
              </p>
              {headers.map((entry, index) => (
                <div className="row row--tight" key={index}>
                  <input
                    className="input"
                    aria-label={`Extra header ${index + 1} name`}
                    value={entry.key}
                    onChange={(event) => {
                      const next = [...headers];
                      next[index] = { ...next[index], key: event.target.value };
                      setHeaders(next);
                    }}
                  />
                  <input
                    className="input"
                    type="password"
                    autoComplete="off"
                    placeholder="new value"
                    aria-label={`Extra header ${index + 1} value`}
                    value={entry.value}
                    onChange={(event) => {
                      const next = [...headers];
                      next[index] = { ...next[index], value: event.target.value };
                      setHeaders(next);
                    }}
                  />
                  <IconButton
                    label={`Remove header ${index + 1}`}
                    onClick={() => setHeaders(headers.filter((_, position) => position !== index))}
                  >
                    <Icon name="close" size={13} />
                  </IconButton>
                </div>
              ))}
              <Button size="sm" onClick={() => setHeaders([...headers, { key: '', value: '' }])}>
                Add header
              </Button>
            </div>
            <Switch
              label="Connection enabled"
              hint="A disabled connection keeps its key but cannot serve generations."
              checked={connection.is_enabled}
              onChange={async (next) => {
                try {
                  const updated = await connectionsApi.patch(connection.id, { is_enabled: next });
                  setConnection(updated);
                  invalidate('connections');
                } catch (err) {
                  setActionError(err);
                }
              }}
            />
            <Button variant="primary" onClick={() => void save()} disabled={savePending}>
              {savePending ? 'Saving…' : 'Save changes'}
            </Button>
          </div>
        </Section>
      </div>

      <Section title="Rotate the API key">
        {rotating ? (
          <div className="stack stack--tight">
            <TextInput
              label="New API key"
              type="password"
              autoComplete="off"
              value={newCredential}
              onChange={(event) => setNewCredential(event.target.value)}
              hint="Sent once. It replaces the stored key and is cleared from this form immediately afterwards."
            />
            <div className="row row--tight">
              <Button
                variant="primary"
                onClick={() => void rotate()}
                disabled={!newCredential || rotatePending}
              >
                {rotatePending ? 'Rotating…' : 'Replace the stored key'}
              </Button>
              <Button
                onClick={() => {
                  setNewCredential('');
                  setRotating(false);
                }}
                disabled={rotatePending}
              >
                Cancel
              </Button>
            </div>
          </div>
        ) : (
          <div className="row row--tight">
            <Button onClick={() => setRotating(true)}>Rotate the key</Button>
            <span className="dim" style={{ fontSize: 'var(--fs-xs)' }}>
              Use this when a key was leaked or expired. The old key is replaced server-side.
            </span>
          </div>
        )}
      </Section>

      <Section
        title={`Models on this connection (${models?.length ?? 0})`}
        actions={
          <Link to={`/models`} className="btn btn--sm">
            Open the registry
          </Link>
        }
      >
        {models === null ? (
          <p className="muted" style={{ margin: 0 }}>Reading models…</p>
        ) : models.length ? (
          <div className="table-wrap">
            <table className="table table--compact">
              <thead>
                <tr>
                  <th>Model</th>
                  <th>Modality</th>
                  <th>Modes</th>
                  <th>State</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {models.map((model) => (
                  <tr key={model.id}>
                    <td>
                      <Link to={`/models/${model.id}`}>{model.display_name}</Link>
                      <span className="mono dim" style={{ display: 'block', fontSize: 'var(--fs-xs)' }}>
                        {model.model_key}
                      </span>
                      <MockBadge isMock={model.provider.is_mock} />
                    </td>
                    <td className="mono">{model.modality}</td>
                    <td className="dim">{model.modes.join(', ') || 'none declared'}</td>
                    <td>
                      <Badge tone={model.is_enabled ? 'ok' : 'plain'}>
                        {model.is_enabled ? 'enabled' : 'disabled'}
                      </Badge>
                    </td>
                    <td>
                      <Button
                        size="sm"
                        onClick={async () => {
                          try {
                            await modelsApi.patch(model.id, { is_enabled: !model.is_enabled });
                            setNonce((n) => n + 1);
                            invalidate('models');
                          } catch (err) {
                            setActionError(err);
                          }
                        }}
                      >
                        {model.is_enabled ? 'Disable' : 'Enable'}
                      </Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : (
          <p className="muted" style={{ margin: 0 }}>
            No model is registered on this connection yet. A connection alone cannot generate anything.
          </p>
        )}
      </Section>

      <Section title="Adapter notes">
        {adapter ? (
          <div className="stack stack--tight">
            <p style={{ margin: 0 }}>{adapter.summary}</p>
            <div className="row row--tight" style={{ flexWrap: 'wrap' }}>
              <Badge tone={adapter.discovers_models ? 'ok' : 'plain'}>
                {adapter.discovers_models ? 'discovers models automatically' : 'models added manually'}
              </Badge>
              {adapter.supports_cancel ? <Badge tone="plain">jobs can be cancelled</Badge> : <Badge tone="warn">no cancellation support</Badge>}
              {(adapter.auth_schemes ?? []).map((scheme) => (
                <Badge key={scheme} tone="plain">
                  {scheme}
                </Badge>
              ))}
            </div>
            {adapter.is_mock ? (
              <p className="muted" style={{ margin: 0 }}>
                This is the local development placeholder adapter. Anything it produces carries the placeholder badge
                wherever it appears.
              </p>
            ) : null}
          </div>
        ) : (
          <p className="muted" style={{ margin: 0 }}>
            The backend does not list an adapter with the slug this connection uses.
          </p>
        )}
      </Section>
    </div>
  );
}
