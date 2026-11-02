import { useEffect, useRef, type ReactNode } from 'react';
import { Link, useRouter } from './router.tsx';
import { NAV, RAIL, navEntryFor } from './nav.ts';
import { Icon } from '../components/Icon.tsx';
import { useSession, useStatus } from '../state/session.tsx';
import { useQuery } from '../state/query.ts';
import { connections as connectionsApi } from '../api/endpoints.ts';
import type { Connection } from '../api/types.ts';

/**
 * The permanent frame: a slim icon rail, a quiet top bar, and the content.
 *
 * The rail is the product's spine — six creation surfaces, four places work
 * lands, three configuration screens — because that is the whole application
 * and hiding half of it behind a menu would be a lie about its size. The bar
 * above the content carries only what changes: where you are, whether a key is
 * connected, and the shortcut into the studio.
 */
export function AppShell({ children }: { children: ReactNode }) {
  const { route, navigate } = useRouter();
  const { user, workspace, signOut } = useSession();
  const { status } = useStatus();
  const mainRef = useRef<HTMLElement | null>(null);
  const entry = navEntryFor(route.path);

  // Moving between screens moves the reader to the new content, not back to the
  // top of the document with the same focus ring left behind.
  useEffect(() => {
    window.scrollTo({ top: 0, left: 0, behavior: 'auto' });
    mainRef.current?.focus({ preventScroll: true });
  }, [route.path]);

  const byId = (id: string) => NAV.find((item) => item.id === id);
  const initial = (user?.display_name ?? user?.email ?? '?').trim().charAt(0).toUpperCase();
  // The count is read from the real registry, not inferred from the status
  // document — a connection that exists but is paused still has a key stored,
  // and the pill should say so.
  const links = useQuery<Connection[]>('connections:shell', () => connectionsApi.list(), {
    staleMs: 30_000,
  });
  const connected = (links.data?.length ?? 0) > 0;

  return (
    <div className="hf-app">
      <a className="skip-link" href="#main">
        Skip to content
      </a>

      <nav className="hf-rail" aria-label="Studio sections">
        <Link to="/" className="hf-rail__mark" title="Prompt Layer — home">
          <Icon name="layers" size={17} />
        </Link>

        <div className="hf-rail__group">
          {RAIL.create.map((id) => {
            const item = byId(id);
            if (!item) return null;
            return (
              <Link
                key={item.id}
                to={item.path}
                className="hf-rail__link"
                title={item.summary}
                ariaCurrent={item.id === entry.id ? 'page' : undefined}
              >
                <span className="hf-rail__icon">
                  <Icon name={item.icon} size={18} />
                </span>
                <span>{item.short}</span>
              </Link>
            );
          })}
        </div>

        <div className="hf-rail__sep" />

        <div className="hf-rail__group">
          {RAIL.organise.map((id) => {
            const item = byId(id);
            if (!item) return null;
            return (
              <Link
                key={item.id}
                to={item.path}
                className="hf-rail__link"
                title={item.summary}
                ariaCurrent={item.id === entry.id ? 'page' : undefined}
              >
                <span className="hf-rail__icon">
                  <Icon name={item.icon} size={17} />
                </span>
                <span>{item.short}</span>
              </Link>
            );
          })}
        </div>

        <span className="hf-rail__spacer" />

        <div className="hf-rail__group">
          {RAIL.configure.map((id) => {
            const item = byId(id);
            if (!item) return null;
            return (
              <Link
                key={item.id}
                to={item.path}
                className="hf-rail__link"
                title={item.summary}
                ariaCurrent={item.id === entry.id ? 'page' : undefined}
              >
                <span className="hf-rail__icon">
                  <Icon name={item.icon} size={17} />
                </span>
                <span>{item.short}</span>
              </Link>
            );
          })}

          <button
            type="button"
            className="hf-rail__avatar"
            title={`${user?.display_name ?? user?.email ?? 'Signed out'} — account`}
            onClick={() => {
              if (window.confirm('Sign out of this workspace?')) void signOut();
            }}
          >
            {initial}
          </button>
        </div>
      </nav>

      <div className="hf-main">
        <header className="hf-topbar">
          <div className="hf-topbar__crumb">
            {entry.label}
            <span>·</span>
            <span>{workspace?.name ?? 'No workspace'}</span>
          </div>

          <span className="hf-topbar__spacer" />

          <button
            type="button"
            className={connected ? 'hf-pill hf-pill--live' : 'hf-pill hf-pill--warn'}
            onClick={() => navigate('/connections')}
            title={
              connected
                ? 'Provider connections are configured. Every generation runs on your key.'
                : 'No provider is connected yet. Nothing can be generated until one is.'
            }
          >
            <span className="hf-pill__dot" />
            {connected ? 'BYOK · connected' : 'BYOK · no key yet'}
          </button>

          {status?.mock_provider_enabled ? (
            <span className="hf-pill" title="The local placeholder provider accepts requests without a key.">
              placeholder on
            </span>
          ) : null}

          <Link to="/history" className="hf-btn hf-btn--sm hf-btn--ghost" title="Every request and its outputs">
            <Icon name="clock" size={14} /> Jobs
          </Link>

          <Link to="/image" className="hf-btn hf-btn--sm hf-btn--primary">
            <Icon name="plus" size={14} /> New generation
          </Link>
        </header>

        <main className="hf-content" id="main" ref={mainRef} tabIndex={-1}>
          {children}
        </main>
      </div>
    </div>
  );
}
