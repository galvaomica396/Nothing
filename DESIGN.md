# Design System: Pencil A-G Canonical Replacement

## Source Of Truth

The canonical frontend references are the seven Pencil HTML exports in
`[PENCIL_EXPORT_ROOT]/`.

1. `시안A-문서데스크.html`
2. `시안B-검토화면.html`
3. `시안C-설정.html`
4. `시안D-키워드마스킹.html`
5. `시안E-저장함.html`
6. `시안F-자동마스킹진행중.html`
7. `시안G-저장완료.html`

They are canonical for layout, spacing, hierarchy, labels, density, and modal
presentation. Production must preserve working controls even when a reference
omits them.

## Product Identity

Nothing is a Korean-first desktop masking tool for operators who open a PDF,
run automatic masking, review the result, and save a safe document. The app
must read as a paper-gray document desk, not a developer workbench.

## Visual Direction

- Default theme is light-first.
- Base background uses paper gray.
- Primary surfaces are white with soft gray borders.
- Accent color is the existing trust blue.
- The left rail is always present on desktop.
- Controls stay compact and dense.
- PDF pages remain white and theme-independent.

Dark mode remains supported, but the Pencil light layout is the primary visual
authority.

## Screen Contracts

### A. Document Desk

- Left rail: `문서 데스크`, `마스킹 작업`, `검토 대기`, `저장함`, `설정`
- Header search field
- Top title, subtitle, and a primary `PDF 열기` action
- Three compact stats cards
- Large dashed drop zone with `파일 선택` and a secondary sample action
- Recent-document table

### B. Review Workspace

- Header with back affordance, document title, and save-related actions
- Separate toolstrip below the header
- Centered PDF page on a light desk
- Right inspector with detection counts, action buttons, item list, and bottom actions

### C. Settings

- Left in-screen sub-navigation
- Dense row-style settings groups
- White cards on paper-gray background
- Compact segmented choices and toggles

### D. Keyword Masking

- Modal sheet over the review workspace
- Single-line keyword entry with explicit add action
- Chip list of current keywords
- Existing textarea-backed storage remains allowed internally, but the visible
  interaction should read as entry-plus-chips

### E. Storage

- Search field in the shell header
- Storage title and summary chips
- Full-width saved-document table

### F. Masking Progress

- Dedicated progress dialog
- Title, explanatory copy, progress bar, current page progress, and compact stats

### G. Save Success

- Dedicated success modal
- Success mark, saved file summary, and follow-up actions

## Runtime Constraints

- Preserve all existing runtime IDs, `data-*` hooks, and DOM relationships used
  by the frontend controllers.
- Do not remove existing working controls because a mockup omitted them.
- Existing omitted functionality must remain available and visually integrated.
- No new dependencies.

## Layout Constraints

- Desktop: left rail + top header + main content + 28px status bar
- Tablet and mobile: no horizontal overflow at `768px` and `375px`
- Inspector stacks below the stage when space is narrow
- Modals remain fully visible at narrow widths

## Styling Files

- `src/styles/variables.css`
- `src/styles/themes.css`
- `src/styles/shell.css`
- `src/styles/components.css`
- `src/styles/screen-desk.css`
- `src/styles/screen-settings.css`
- `src/styles/screen-canvas.css`

These files implement the canonical Pencil replacement. Older mockups and older
design writeups are historical only unless they describe runtime behavior that
the Pencil refs do not show.
