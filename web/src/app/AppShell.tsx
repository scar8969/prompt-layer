import { useEffect, useRef, type ReactNode } from 'react';
import { Link, useRouter } from './router.tsx';
import { NAV, NAV_GROUPS, navEntryFor } from './nav.ts';
import { Icon } from '../components/Icon.tsx';
import { Badge } from '../components/Badge.tsx';
import { IconButton } from '../components/Button.tsx';
import { useSession, useStatus } from '../state/session.tsx';

/**
 * The permanent frame: a rail of the eleven surfaces, a top bar that names where
 * you are, and the main region. The rail collapses into a bottom tab bar on
 * narrow screens, which is a CSS-only change; nothing here duplicates the nav.
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

  return (
    <div className="app">
      <a className="skip-link" href="#main">
        Skip to content
      </a>

      <header className="topbar">
        <div className="topbar__title">
          <h1>{entry.label}</h1>
          <span className="dim" style={{ fontSize: 'var(--fs-xs)' }}>
            {workspace?.name ?? 'No workspace reported'}
          </span>
        </div>
        <span className="topbar__spacer" />
        <div className="topbar__actions">
          {status ? (
            <Badge tone={status.mock_provider_enabled ? 'mock' : 'plain'}>
              {status.mock_provider_enabled
                ? 'placeholder provider enabled'
                : 'placeholder provider off'}
            </Badge>
          ) : null}
          <Link to="/history" className="btn btn--sm btn--ghost">
            <Icon name="clock" size={14} /> Jobs
          </Link>
          <IconButton
            label="Appearance and preferences"
            onClick={() => navigate('/settings')}
          >
            <Icon name="gear" size={15} />
          </IconButton>
        </div>
      </header>

      <nav className="rail" aria-label="Studio sections">
        <div className="rail__brand">
          <span className="rail__mark" aria-hidden="true">
            <Icon name="layers" size={18} />
          </span>
          <span className="rail__name">Prompt Layer</span>
        </div>

        {NAV_GROUPS.map((group) => {
          const entries = NAV.filter((item) => item.group === group.id);
          if (!entries.length) return null;
          return (
            <div className="rail__group" key={group.id}>
              <span className="rail__group-label">{group.label}</span>
              {entries.map((item) => {
                const current = item.id === entry.id;
                return (
                  <Link
                    key={item.id}
                    to={item.path}
                    className="rail__link"
                    title={item.summary}
                    ariaCurrent={current ? 'page' : undefined}
                  >
                    <span
                      className="rail__icon"
                      aria-hidden="true"
                      style={{ display: 'inline-flex' }}
                    >
                      <Icon name={item.icon} size={16} />
                    </span>
                    <span>{item.label}</span>
                  </Link>
                );
              })}
            </div>
          );
        })}

        <div className="rail__footer">
          <span>{user?.display_name ?? user?.email ?? 'Signed out'}</span>
          <button
            type="button"
            className="btn btn--sm btn--ghost"
            onClick={() => {
              void signOut();
            }}
          >
            Sign out
          </button>
        </div>
      </nav>

      <main className="main" id="main" ref={mainRef} tabIndex={-1}>
        {children}
      </main>
    </div>
  );
}
