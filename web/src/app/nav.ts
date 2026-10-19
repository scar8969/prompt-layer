// The eleven surfaces of the studio. Every entry is a real screen; none of them
// is a placeholder. The icon name indexes into components/Icon.tsx.

export interface NavEntry {
  id: string;
  label: string;
  path: string;
  icon: string;
  group: 'create' | 'organise' | 'configure';
  summary: string;
}

export const NAV: NavEntry[] = [
  {
    id: 'home',
    label: 'Home',
    path: '/',
    icon: 'home',
    group: 'create',
    summary: 'What this studio is, and where to start.',
  },
  {
    id: 'image',
    label: 'Image Studio',
    path: '/image',
    icon: 'image',
    group: 'create',
    summary: 'Compose a prompt, choose a model, generate images.',
  },
  {
    id: 'video',
    label: 'Video Studio',
    path: '/video',
    icon: 'video',
    group: 'create',
    summary: 'Text, image and video driven generation.',
  },
  {
    id: 'projects',
    label: 'Projects',
    path: '/projects',
    icon: 'folder',
    group: 'organise',
    summary: 'Group assets and jobs into projects.',
  },
  {
    id: 'assets',
    label: 'Asset Library',
    path: '/assets',
    icon: 'library',
    group: 'organise',
    summary: 'Every uploaded file and generated output.',
  },
  {
    id: 'presets',
    label: 'Presets & Workflows',
    path: '/presets',
    icon: 'sliders',
    group: 'organise',
    summary: 'Reusable prompt templates and settings.',
  },
  {
    id: 'characters',
    label: 'Character Library',
    path: '/characters',
    icon: 'person',
    group: 'organise',
    summary: 'Reusable character descriptions and references.',
  },
  {
    id: 'history',
    label: 'History & Jobs',
    path: '/history',
    icon: 'clock',
    group: 'organise',
    summary: 'Every request, its state, cost and outputs.',
  },
  {
    id: 'models',
    label: 'Models',
    path: '/models',
    icon: 'chip',
    group: 'configure',
    summary: 'The model registry with full capability profiles.',
  },
  {
    id: 'connections',
    label: 'API Connections',
    path: '/connections',
    icon: 'plug',
    group: 'configure',
    summary: 'Connect a provider with your own key and endpoint.',
  },
  {
    id: 'settings',
    label: 'Settings',
    path: '/settings',
    icon: 'gear',
    group: 'configure',
    summary: 'Profile, workspace, appearance, usage and security.',
  },
];

export const NAV_GROUPS: { id: NavEntry['group']; label: string }[] = [
  { id: 'create', label: 'Create' },
  { id: 'organise', label: 'Organise' },
  { id: 'configure', label: 'Configure' },
];

export function navEntryFor(path: string): NavEntry {
  const clean = path.split('?')[0].replace(/\/+$/, '') || '/';
  const exact = NAV.find((entry) => entry.path === clean);
  if (exact) return exact;
  const prefix = NAV.filter((entry) => entry.path !== '/' && clean.startsWith(entry.path)).sort(
    (a, b) => b.path.length - a.path.length,
  )[0];
  return prefix ?? NAV[0];
}
