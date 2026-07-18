# Security And Privacy

## Local Processing

The app processes documents locally through Tauri, Rust commands, and Python scripts. The default flow does not call external APIs.

## Safe Defaults

- The user's output folder receives the masked document (PDF) only. The safe report is an internal verification artifact and is never copied into the user's output folder; the "safe report" concept is not surfaced in the UI (v4.1).
- Raw TXT extraction is off by default.
- The internal verification report file is named `safe_report` (filename pattern `*.safe_report.*.json`) and is written only under a per-session directory inside the OS temp folder (`makiiing_v2_internal_reports/report_*/`). It stores counts, statuses, display tokens, and quality checks. It is still generated on every run and is what the verification/advisory layer reads; result JSON `report_path` fields remain populated with this internal path. Rust `finalize`'s `copy_report` defaults to `false`, and the frontend always passes `copyReport: false`.
- Raw detected values are not written to the report.

## Verification (Advisory, v4.2.0)

Verification always runs and is always recorded, but its result is advisory —
it is surfaced as a recommendation and never hard-blocks the save. **Final save
is always the user's decision.** As long as a masked document exists, the save
button stays enabled. The checks the verification layer evaluates and reports are:

- `verification.verified == true`
- `targets_hit == targets_requested`
- `missing_targets_count == 0`
- `residual_hits == 0`
- restore-revalidation status (a restored region may re-expose masking)

When any check does not pass, the UI shows a one-time "저장 전 확인" (save-confirm)
dialog listing the advisory items (counts only — never raw values or coordinates)
with two choices: "그대로 저장" (save as-is) or "취소" (cancel). Continuing opens
the OS-native save dialog with `<original-stem>_masked` as the editable default name.
The selected exact path is normalized to PDF and returned with an opaque one-shot
token. `AllowedFileAccess` binds the token to that path, replaces or clears stale
selections on the next chooser invocation, and consumes both before
`finalize_manual_output_to_selected_path` writes. If PDF normalization changes the
dialog-confirmed path and the normalized target already exists, saving is rejected
so the OS dialog can confirm the actual overwrite target explicitly.
The compatibility command `finalize_manual_output` and the selected-path command
never fail on report content (residual / missing / quality / failed revalidation /
missing-or-unparseable report); the former `report_allows_final_save` hard-block
predicate is retired. Masking itself is never reduced by this policy.

## Logs

Logs may include status, counts, paths, and failure reasons. They must not include raw detected values or extracted document text.

## Known Limits

Scanned PDFs, unusual encodings, broken text layers, and OCR failures can prevent automatic rectangle detection. These documents require manual review.
