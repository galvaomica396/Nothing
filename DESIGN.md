# Design System: Nothing Notion Desk (v4.6 — "One Document Flow, One Accent")

## 1. Source Of Truth

This file is the **single current authority** for the frontend look, information
architecture, and interaction contracts, with one explicit visual exception:
the browser-rendered mockups in
`docs/design-mockups/2026-07-17-notion/01-workspace-notion.html`,
`02-masking-settings-notion.html`, and `03-save-flow-notion.html` are canonical
for the anatomy, measurements, spacing, typography, and surface treatment of
those three views. `docs/refactor/notion-theme-design-2026-07-17.md` translates
that visual authority into an executable implementation and screenshot contract.
Other older documents under `docs/` remain history rather than implementation
requirements. Product behavior, accessibility, and runtime bindings still come
from this file and `docs/RUNTIME_CONTRACT.md`; a mockup may omit a working control
for presentation, but production must retain and restyle it. v4.6 keeps the "One
Accent" workflow language, adds a Notion-style light/dark system, and continues
to exclude the coordinate-template product flow entirely.

Nothing now has a dual Notion-style workspace: warm paper and white surfaces in
light mode, plus a refined near-black desk in dark mode. The PDF page remains the
visual protagonist in both modes. A single blue action accent carries primary
commands; violet and the retired accent variants remain prohibited.

## 2. Product Identity

DocuMasker is a Windows/macOS desktop tool for public-sector, administrative, and
legal operators. It opens a PDF 공문, detects and masks personal information,
lets a human review and correct on the document, and saves a safe file. It must
read as a "문서 도구 you trust and install", not a developer demo. The core
flow it must always make obvious is:

- 일반 문서 흐름: `PDF 추가 -> 기본 마스킹 -> 검토/보정 -> 저장`

## 3. Visual Theme

The interface is a quiet document-security desk. Light mode uses warm paper
`#f6f5f4`, pure-white surfaces, and whisper `#e6e6e6` hairlines. Dark mode uses
near-black surfaces with the same restrained component anatomy. Labels are
Korean-first, controls remain compact, and the document stage sits beside a slim
review/save inspector that only becomes meaningful after masking runs. The blue
accent marks primary commands; semantic success and danger colors remain
decorative status signals rather than structural chrome.

## 4. Design Tokens

All styling flows through a single `--dm-*` token root in
`src/styles/variables.css`. The legacy `--nothing-*` / `--stitch-*` / `--obs-*`
token systems are removed. `variables.css` provides a dark-safe fallback and
`themes.css` defines explicit light and dark resolved themes. Key tokens:

- Surfaces (near-black, slight blue tint): `--dm-bg: #0e1116`,
  `--dm-surface: #161b22`, `--dm-surface-raised: #1c2330`,
  `--dm-surface-sunken: #0b0e13`, `--dm-border: #262d38`,
  `--dm-border-strong: #3a4351`
- Text: `--dm-text: #e8edf4`, `--dm-text-secondary: #9aa6b5`,
  `--dm-text-muted: #5c6875` (waiting/empty states only)
- Accent (single trust blue, 1–2 spots per screen): `--dm-accent: #2f81f7`,
  `--dm-accent-hover: #4c95f9`, `--dm-accent-soft: #16233c`
- Semantic (desaturated for dark): `--dm-success`, `--dm-warning`, `--dm-danger`
  (+ soft variants), and `--dm-mask: #000000` for the black masking box color.
- Light surfaces: `--dm-bg: #f6f5f4`, `--dm-surface: #ffffff`,
  `--dm-border: #e6e6e6`, `--dm-text: #37352f`, `--dm-accent: #0075de`.
- Spacing scale: `4 / 8 / 12 / 16 / 24 / 32px`; off-scale spacing requires a
  documented content or platform constraint.
- Type scale: eyebrow `12px/600`, caption `14px`, control/body-small
  `15px/500–600`, body `16px/1.5`, card heading `20px/600`, modal heading
  `22px/700`, and screen/hero heading `26px/700`.
- Geometry: utility controls use `--dm-radius-sm: 8px` / `--dm-radius: 8px`,
  cards and panels use `--dm-radius-lg: 12px`, the final-save modal alone uses
  `16px`, and primary CTAs use `--dm-radius-pill: 9999px`. Boundaries are 1px
  hairlines; raised surfaces use the mockups' restrained layered shadows.
- Layout: `--dm-header-h: 48px`, `--dm-inspector-w: 320px`,
  `--dm-statusbar-h: 28px`. **There is no rail width token** — the left rail is
  abolished; screen switching lives in the top bar.

There is no bundled web font. Typography uses the system stack
(`Pretendard`, `Segoe UI`, `Malgun Gothic`, `Apple SD Gothic Neo`) so the app
looks native and never fails on offline/strict-CSP packaging. Type stays compact
and Korean-first; screen titles may use a larger size (20→24) but oversized
dashboard headings are avoided outside the document viewer.

