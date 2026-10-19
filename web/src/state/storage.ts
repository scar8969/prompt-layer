/**
 * Browser persistence, deliberately tiny and deliberately paranoid.
 *
 * Only the keys in ALLOWED_KEYS are ever written. Anything that looks like a
 * credential is refused outright, so there is no code path that can persist an
 * API key — the credential form keeps its value in component state and posts it
 * once. The CSRF token lives in a module variable in the API client and is
 * never written here either.
 */

const PREFIX = 'studio.' as const;

export const ALLOWED_KEYS = [
  'studio.theme',
  'studio.motion',
  'studio.defaultModelId',
  'studio.density',
] as const;

export type PreferenceKey = (typeof ALLOWED_KEYS)[number];

/**
 * Refuses anything whose key or value smells like a secret. Belt and braces.
 * Exported so Settings can audit localStorage with the same rule the writer uses.
 */
export const SECRET_PATTERN = /(api[-_ ]?key|secret|token|credential|password|bearer|authorization|sk-)/i;

function isAllowed(key: string): key is PreferenceKey {
  return (ALLOWED_KEYS as readonly string[]).includes(key);
}

export function readPreference(key: PreferenceKey): string | null {
  if (!isAllowed(key)) return null;
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

export function writePreference(key: PreferenceKey, value: string | null): void {
  if (!isAllowed(key)) return;
  if (value !== null && SECRET_PATTERN.test(value)) {
    // Never store something that looks like a credential, even by accident.
    return;
  }
  try {
    if (value === null) window.localStorage.removeItem(key);
    else window.localStorage.setItem(key, value);
  } catch {
    /* private mode / disabled storage: preferences simply do not persist */
  }
}

export function clearPreferences(): void {
  for (const key of ALLOWED_KEYS) writePreference(key, null);
  void PREFIX;
}
