import { useEffect } from 'react';
import { RouterProvider, useRouter, matchPath } from './app/router.tsx';
import { AppShell } from './app/AppShell.tsx';
import { SettingsProvider } from './state/settings.tsx';
import { ToastProvider } from './state/toasts.tsx';
import { SessionProvider, StatusProvider, useSession } from './state/session.tsx';
import { AuthScreen } from './features/auth/AuthScreen.tsx';
import { LoadingState } from './components/State.tsx';
import { HomeScreen } from './features/home/HomeScreen.tsx';
import { PromptLabScreen } from './features/prompt/PromptLabScreen.tsx';
import { ImageStudioScreen } from './features/image-studio/ImageStudioScreen.tsx';
import { VideoStudioScreen } from './features/video-studio/VideoStudioScreen.tsx';
import { ProjectsScreen } from './features/projects/ProjectsScreen.tsx';
import { ProjectDetailScreen } from './features/projects/ProjectDetailScreen.tsx';
import { AssetsScreen } from './features/assets/AssetsScreen.tsx';
import { PresetsScreen } from './features/presets/PresetsScreen.tsx';
import { CharactersScreen } from './features/characters/CharactersScreen.tsx';
import { JobsScreen } from './features/jobs/JobsScreen.tsx';
import { JobDetailScreen } from './features/jobs/JobDetailScreen.tsx';
import { ModelsScreen } from './features/models/ModelsScreen.tsx';
import { ModelDetailScreen } from './features/models/ModelDetailScreen.tsx';
import { ConnectionsScreen } from './features/connections/ConnectionsScreen.tsx';
import { ConnectionDetailScreen } from './features/connections/ConnectionDetailScreen.tsx';
import { SettingsScreen } from './features/settings/SettingsScreen.tsx';
import { EmptyState } from './components/State.tsx';

const AUTH_ROUTES: { path: string; mode: 'login' | 'register' | 'forgot' | 'reset' }[] = [
  { path: '/login', mode: 'login' },
  { path: '/register', mode: 'register' },
  { path: '/forgot-password', mode: 'forgot' },
  { path: '/reset-password', mode: 'reset' },
];

function ScreenForPath({ path }: { path: string }) {
  if (matchPath('/', path)) return <HomeScreen />;
  if (matchPath('/prompt', path)) return <PromptLabScreen />;
  if (matchPath('/image', path)) return <ImageStudioScreen />;
  if (matchPath('/video', path)) return <VideoStudioScreen />;
  if (matchPath('/projects', path)) return <ProjectsScreen />;
  const project = matchPath('/projects/:id', path);
  if (project) return <ProjectDetailScreen projectId={project.id} />;
  if (matchPath('/assets', path)) return <AssetsScreen />;
  if (matchPath('/presets', path)) return <PresetsScreen />;
  if (matchPath('/characters', path)) return <CharactersScreen />;
  if (matchPath('/history', path)) return <JobsScreen />;
  const job = matchPath('/history/:id', path);
  if (job) return <JobDetailScreen jobId={job.id} />;
  if (matchPath('/models', path)) return <ModelsScreen />;
  const model = matchPath('/models/:id', path);
  if (model) return <ModelDetailScreen modelId={model.id} />;
  if (matchPath('/connections', path)) return <ConnectionsScreen />;
  const connection = matchPath('/connections/:id', path);
  if (connection) return <ConnectionDetailScreen connectionId={connection.id} />;
  if (matchPath('/settings', path)) return <SettingsScreen />;
  return (
    <EmptyState
      icon="alert"
      title="No screen at this address"
      body={
        <p style={{ margin: 0 }}>
          <span className="mono">{path}</span> does not match a surface in this studio.
        </p>
      }
    />
  );
}

function CurrentScreen() {
  const { route, navigate } = useRouter();
  const session = useSession();

  const authRoute = AUTH_ROUTES.find((entry) => matchPath(entry.path, route.path));

  useEffect(() => {
    if (session.phase !== 'anonymous') return;
    if (authRoute) return;
    const next = encodeURIComponent(route.path + (route.params.toString() ? `?${route.params}` : ''));
    navigate(`/login?next=${next}`, { replace: true });
  }, [session.phase, authRoute, route.path, route.params, navigate]);

  if (authRoute) {
    if (session.phase === 'authenticated') {
      const next = route.params.get('next') ?? '/';
      return <RedirectNotice to={next} />;
    }
    return <AuthScreen mode={authRoute.mode} />;
  }

  if (session.phase === 'checking') {
    return (
      <div className="auth">
        <div className="auth__card">
          <LoadingState label="Checking your session…" />
        </div>
      </div>
    );
  }

  if (session.phase === 'anonymous') {
    return (
      <div className="auth">
        <div className="auth__card">
          <LoadingState label="Taking you to the sign-in screen…" />
        </div>
      </div>
    );
  }

  return (
    <AppShell>
      <ScreenForPath path={route.path} />
    </AppShell>
  );
}

/** Sent here when an already-authenticated visitor opens a sign-in URL. */
function RedirectNotice({ to }: { to: string }) {
  const { navigate } = useRouter();
  useEffect(() => {
    navigate(to, { replace: true });
  }, [to, navigate]);
  return <LoadingState label="You are already signed in." />;
}

export default function App() {
  return (
    <SettingsProvider>
      <ToastProvider>
        <RouterProvider>
          <SessionProvider>
            <StatusProvider>
              <CurrentScreen />
            </StatusProvider>
          </SessionProvider>
        </RouterProvider>
      </ToastProvider>
    </SettingsProvider>
  );
}