## 5. CSS Architecture

Styling is split into focused files under `src/styles/`, imported in this order
from `src/main.tsx`:

| File | Role |
|---|---|
| `variables.css` | `--dm-*` token root (dark default) |
| `base.css` | reset, html/body, typography, scrollbar, focus ring, `.is-hidden` / visually-hidden toggles |
| `components.css` | buttons, inputs, selects, cards, badges, tables, tooltips, empty states |
| `shell.css` | app shell: document home + actions + gears, status bar, screen switching (no rail, no mobile dock) |
| `screen-canvas.css` | unified 문서 화면 = 마스킹 캔버스 + 검토·저장 레일 (+ standalone window mode) |
| `screen-settings.css` | 마스킹 설정 + 앱 설정 |
| `themes.css` | resolved `data-theme` overrides — `dark` / `light` (loaded last) |

There is no `screen-documents.css`: v4 merged 문서 관제 into the canvas screen, so
the review/save rules now live in `screen-canvas.css`. New CSS uses the `dm-`
class prefix and **must not use `!important`** (focus / accessibility exceptions
only). z-index is limited to 5, 10 (panels), 100 (modals), 200 (toasts). Tailwind
utilities are not used in JSX; there is no `@import "tailwindcss"` in the
stylesheets — `base.css` covers the reset the app relies on.

## 6. Layout

Desktop uses a shell grid: header (48px, full width) over a single-column `main`
row (`grid-template-columns: minmax(0, 1fr)`; the 224px left rail is gone), over a
status bar (28px, full width). The full vertical space goes to the document.

The unified 문서 화면 (one screen for document control + manual correction):

1. Top slim bar: edit tools (마스킹/복원/선택/삭제/이동), keyword, original-compare
   toggle, page/zoom, batch (collapsed), and 반영 → 저장.
2. Center preview stage: the empty-state hero (single "PDF 열기" CTA) becomes a
   dual PDF compare view once a document loads — the white page on the dark desk.
3. Right slim inspector (`#side-panel`): persistent 320px chrome with detection
   review list, current-page box controls (no coordinate numbers), save summary,
   and the final-save button. Before masking it remains visible with a restrained
   empty-state card; its content becomes actionable after masking runs. (v4.1: the safe-report summary was removed —
   the safe report is an internal verification device only, never surfaced in the
   UI and never copied to the user's output folder. The internal report is still
   generated and feeds the advisory layer.) (v4.2.0: verification always runs and
   is recorded, but its result is advisory — it never hard-blocks the save. Final
   save is always the user's decision: the button stays enabled whenever a masked
   document exists, and a one-time "저장 전 확인" dialog ("그대로 저장"/"취소")
   appears only when there are advisory items. Masking itself is never reduced.)

Final save creates an immutable exported snapshot by default; it does not close
the working document session. After the backend returns the saved path, the frontend must load-verify
that exact file before adopting it as the sole continuation baseline. Later
mask/restore operations read that snapshot but write a new private preview. A
later save normally branches to another file, but the snapshot's immutability has
one explicit user-controlled exception: when the user selects the same exact path
and accepts the OS-native overwrite warning, that confirmed intent authorizes
replacement. The replacement becomes the sole continuation baseline only after
its own load verification succeeds. The picker-selected original remains
available only as the restore reference. Inline and standalone canvas surfaces
must use the same continuation baseline and must never silently fall back to an
older generated preview or the original after a final snapshot exists. If the
saved snapshot cannot be loaded, editing and re-saving stay disabled and the UI
must clearly distinguish "file written, integrity verification failed" from a
verified save and direct the user to reopen the saved PDF.

Loaded-document flow is expressed by button enable/disable and placement order,
not by persistent numbered teaching chrome (UX_SIMPLICITY_V3_4). The canonical
workspace mockup creates one narrow exception: the empty-document hero shows the
three compact 32px steps `PDF 열기 → 자동 마스킹 → 수동 보정 및 저장`, joined by
40px hairlines. The guide disappears when a document is loaded and must never
replace functional controls. The 48px header, toolbar, persistent inspector, and
28px status bar remain present in the empty state. At narrow widths the stage and
inspector stack vertically (block flow) — there is no mobile dock.

Canvas and manual correction share this one screen; the standalone canvas window
(`body.standalone-canvas-window`, `?mode=canvas`) layout must keep working.

## 7. Themes

The persisted preference is `light` / `dark` / `system`. The DOM always receives
a resolved `data-theme="light|dark"`; `system` follows
`prefers-color-scheme` without restarting. No settings record means a new user
starts with `system`. A legacy settings record with no theme, or the old
`default` value, migrates to explicit `dark` so existing users do not experience
an unexpected bright launch. `themes.css` overrides token values only.

## 8. Components

