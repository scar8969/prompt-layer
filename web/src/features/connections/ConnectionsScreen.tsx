import { useEffect, useState } from 'react';
import { connections as connectionsApi, system } from '../../api/endpoints.ts';
import type { AdapterType, Connection } from '../../api/types.ts';
import { Badge, MockBadge } from '../../components/Badge.tsx';
import { Button } from '../../components/Button.tsx';
import { Section } from '../../components/Data.tsx';
import { Dialog } from '../../components/Dialog.tsx';
import { ErrorNotice } from '../../components/ErrorNotice.tsx';
import { EmptyState, ErrorState, LoadingState } from '../../components/State.tsx';
import { DestructiveAction } from '../../components/Confirm.tsx';
import { Icon } from '../../components/Icon.tsx';
import { Link, useRouter } from '../../app/router.tsx';
import { invalidate, useQuery } from '../../state/query.ts';
import { useToasts } from '../../state/toasts.tsx';
import { formatDateTime } from '../../lib/util.ts';
import { ConnectionWizard } from './ConnectionWizard.tsx';

/**
 * API Connections. This is the only screen where a credential is typed, and it
 * is typed exactly once. What is displayed afterwards is the masked summary the
 * backend returns.
 */
export function ConnectionsScreen() {
  const toasts = useToasts();
  const { navigate } = useRouter();
  const connections = useQuery<Connection[]>('connections', () => connectionsApi.list(), { staleMs: 10_000 });
  const adapters = useQuery<AdapterType[]>('connection-types', () => system.status().then((status) => status.adapters), {
    staleMs: 60_000,
  });
  const [wizardOpen, setWizardOpen] = useState(false);
  const [nonce, setNonce] = useState(0);

  useEffect(() => {
    if (nonce > 0) connections.reload();
  }, [nonce]);

  const list = connections.data ?? [];
  const adapterList = adapters.data ?? [];

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__heading">API Connections</h1>
          <p className="page__subtitle">
            Bring your own key and your own model. A connection is an endpoint plus a credential the backend stores; the
            key is sent once and never rendered back.
          </p>
        </div>
        <div className="page__actions">
          <Button size="sm" onClick={() => { connections.reload(); adapters.reload(); }}>
            Refresh
          </Button>
          <Button variant="primary" onClick={() => setWizardOpen(true)} disabled={!adapterList.length}>
            <span className="btn__icon">
              <Icon name="plug" size={14} />
            </span>
            New connection
          </Button>
        </div>
      </div>

      {!adapterList.length && !adapters.loading ? (
        <div className="panel panel--warn">
          <p className="panel__title">
            <Icon name="alert" size={13} /> The backend reported no adapter types
          </p>
          <p style={{ margin: 0 }}>
            Without an adapter there is no way to describe how a provider is called, so the wizard stays closed. This is
            a backend configuration problem, not something the client can work around.
          </p>
        </div>
      ) : null}

      {connections.loading ? <LoadingState label="Reading your connections…" /> : null}
      {connections.error ? <ErrorState error={connections.error} onRetry={connections.reload} /> : null}

      {!connections.loading && !connections.error && !list.length ? (
        <EmptyState
          icon="plug"
          title="No connection yet"
          body="Add a provider endpoint and key. The backend tests it, and the models you register on it then appear in the studios."
          action={
            <Button variant="primary" onClick={() => setWizardOpen(true)} disabled={!adapterList.length}>
              Start the connection wizard
            </Button>
          }
        />
      ) : null}

      {list.length ? (
        <Section title={`${list.length} connection${list.length === 1 ? '' : 's'}`}>
          <div className="stack">
            {list.map((connection) => (
              <div className="card" key={connection.id}>
                <div className="card__head">
                  <Link to={`/connections/${connection.id}`} className="card__title">
                    {connection.name}
                  </Link>
                  <Badge tone={connection.is_enabled ? 'ok' : 'plain'}>
                    {connection.is_enabled ? 'enabled' : 'disabled'}
                  </Badge>
                  <Badge tone="plain">{connection.adapter_label}</Badge>
                  <MockBadge isMock={adapterList.find((item) => item.slug === connection.adapter_type)?.is_mock} />
                </div>
                <div className="job__meta" style={{ marginTop: 'var(--s2)' }}>
                  <span className="mono">{connection.base_url ?? 'no base URL reported'}</span>
                  <span className="mono">
                    {connection.credential.present
                      ? connection.credential.last4
                        ? `key ••••${connection.credential.last4}`
                        : 'key on file'
                      : 'no key on file'}
                  </span>
                  <span>{connection.model_count} model{connection.model_count === 1 ? '' : 's'}</span>
                  <span>last test: {connection.last_test_status ?? 'never run'}</span>
                  <span>last used: {connection.last_used_at ? formatDateTime(connection.last_used_at) : 'never'}</span>
                </div>
                <div className="row row--tight" style={{ marginTop: 'var(--s3)' }}>
                  <Link to={`/connections/${connection.id}`} className="btn btn--sm">
                    Manage
                  </Link>
                  <Link to={`/models?connection=${connection.id}`} className="btn btn--sm">
                    Models
                  </Link>
                  <DestructiveAction
                    label="Delete"
                    title={`Delete "${connection.name}"?`}
                    body={
                      <p style={{ margin: 0 }}>
                        The connection and its stored key are removed. The backend reports how many models were disabled
                        and how many jobs were preserved.
                      </p>
                    }
                    confirmLabel="Delete connection"
                    onConfirm={async () => {
                      const result = await connectionsApi.remove(connection.id);
                      invalidate('connections');
                      invalidate('models');
                      connections.reload();
                      setNonce((n) => n + 1);
                      toasts.push({
                        tone: 'info',
                        title: 'Connection deleted',
                        text: `${result.models_disabled} models disabled, ${result.jobs_preserved} jobs preserved.`,
                      });
                    }}
                  />
                </div>
              </div>
            ))}
          </div>
        </Section>
      ) : null}

      <Dialog
        open={wizardOpen}
        onClose={() => setWizardOpen(false)}
        title="Connect a provider"
        width="wide"
      >
        {adapters.loading ? (
          <LoadingState label="Reading the adapter types…" />
        ) : adapters.error ? (
          <ErrorNotice error={adapters.error} onRetry={adapters.reload} />
        ) : (
          <ConnectionWizard
            adapters={adapterList}
            onCancel={() => setWizardOpen(false)}
            onDone={(connection) => {
              setWizardOpen(false);
              invalidate('connections');
              invalidate('models');
              connections.reload();
              setNonce((n) => n + 1);
              navigate(`/connections/${connection.id}`);
            }}
          />
        )}
      </Dialog>
    </div>
  );
}
