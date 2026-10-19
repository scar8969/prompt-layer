import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { readPreference, writePreference } from './storage.ts';

export type ThemeChoice = 'dark' | 'light';
export type MotionChoice = 'auto' | 'reduced';
export type DensityChoice = 'comfortable' | 'compact';

interface SettingsValue {
  theme: ThemeChoice;
  motion: MotionChoice;
  density: DensityChoice;
  defaultModelId: string | null;
  setTheme: (value: ThemeChoice) => void;
  setMotion: (value: MotionChoice) => void;
  setDensity: (value: DensityChoice) => void;
  setDefaultModelId: (value: string | null) => void;
}

const SettingsContext = createContext<SettingsValue | null>(null);

/**
 * Appearance and the default-model preference. Only these four values are ever
 * persisted; see state/storage.ts for why a credential can never end up here.
 */
export function SettingsProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<ThemeChoice>(
    () => (readPreference('studio.theme') === 'light' ? 'light' : 'dark'),
  );
  const [motion, setMotionState] = useState<MotionChoice>(
    () => (readPreference('studio.motion') === 'reduced' ? 'reduced' : 'auto'),
  );
  const [density, setDensityState] = useState<DensityChoice>(
    () => (readPreference('studio.density') === 'compact' ? 'compact' : 'comfortable'),
  );
  const [defaultModelId, setDefaultModelState] = useState<string | null>(
    () => readPreference('studio.defaultModelId'),
  );

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
  }, [theme]);

  useEffect(() => {
    if (motion === 'reduced') document.documentElement.dataset.motion = 'reduced';
    else delete document.documentElement.dataset.motion;
  }, [motion]);

  useEffect(() => {
    document.documentElement.dataset.density = density;
  }, [density]);

  const value = useMemo<SettingsValue>(
    () => ({
      theme,
      motion,
      density,
      defaultModelId,
      setTheme: (next) => {
        setThemeState(next);
        writePreference('studio.theme', next);
      },
      setMotion: (next) => {
        setMotionState(next);
        writePreference('studio.motion', next);
      },
      setDensity: (next) => {
        setDensityState(next);
        writePreference('studio.density', next);
      },
      setDefaultModelId: (next) => {
        setDefaultModelState(next);
        writePreference('studio.defaultModelId', next);
      },
    }),
    [theme, motion, density, defaultModelId],
  );

  return <SettingsContext.Provider value={value}>{children}</SettingsContext.Provider>;
}

export function useSettings(): SettingsValue {
  const value = useContext(SettingsContext);
  if (!value) throw new Error('useSettings must be used inside <SettingsProvider>');
  return value;
}
