import { useEffect, useMemo, useState } from 'react';
import { assets as assetsApi, auth, usage as usageApi } from '../../api/endpoints.ts';
import type { Asset, UsageRow } from '../../api/types.ts';
import { Badge } from '../../components/Badge.tsx';
import { Button } from '../../components/Button.tsx';
import { KeyValueList, Section } from '../../components/Data.tsx';
import { TextInput } from '../../components/Field.tsx';
import { ErrorNotice } from '../../components/ErrorNotice.tsx';
import { Segmented } from '../../components/Controls.tsx';
import { LoadingState } from '../../components/State.tsx';
import { Link } from '../../app/router.tsx';
import { billingSentence, useSession, useStatus } from '../../state/session.tsx';
import { useSettings } from '../../state/settings.tsx';
import { clearPreferences, SECRET_PATTERN } from '../../state/storage.ts';
import { invalidate } from '../../state/query.ts';
import { useToasts } from '../../state/toasts.tsx';
import { formatBytes, formatDateTime } from '../../lib/util.ts';

/**
 * Settings. Profile, workspace, appearance, the local default model, usage and
 * the security note about keys. Nothing here can display a secret, because no
 * secret is ever held anywhere this component can reach.
 */
export function SettingsScreen() {
  const session = useSession();
  const { status } = useStatus();
  const settings = useSettings();
  const toasts = useToasts();
  const [usageError, setUsageError] = useState<unknown>(null);
  const [usageRows, setUsageRows] = useState<UsageRow[] | null>(null);
  const [storageAudit, setStorageAudit] = useState<string | null>(null);
  const [assetStats, setAssetStats] = useState<{ count: number; bytes: number } | null>(null);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const value = await usageApi.list(25);
        if (!cancelled) {
          setUsageRows(value.usage);
          setUsageError(null);
        }
      } catch (err) {
        if (!cancelled) setUsageError(err);
      }
    })();
    void (async () => {
      try {
        const page = await assetsApi.list({ limit: 100 });
        if (cancelled) return;
        setAssetStats({
          count: page.assets.length,
          bytes: page.assets.reduce((total: number, asset: Asset) => total + asset.byte_size, 0),
        });
      } catch {
        if (!cancelled) setAssetStats(null);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const localKeys = useMemo(() => {
    const keys: string[] = [];
    try {
      for (let index = 0; index < localStorage.length; index += 1) {
        const key = localStorage.key(index);
        if (key) keys.push(key);
      }
    } catch {
      return ['localStorage is unavailable in this browser context'];
    }
    return keys;
  }, [storageAudit]);

  const suspicious = localKeys.filter((key) => SECRET_PATTERN.test(key));

  return (
    <div className="page">
      <div className="page__header">
        <div>
          <h1 className="page__heading">Settings</h1>
          <p className="page__subtitle">
            Who you are, what this workspace is, how the studio looks, and what the backend reports about usage. Every
            value below is either a backend record or a local appearance preference.
          </p>
        </div>
      </div>

      <div className="grid grid--2">
        <Section title="Profile">
          <KeyValueList
            rows={[
              { key: 'Display name', value: session.user?.display_name ?? 'not reported' },
              { key: 'Email', value: session.user?.email ?? 'not reported' },
              { key: 'User id', value: <span className="mono">{session.user?.id ?? '—'}</span> },
              { key: 'Account', value: session.user?.is_admin ? 'administrator' : 'member' },
              {
                key: 'Permissions',
                value: session.permissions.length ? session.permissions.join(', ') : 'the backend reported none',
              },
            ]}
          />
        </Section>

        <Section title="Workspace">
          <KeyValueList
            rows={[
              { key: 'Name', value: session.workspace?.name ?? 'not reported' },
              { key: 'Slug', value: <span className="mono">{session.workspace?.slug ?? '—'}</span> },
              { key: 'Role', value: session.workspace?.role ?? 'not reported' },
              {
                key: 'Memberships',
                value: session.workspaces.length ? session.workspaces.map((item) => item.name).join(', ') : 'one workspace',
              },
              {
                key: 'Workspace id',
                value: <span className="mono">{session.workspace?.id ?? '—'}</span>,
              },
            ]}
          />
        </Section>
      </div>

      <PasswordSection />

      <Section title="Appearance">
        <div className="grid grid--3">
          <div className="stack stack--tight">
            <span className="field__label">Theme</span>
            <Segmented
              label="Theme"
              value={settings.theme}
              onChange={settings.setTheme}
              options={[
                { value: 'dark', label: 'Dark' },
                { value: 'light', label: 'Light' },
              ]}
            />
          </div>
          <div className="stack stack--tight">
            <span className="field__label">Motion</span>
            <Segmented
              label="Motion"
              value={settings.motion}
              onChange={settings.setMotion}
              options={[
                { value: 'auto', label: 'Follow the system' },
                { value: 'reduced', label: 'Reduce motion' },
              ]}
            />
          </div>
          <div className="stack stack--tight">
            <span className="field__label">Density</span>
            <Segmented
              label="Density"
              value={settings.density}
              onChange={settings.setDensity}
              options={[
                { value: 'comfortable', label: 'Comfortable' },
                { value: 'compact', label: 'Compact' },
              ]}
            />
          </div>
        </div>
        <p className="dim" style={{ marginTop: 'var(--s3)', fontSize: 'var(--fs-xs)' }}>
          These three choices, plus the default model id, are the only values this client stores in the browser. They are
          written to localStorage under a fixed allow-list of keys; anything else is refused.
        </p>
      </Section>

      <Section title="Storage audit">
        <p className="muted" style={{ margin: 0 }}>
          The client re-reads localStorage and lists every key it finds. A credential can never appear here: the storage
          module refuses any key that matches a secret pattern, and nothing else is written.
        </p>
        <div className="row row--tight" style={{ marginTop: 'var(--s2)' }}>
          <Button size="sm" onClick={() => setStorageAudit(`audited at ${new Date().toISOString()}`)}>
            Re-read localStorage
          </Button>
          <Button
            size="sm"
            onClick={() => {
              clearPreferences();
              setStorageAudit(`cleared at ${new Date().toISOString()}`);
              toasts.push({
                tone: 'info',
                title: 'Local preferences cleared',
                text: 'Theme, motion, density and the default model id were removed.',
              });
            }}
          >
            Clear local preferences
          </Button>
          {storageAudit ? <span className="dim" style={{ fontSize: 'var(--fs-xs)' }}>{storageAudit}</span> : null}
        </div>
        <div className="table-wrap" style={{ marginTop: 'var(--s2)' }}>
          <table className="table table--compact">
            <thead>
              <tr>
                <th>Key</th>
                <th>Verdict</th>
              </tr>
            </thead>
            <tbody>
              {localKeys.map((key) => (
                <tr key={key}>
                  <td className="mono">{key}</td>
                  <td>
                    {SECRET_PATTERN.test(key) ? (
                      <Badge tone="danger">looks like a secret — this should be impossible</Badge>
                    ) : (
                      <Badge tone="ok">an appearance preference</Badge>
                    )}
                  </td>
                </tr>
              ))}
              {!localKeys.length ? (
                <tr>
                  <td colSpan={2} className="dim">
                    Nothing is stored. The client works without local persistence.
                  </td>
                </tr>
              ) : null}
            </tbody>
          </table>
        </div>
        {suspicious.length ? (
          <div className="panel panel--danger">
            <p className="panel__title">Unexpected keys</p>
            <p style={{ margin: 0 }}>
              {suspicious.join(', ')} — these were not written by this client. Check for a browser extension.
            </p>
          </div>
        ) : null}
      </Section>

      <Section title="Backend report">
        {status ? (
          <div className="stack stack--tight">
            <p style={{ margin: 0 }}>{billingSentence(status.billing)}</p>
            <KeyValueList
              rows={[
                { key: 'Version', value: status.version },
                { key: 'Environment', value: status.environment },
                { key: 'Reported time', value: formatDateTime(status.time_utc) },
                { key: 'Database', value: status.database_backend ?? status.database },
                { key: 'Storage', value: status.storage_backend ?? status.storage },
                {
                  key: 'Placeholder provider',
                  value: status.mock_provider_enabled ? 'enabled' : 'disabled',
                },
                {
                  key: 'Missing configuration',
                  value: status.missing_configuration.length ? status.missing_configuration.join(', ') : 'nothing reported',
                },
                { key: 'Parameter types', value: status.parameter_types.join(', ') || 'none reported' },
                { key: 'Camera moves', value: status.camera_moves.join(', ') || 'none reported' },
                { key: 'Reference roles', value: status.reference_roles.join(', ') || 'none reported' },
              ]}
            />
            <div className="stack stack--tight">
              <span className="field__label">Adapters</span>
              {status.adapters.map((adapter) => (
                <div className="row row--between" key={adapter.slug}>
                  <span>
                    {adapter.label} <span className="mono dim">{adapter.slug}</span>
                  </span>
                  <span className="row row--tight">
                    {adapter.is_mock ? <Badge tone="mock">placeholder</Badge> : null}
                    <Badge tone={adapter.discovers_models ? 'ok' : 'plain'}>
                      {adapter.discovers_models ? 'discovers models' : 'manual entry'}
                    </Badge>
                  </span>
                </div>
              ))}
            </div>
          </div>
        ) : (
          <p className="muted" style={{ margin: 0 }}>The backend status has not been read yet.</p>
        )}
      </Section>

      <Section title="Asset footprint">
        {assetStats ? (
          <p style={{ margin: 0 }}>
            The first {assetStats.count} assets the backend lists total {formatBytes(assetStats.bytes)}. This is a count of
            what the API returned, not a storage-accounting figure.
          </p>
        ) : (
          <p className="muted" style={{ margin: 0 }}>The asset list could not be read, so no footprint is shown.</p>
        )}
      </Section>

      <Section title="Usage">
        {usageError ? <ErrorNotice error={usageError} /> : null}
        {usageRows === null && !usageError ? <LoadingState label="Reading usage…" /> : null}
        {usageRows && usageRows.length ? (
          <div className="table-wrap">
            <table className="table table--compact">
              <thead>
                <tr>
                  <th>When</th>
                  <th>Kind</th>
                  <th>Quantity</th>
                  <th>Estimate?</th>
                  <th>Billable here?</th>
                  <th>Note</th>
                </tr>
              </thead>
              <tbody>
                {usageRows.map((row) => (
                  <tr key={row.id}>
                    <td>{formatDateTime(row.created_at)}</td>
                    <td className="mono">{row.kind}</td>
                    <td className="num">
                      {row.quantity} {row.unit}
                    </td>
                    <td>
                      <Badge tone={row.is_estimate ? 'warn' : 'plain'}>
                        {row.is_estimate ? 'estimate' : 'measured'}
                      </Badge>
                    </td>
                    <td>
                      <Badge tone={row.is_billable ? 'accent' : 'plain'}>
                        {row.is_billable ? 'billed here' : 'billed by your provider'}
                      </Badge>
                    </td>
                    <td className="dim">{row.note ?? '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : null}
        {usageRows && !usageRows.length ? (
          <p className="muted" style={{ margin: 0 }}>No usage row was returned for this workspace.</p>
        ) : null}
      </Section>

      <Section title="Keys and this browser">
        <p style={{ margin: 0 }}>
          API keys never enter this application beyond the single form field that posts them. There is no code path that
          puts a key into localStorage, a URL or a log. The rail, the studio and every list show only the masked value the
          backend returns, such as <span className="mono">••••{session.user ? 'abcd' : '0000'}</span>.
        </p>
        <p className="muted" style={{ margin: 'var(--s2) 0 0' }}>
          Sessions use an httpOnly cookie the client cannot read. The CSRF token lives in a module variable and is sent as
          a header on non-GET requests; reloading the page re-reads it from <span className="mono">/api/auth/me</span>.
        </p>
      </Section>

      <Section title="Session">
        <div className="row row--tight">
          <Button onClick={() => void session.refresh()}>Re-read my session</Button>
          <Button
            variant="danger"
            onClick={() => {
              void session.signOut();
            }}
          >
            Sign out
          </Button>
          <Link to="/connections" className="btn">
            Manage API connections
          </Link>
        </div>
      </Section>
    </div>
  );
}

function PasswordSection() {
  const toasts = useToasts();
  const [current, setCurrent] = useState('');
  const [next, setNext] = useState('');
  const [confirm, setConfirm] = useState('');
  const [error, setError] = useState<unknown>(null);
  const [pending, setPending] = useState(false);

  const submit = async () => {
    setError(null);
    if (next !== confirm) {
      setError(new Error('The new password and its confirmation do not match.'));
      return;
    }
    setPending(true);
    try {
      await auth.changePassword({ current_password: current, new_password: next });
      setCurrent('');
      setNext('');
      setConfirm('');
      invalidate('me');
      toasts.push({ tone: 'success', title: 'Password changed' });
    } catch (err) {
      setError(err);
    } finally {
      setPending(false);
    }
  };

  return (
    <Section title="Password">
      <div className="grid grid--3">
        <TextInput
          label="Current password"
          type="password"
          autoComplete="current-password"
          value={current}
          onChange={(event) => setCurrent(event.target.value)}
        />
        <TextInput
          label="New password"
          type="password"
          autoComplete="new-password"
          value={next}
          onChange={(event) => setNext(event.target.value)}
          hint="Sent once; it is not stored in this browser."
        />
        <TextInput
          label="Confirm new password"
          type="password"
          autoComplete="new-password"
          value={confirm}
          onChange={(event) => setConfirm(event.target.value)}
        />
      </div>
      {error ? <ErrorNotice error={error} /> : null}
      <div className="row row--tight">
        <Button variant="primary" onClick={() => void submit()} disabled={!current || !next || pending}>
          {pending ? 'Changing…' : 'Change the password'}
        </Button>
      </div>
    </Section>
  );
}
