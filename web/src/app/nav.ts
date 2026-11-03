// The studio's surfaces. Every entry is a real screen; none of them is a
// placeholder. The icon name indexes into components/Icon.tsx.
//
// The order is the product's own: you look first (Explore), then you shape a
// shot (Cinema), then you run it (Image / Video), then you check what the
// provider will do to the prompt before it is spent (Prompt Lab). Organise and
// configure sit below the line, where they belong.

export interface NavEntry {
  id: string;
  label: string;
  /** Short label for the icon rail. */
  short: string;
  path: string;
  icon: string;
  group: 'create' | 'organise' | 'configure';
  summary: string;
}

export const NAV: NavEntry[] = [
  {
    id: 'home',
    label: 'Home',
    short: 'Home',
    path: '/',
    icon: 'home',
    group: 'create',
    summary: 'What this studio is, and where to start.',
  },
  {
    id: 'chat',
    label: 'Chat',
    short: 'Chat',
    path: '/chat',
    icon: 'send',
    group: 'create',
    summary: 'A conversation with the model you registered, on your key.',
  },
  {
    id: 'explore',
    label: 'Explore',
    short: 'Explore',
    path: '/explore',
    icon: 'compass',
    group: 'create',
    summary: 'Seventy sourced looks, as a gallery you can generate from.',
  },
  {
    id: 'cinema',
    label: "Director's Panel",
    short: 'Cinema',
    path: '/cinema',
    icon: 'clapper',
    group: 'create',
    summary: 'Body, glass, light and grade — compiled into one prompt, clause by clause.',
  },
  {
    id: 'canvas',
    label: 'Canvas',
    short: 'Canvas',
    path: '/canvas',
    icon: 'grid',
    group: 'create',
    summary: 'Chain generations into a graph; each node is one real request.',
  },
  {
    id: 'marketing',
    label: 'Marketing Studio',
    short: 'Ads',
    path: '/marketing',
    icon: 'sparkle',
    group: 'create',
    summary: 'A product page in, a campaign of real renders out.',
  },
  {
    id: 'image',
    label: 'Image Studio',    short: 'Image',
    path: '/image',
    icon: 'image',
    group: 'create',
    summary: 'Compose a prompt, choose a model, generate images.',
  },
  {
    id: 'video',
    label: 'Video Studio',
    short: 'Video',
    path: '/video',
    icon: 'video',
    group: 'create',
    summary: 'Text, image and video driven generation.',
  },
  {
    id: 'prompt',
    label: 'Prompt Lab',
    short: 'Layers',
    path: '/prompt',
    icon: 'layers',
    group: 'create',
    summary: 'See what a provider adds to your prompt before you spend anything.',
  },
  {
    id: 'assets',
    label: 'Library',
    short: 'Library',
    path: '/assets',
    icon: 'library',
    group: 'organise',
    summary: 'Every uploaded file and generated output.',
  },
  {
    id: 'presets',
    label: 'Presets',
    short: 'Presets',
    path: '/presets',
    icon: 'sliders',
    group: 'organise',
    summary: 'Reusable prompt templates and settings.',
  },
  {
    id: 'characters',
    label: 'Characters',
    short: 'Cast',
    path: '/characters',
    icon: 'person',
    group: 'organise',
    summary: 'Reusable character descriptions and references.',
  },
  {
    id: 'projects',
    label: 'Projects',
    short: 'Projects',
    path: '/projects',
    icon: 'folder',
    group: 'organise',
    summary: 'Group assets and jobs into projects.',
  },
  {
    id: 'history',
    label: 'History',
    short: 'History',
    path: '/history',
    icon: 'clock',
    group: 'organise',
    summary: 'Every request, its state, cost and outputs.',
  },
  {
    id: 'models',
    label: 'Models',
    short: 'Models',
    path: '/models',
    icon: 'chip',
    group: 'configure',
    summary: 'The model registry with full capability profiles.',
  },
  {
    id: 'connections',
    label: 'Connections',
    short: 'Keys',
    path: '/connections',
    icon: 'plug',
    group: 'configure',
    summary: 'Connect a provider with your own key and endpoint.',
  },
  {
    id: 'onboard',
    label: 'Add a provider',
    short: 'Add key',
    path: '/onboard',
    icon: 'key',
    group: 'configure',
    summary: 'One URL and one key: test every model the endpoint lists, then register what this build can drive.',
  },
  {
    id: 'settings',
    label: 'Settings',
    short: 'Settings',
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

/** The rail shows the creation surfaces and the four places work lands. */
export const RAIL: { create: string[]; organise: string[]; configure: string[] } = {
  create: ['home', 'chat', 'explore', 'cinema', 'canvas', 'marketing', 'image', 'video', 'prompt'],
  organise: ['assets', 'presets', 'characters', 'history'],
  configure: ['models', 'connections', 'onboard', 'settings'],
};

export function navEntryFor(path: string): NavEntry {
  const clean = path.split('?')[0].replace(/\/+$/, '') || '/';
  const exact = NAV.find((entry) => entry.path === clean);
  if (exact) return exact;
  const prefix = NAV.filter((entry) => entry.path !== '/' && clean.startsWith(entry.path)).sort(
    (a, b) => b.path.length - a.path.length,
  )[0];
  return prefix ?? NAV[0];
}
