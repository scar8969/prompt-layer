import type { SVGProps } from 'react';

const PATHS: Record<string, string> = {
  home: 'M3 10.5 12 3l9 7.5M5.5 9.5V20h13V9.5',
  image: 'M3 5h18v14H3zM3 15l5-5 4 4 3-3 6 6',
  video: 'M3 6h12v12H3zM15 10l6-3v10l-6-3',
  folder: 'M3 6h6l2 2h10v11H3z',
  library: 'M4 4h6v16H4zM14 4h6v16h-6M12 4v16',
  sliders: 'M4 7h10M18 7h2M4 12h4M12 12h8M4 17h12M20 17h0M14 5v4M8 10v4M16 15v4',
  person: 'M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8ZM4 20c0-3.3 3.6-6 8-6s8 2.7 8 6',
  clock: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18ZM12 7v5l3.5 2',
  chip: 'M7 7h10v10H7zM10 3v4M14 3v4M10 17v4M14 17v4M3 10h4M3 14h4M17 10h4M17 14h4',
  plug: 'M9 3v6M15 3v6M6 9h12v3a6 6 0 0 1-6 6 6 6 0 0 1-6-6V9ZM12 18v3',
  gear: 'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6ZM19.4 15a1.7 1.7 0 0 0 .3 1.9l.1.1a2 2 0 1 1-2.8 2.8l-.1-.1a1.7 1.7 0 0 0-2.9 1.2 2 2 0 1 1-4 0 1.7 1.7 0 0 0-2.9-1.2l-.1.1a2 2 0 1 1-2.8-2.8l.1-.1A1.7 1.7 0 0 0 3 15a2 2 0 1 1 0-4 1.7 1.7 0 0 0 1.4-2.9l-.1-.1a2 2 0 1 1 2.8-2.8l.1.1A1.7 1.7 0 0 0 10 4.6a2 2 0 1 1 4 0 1.7 1.7 0 0 0 2.9 1.2l.1-.1a2 2 0 1 1 2.8 2.8l-.1.1A1.7 1.7 0 0 0 21 11a2 2 0 1 1 0 4Z',
  plus: 'M12 5v14M5 12h14',
  close: 'M6 6l12 12M18 6 6 18',
  check: 'M4 12.5 9 17.5 20 6.5',
  chevronDown: 'M6 9l6 6 6-6',
  chevronRight: 'M9 6l6 6-6 6',
  chevronLeft: 'M15 6l-6 6 6 6',
  search: 'M10.5 17a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13ZM15.5 15.5 20 20',
  refresh: 'M20 11a8 8 0 1 0-2.3 6.3M20 5v6h-6',
  download: 'M12 3v12M7.5 10.5 12 15l4.5-4.5M4 20h16',
  upload: 'M12 21V9M7.5 13.5 12 9l4.5 4.5M4 4h16',
  star: 'M12 3.6l2.6 5.4 5.9.8-4.3 4.1 1.1 5.9L12 17l-5.3 2.8 1.1-5.9-4.3-4.1 5.9-.8Z',
  starOutline: 'M12 3.6l2.6 5.4 5.9.8-4.3 4.1 1.1 5.9L12 17l-5.3 2.8 1.1-5.9-4.3-4.1 5.9-.8ZM12 3.6v13.4',
  copy: 'M9 9h10v12H9zM5 3h10v3M5 3v12h4',
  trash: 'M4 7h16M9 7V4h6v3M6 7l1 14h10l1-14M10 11v6M14 11v6',
  edit: 'M4 20h4l11-11-4-4L4 16v4ZM14 5l4 4',
  play: 'M7 4.5v15l13-7.5z',
  pause: 'M8 5h3v14H8zM13 5h3v14h-3z',
  alert: 'M12 3 2.5 20h19ZM12 9v5M12 17h.01',
  info: 'M12 21a9 9 0 1 0 0-18 9 9 0 0 0 0 18ZM12 11v5M12 8h.01',
  filter: 'M3 5h18l-7 8v6l-4-2v-4z',
  grid: 'M4 4h7v7H4zM13 4h7v7h-7zM4 13h7v7H4zM13 13h7v7h-7z',
  list: 'M4 6h16M4 12h16M4 18h16',
  expand: 'M4 9V4h5M20 15v5h-5M4 4l6 6M20 20l-6-6',
  zoomIn: 'M10.5 17a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13ZM15.5 15.5 20 20M10.5 8v5M8 10.5h5',
  zoomOut: 'M10.5 17a6.5 6.5 0 1 0 0-13 6.5 6.5 0 0 0 0 13ZM15.5 15.5 20 20M8 10.5h5',
  brush: 'M4 20c2 0 3-1 3-3 0-1.1.9-2 2-2M9 15 20 4M6 17l1-1',
  eraser: 'M8 20h12M4.5 15.5 12 8l4 4-7.5 7.5H6Z',
  undo: 'M9 7H5V3M5 7a8 8 0 1 1-1 8',
  redo: 'M15 7h4V3M19 7a8 8 0 1 0 1 8',
  wand: 'M5 19 19 5M14 4l1 3 3 1-3 1-1 3-1-3-3-1 3-1zM19 14l.7 2 2 .7-2 .7-.7 2-.7-2-2-.7 2-.7z',
  robot: 'M8 9h8v9H8zM12 5v4M9 13h.01M15 13h.01M5 12v4M19 12v4',
  layers: 'M12 3 3 8l9 5 9-5zM3 13l9 5 9-5M3 17.5l9 5 9-5',
  film: 'M3 4h18v16H3zM7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4',
  external: 'M14 4h6v6M20 4l-9 9M18 14v6H4V6h6',
  lock: 'M6 11h12v9H6zM9 11V8a3 3 0 0 1 6 0v3',
  key: 'M15 8a4 4 0 1 1-3.5 5.9L4 21l-1-1 1.5-1.5L3 17l2-2 1.5 1.5L10 13a4 4 0 0 1 5-5ZM16 7h.01',
  send: 'M4 12 20 4l-6 16-4-6z',
  save: 'M5 4h11l3 3v13H5zM8 4v6h7V4M8 20v-6h8v6',
  folderPlus: 'M3 6h6l2 2h10v11H3zM12 12v5M9.5 14.5h5',
  eye: 'M2.5 12S6 6 12 6s9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6ZM12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6Z',
  hdd: 'M3 12h18M5 12 7 5h10l2 7v7H5zM8 16h.01M12 16h3',
  shield: 'M12 3 5 6v6c0 4 3 7.5 7 9 4-1.5 7-5 7-9V6ZM9.5 12.5l2 2 3.5-4',
  users: 'M9 12a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7ZM2.5 20c0-3 3-5.5 6.5-5.5S15.5 17 15.5 20M16 5.5a3.5 3.5 0 0 1 0 7M18 20c0-2-.7-3.8-2-5',
  activity: 'M3 12h4l2.5-6 4 13L16 12h5',
  crop: 'M6 2v16h16M2 6h16v16',
  reset: 'M4 4v6h6M4.5 10a8 8 0 1 1 1 8',
};

export type IconName = keyof typeof PATHS | string;

export function Icon({
  name,
  size = 16,
  ...rest
}: { name: IconName; size?: number } & Omit<SVGProps<SVGSVGElement>, 'name'>) {
  const path = PATHS[name] ?? PATHS.info;
  return (
    <svg
      viewBox="0 0 24 24"
      width={size}
      height={size}
      fill="none"
      stroke="currentColor"
      strokeWidth={1.6}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
      {...rest}
    >
      <path d={path} />
    </svg>
  );
}
