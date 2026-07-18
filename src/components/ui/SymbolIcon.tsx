import type { SVGProps } from "react";

const icons = {
  dashboard: (
    <>
      <rect x="3.5" y="3.5" width="7" height="7" rx="1.5" />
      <rect x="13.5" y="3.5" width="7" height="7" rx="1.5" />
      <rect x="3.5" y="13.5" width="7" height="7" rx="1.5" />
      <rect x="13.5" y="13.5" width="7" height="7" rx="1.5" />
    </>
  ),
  search: <path d="m20 20-4.7-4.7m2-4.8a6.8 6.8 0 1 1-13.6 0 6.8 6.8 0 0 1 13.6 0Z" />,
  notifications: <path d="M18 9.5a6 6 0 1 0-12 0c0 7-2.5 7-2.5 8h17S18 16.5 18 9.5ZM9.5 20a2.8 2.8 0 0 0 5 0" />,
  help: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M9.7 9.5a2.5 2.5 0 1 1 3.6 2.2c-.9.5-1.3 1.1-1.3 2.1M12 17.3h.01" />
    </>
  ),
  settings: (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M19.4 13.5a7.8 7.8 0 0 0 .1-3l2-1.5-2-3.4-2.4 1a8.7 8.7 0 0 0-2.6-1.5L14.2 2h-4.4l-.4 3.1a8.7 8.7 0 0 0-2.5 1.5l-2.4-1-2 3.4 2 1.5a7.8 7.8 0 0 0 0 3l-2 1.5 2 3.4 2.4-1a8.7 8.7 0 0 0 2.5 1.5l.4 3.1h4.4l.4-3.1a8.7 8.7 0 0 0 2.5-1.5l2.4 1 2-3.4-2.1-1.5Z" />
    </>
  ),
  person: (
    <>
      <circle cx="12" cy="8" r="3.2" />
      <path d="M5 20a7 7 0 0 1 14 0" />
    </>
  ),
  note_add: (
    <>
      <path d="M6 3.5h8l4 4V20.5H6Z" />
      <path d="M14 3.5v4h4M12 11v6M9 14h6" />
    </>
  ),
  library_add: (
    <>
      <path d="M5 6.5h11.5A2.5 2.5 0 0 1 19 9v10H7.5A2.5 2.5 0 0 1 5 16.5Z" />
      <path d="M8 3.5h9M12 10v5M9.5 12.5h5" />
    </>
  ),
  policy: (
    <>
      <path d="M12 3.5 19 6v5.2c0 4.6-2.7 7.6-7 9.3-4.3-1.7-7-4.7-7-9.3V6Z" />
      <path d="m8.5 12 2.2 2.2 4.8-5" />
    </>
  ),
  arrow_drop_down: <path d="m7 9 5 6 5-6Z" fill="currentColor" stroke="none" />,
  edit: <path d="M4 17.5V20h2.5L18.2 8.3l-2.5-2.5ZM14.8 4.8l1.1-1.1a1.8 1.8 0 0 1 2.6 0l1.8 1.8a1.8 1.8 0 0 1 0 2.6l-1.1 1.1" />,
  play_arrow: <path d="M8 5.5v13l10-6.5Z" fill="currentColor" stroke="none" />,
  playlist_play: (
    <>
      <path d="M4 7h9M4 12h8M4 17h6" />
      <path d="M15 10v8l6-4Z" fill="currentColor" stroke="none" />
    </>
  ),
  stop: <rect x="7" y="7" width="10" height="10" rx="1.5" fill="currentColor" stroke="none" />,
  description: (
    <>
      <path d="M6 3.5h8l4 4V20.5H6Z" />
      <path d="M14 3.5v4h4M9 12h6M9 15.5h6" />
    </>
  ),
  hourglass: <path d="M7 3.5h10M7 20.5h10M8 3.5c0 5 8 5 8 8.5s-8 3.5-8 8.5M16 3.5c0 5-8 5-8 8.5s8 3.5 8 8.5" />,
  check_circle: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="m8.3 12.3 2.5 2.5 5-5.4" />
    </>
  ),
  error: (
    <>
      <circle cx="12" cy="12" r="8.5" />
      <path d="M12 7.5v5.8M12 16.7h.01" />
    </>
  ),
  upload_file: (
    <>
      <path d="M6 3.5h8l4 4V20.5H6Z" />
      <path d="M14 3.5v4h4M12 16V10M9.5 12.5 12 10l2.5 2.5" />
    </>
  ),
  chevron_left: <path d="m15 18-6-6 6-6" />,
  chevron_right: <path d="m9 6 6 6-6 6" />,
  fact_check: (
    <>
      <path d="M4.5 5.5h15v13h-15Z" />
      <path d="m7.5 10 1.4 1.4 2.6-3M13 9.5h4M7.5 15h4M13 15h4" />
    </>
  ),
  dock_to_right: (
    <>
      <rect x="4" y="5" width="16" height="14" rx="2" />
      <path d="M14 5v14M8 12h3" />
    </>
  ),
  check: <path d="m5 12.5 4.2 4.2L19 7" />,
  save: (
    <>
      <path d="M5 4h12l2 2v14H5Z" />
      <path d="M8 4v5h7V4M8 20v-6h8v6" />
    </>
  ),
  folder_open: <path d="M3.5 18.5 5.3 9h15.2l-1.8 9.5Zm1.3-9.5V6h5l2 2h7v1" />,
  science: (
    <>
      <path d="M9 3.5h6M10 3.5v5.2l-5 8.7a2 2 0 0 0 1.8 3.1h10.4a2 2 0 0 0 1.8-3.1l-5-8.7V3.5" />
      <path d="M8 16h8" />
    </>
  ),
  drive_folder_upload: (
    <>
      <path d="M3.5 18.5 5.3 9h15.2l-1.8 9.5Zm1.3-9.5V6h5l2 2h7v1" />
      <path d="M12 16v-5M9.7 13.2 12 11l2.3 2.2" />
    </>
  ),
  print: (
    <>
      <path d="M7 8V4h10v4M7 17H5a2 2 0 0 1-2-2v-4.5h18V15a2 2 0 0 1-2 2h-2" />
      <path d="M7 14h10v6H7Z" />
    </>
  ),
  draw: <path d="m4 20 4.8-1 10-10a2 2 0 0 0-2.8-2.8l-10 10Zm11.5-12.5 2.8 2.8" />,
  ink_eraser: (
    <>
      <path d="m4 15 7.5-7.5a2.2 2.2 0 0 1 3.1 0l3.9 3.9a2.2 2.2 0 0 1 0 3.1L13 20H8Z" />
      <path d="m9 10 5 5M3 20h18" />
    </>
  ),
  ads_click: (
    <>
      <path d="m6 4 12 7-5 1.5 3 5-2.5 1.5-3-5-3.5 4Z" />
      <path d="M17 4.5 19.5 2M20 8h3M13 3V0" />
    </>
  ),
  delete: (
    <>
      <path d="M5 7h14M9 7V4h6v3M7 7l1 13h8l1-13" />
      <path d="M10 11v5M14 11v5" />
    </>
  ),
  open_with: <path d="M8 3H3v5M16 3h5v5M3 16v5h5M21 16v5h-5M4 4l6 6M20 4l-6 6M4 20l6-6M20 20l-6-6" />,
  auto_fix: (
    <>
      <path d="m4 20 10.5-10.5M13 4l1 2.8L17 8l-3 1.2L13 12l-1-2.8L9 8l3-1.2ZM19 13l.7 1.8 1.8.7-1.8.7L19 18l-.7-1.8-1.8-.7 1.8-.7Z" />
    </>
  ),
  compare: (
    <>
      <rect x="4" y="5" width="7" height="14" rx="1.5" />
      <rect x="13" y="5" width="7" height="14" rx="1.5" />
      <path d="M8 9h-1M17 15h-1" />
    </>
  ),
  zoom_out: (
    <>
      <circle cx="10.5" cy="10.5" r="6.5" />
      <path d="M7.5 10.5h6M15.5 15.5 20 20" />
    </>
  ),
  zoom_in: (
    <>
      <circle cx="10.5" cy="10.5" r="6.5" />
      <path d="M7.5 10.5h6M10.5 7.5v6M15.5 15.5 20 20" />
    </>
  ),
  find_replace: (
    <>
      <circle cx="9.5" cy="9.5" r="5.5" />
      <path d="M13.5 13.5 19 19M18 7v4h-4M18 11a5 5 0 0 0-4.5-6" />
    </>
  ),
  key: (
    <>
      <circle cx="8" cy="14" r="3.5" />
      <path d="M11.5 14H21M17 14v-3M14 14v-2" />
    </>
  ),
  sync: (
    <>
      <path d="M20 12a8 8 0 0 1-13.6 5.7L4 15M4 12a8 8 0 0 1 13.6-5.7L20 9" />
      <path d="M4 15v4h4M20 9V5h-4" />
    </>
  ),
  pending_actions: (
    <>
      <rect x="5" y="4" width="12" height="16" rx="2" />
      <path d="M9 3h4M9 9h4M9 13h2" />
      <circle cx="17" cy="17" r="3.5" />
      <path d="M17 15.4V17l1.2 1" />
    </>
  ),
} as const;

export type SymbolIconName = keyof typeof icons;

type SymbolIconProps = Omit<SVGProps<SVGSVGElement>, "name"> & {
  readonly name: SymbolIconName;
};

export function SymbolIcon({ name, className, ...props }: SymbolIconProps) {
  return (
    <svg
      aria-hidden="true"
      className={["symbol-icon", "material-symbols-outlined", className].filter(Boolean).join(" ")}
      fill="none"
      focusable="false"
      stroke="currentColor"
      strokeLinecap="round"
      strokeLinejoin="round"
      strokeWidth="1.8"
      viewBox="0 0 24 24"
      {...props}
    >
      {icons[name]}
    </svg>
  );
}