- Buttons: `dm-btn` (8px bordered utility), `dm-btn--primary` (blue pill),
  `dm-btn--danger`, `dm-btn--ghost` (toolbars). Heights 30px (default) / 36px
  (primary action). Icons reuse the `SymbolIcon` SVG component — the icon
  system is a single scale (16/20px) and must not be changed; no emoji/unicode
  glyphs (⚠🔒✓›) as UI icons.
- Cards: `dm-card` with a 1px border + `--dm-shadow-sm`; no nested content cards
  (separate sections with hairlines). Interactive settings choice tiles are form
  controls, not nested content cards, and may use the same 12px boundary inside a
  settings section as shown by the canonical masking-settings mockup.
- Tables: compact rows, header on a sunken surface, hover row highlight.
- Modals: dark overlay, `hidden` / `is-hidden` toggle preserved.
- Empty/waiting states use `--dm-text-muted` with a dashed empty-state block or
  a "대기" badge — never styled to look like live data.

박스 크기, 버튼 높이, 경계선 두께가 흔들리면 안 된다. Fixed-format controls need
stable dimensions across desktop and narrow widths. The single accent marks the
active command / primary action only.
The accent must appear in at most one or two places on a screen.

## 9. Functional Binding

UI behavior is owned by `src/legacy/startLegacyApp.ts` (+ `src/features/**`) and
validated by `docs/RUNTIME_CONTRACT.md` (see
`node scripts/check_runtime_contract.mjs`). Design changes must preserve every
id / data-attribute / class hook listed there. Visual refactors must also preserve
DOM relationships consumed by behavior: sibling traversal in
`MaskingSettingsScreen`, `.closest(".config-cell")` and `.closest(".dm-field")`,
the run-label child span, the save-action next sibling, and scroll ancestors such
as `.dm-canvas__scroll`. Existing controls omitted from a visual mockup remain in
production and are placed using the mockup's card, hairline, pill, spacing, and
type language. Required hooks include:

- `data-screen-target`, `data-screen-panel`, `.is-active`
- `data-canvas-tool`, `data-settings-panel`
- `btn-pick-pdf`, `btn-run-masking`, `btn-save`, `btn-open-final-save-dialog`
- canvas tool ids and settings screen targets
- resolved `data-theme`, `data-theme-preference`, and the `settings-theme` radios
  (`light` / `dark` / `system`)

Do not remove or rename these hooks for visual changes.

## 9.1 Visual Acceptance

The three canonical HTML mockups are rendered in a browser at `1280×860` and
compared beside production screenshots, not interpreted from source alone.
Acceptance also covers `768px` and `375px` layouts and both resolved themes.
Required production states are: empty and loaded workspace, masking settings at
top and scrolled positions, general settings, save advisory, keyword modal, and
batch pending/failure. Computed checks must prove header `48px`, desktop inspector
`320px`, status bar `28px`, settings content `max-width: 800px`, final-save modal
`480px`, card/panel radius `12px`, final-save modal radius `16px`, primary pill
radius `9999px`, 1px hairlines, no horizontal overflow, and no Korean-text
clipping. The PDF canvas pixels remain theme-independent. A screenshot may have
extra working controls absent from a mockup, but must preserve the reference's
geometry and visual hierarchy around them.

React 19 + TypeScript components and the `--dm-*` plain-CSS token system are the
standard frontend path. Tailwind utilities are not used in JSX and the Vite
Tailwind plugin is intentionally absent.

## 10. Banned Patterns

- Do not reintroduce the removed review queue screen.
- Do not reintroduce the left navigation rail, coordinate-template screen, or
  mobile action dock — screen switching stays in the document home and gears.
- Do not reintroduce the Obsidian violet or accent-variant identities.
- Do not restore the removed `white` / `blue` / `purple` / `brown` / `black`
  theme presets.
- Do not use `!important` in new CSS (focus / accessibility excepted).
- Do not add marketing copy, hero treatments (beyond the single empty-state CTA),
  decorative gradients, or stock imagery.
- Do not create nested card layouts.
- Do not spend the accent on more than one or two elements per screen; express
  status with a full 1px border color + a soft background tint (and a leading
  SymbolIcon for alerts), never a thick colored side-stripe — that is the most
  recognizable AI-generated tell. Group separators inside toolbars may use a 1px
  vertical divider.
- Do not animate layout properties (`width`/`height`/`padding`/`margin`). The
  only sanctioned exception is a JS-driven progress-bar fill inside a fixed-size
  track (readiness / workflow meters); mark those `impeccable-disable-line
  layout-transition` with a reason.
- Do not surface internal numbers (box px coordinates/size, page coordinates,
  progress %, internal state codes, full path strings, "n번 박스" identifiers) —
  the box drawn on the canvas is the state.
- Do not render waiting/empty text as if it were live document data.
- Do not expose internal / English state terms in the UI (e.g. READY, WAIT,
  fixture, preflight). Use Korean user language for visible values.
