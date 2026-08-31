# Analysis-manifest contract discrepancies

The Pydantic source models the public camelCase transport DTO: Rust serializes this
shape and TypeScript parses it. No runtime code consumes the generated output in R1.

| Domain | Python trusted emitter | Rust session DTO | TypeScript parser | R1 canonical DTO |
| --- | --- | --- | --- | --- |
| Manifest envelope | `schema_version`, snake_case, no `run_id`/`manifest_hash`/`manifest_version` | creates and serializes the full camelCase envelope | requires the same full camelCase envelope | Rust/TS public envelope; Python remains the trusted intermediate handoff |
| Threshold artifact | snake_case keys | accepts snake_case from Python, serializes camelCase | requires camelCase | camelCase public DTO |
| Coverage | rich objects (`schema_version`, counts, `kinds[]`) | same rich objects | normalizes them to flattened coverage maps | rich Rust/Python object; TS normalization remains runtime-only |
| Review target | emits `target_id: null` for unmatched routing review | `Option<String>` | rejects `null` and requires a valid target ID | non-null target ID, matching the public parser; unresolved Python output is not public-contract-valid |
| Revision lower bound | requires `analysis_revision >= 1` | creates revision 1 but structs permit `0` | accepts non-negative integers | non-negative public DTO; the Python trusted boundary remains stricter |
| Boundary resolution segment kind | `BoundaryCorrection` accepts `internal_review`, `official_dispatch`, `attachment`, or `legal` | accepts the same four generated DTO values | accepts the same four generated DTO values | Canonical correction set is exactly `{internal_review, official_dispatch, attachment, legal}`. `mixed` is a document profile; `common` is expressed through `common_only`; `unknown` is display-only. |
| Finalization result | returns snake_case engine result (`status: applied`) | returns camelCase IPC result (`status: promoted`) | parses the Rust IPC result | Rust/TS IPC result |

## T29a: boundary-correction kind alignment

The correction boundary is deliberately narrower than document profiles and analysis
segments. `contracts/models.py` owns the four correction kinds and code generation
propagates them to Rust and TypeScript. Every correction entry point must reject
`mixed`, `common`, `unknown`, and arbitrary strings; `mixed` remains a document
profile, `common` remains `common_only`, and `unknown` is read-only UI state.

`legal` is retained: a public-profile document can reanalyse a user-corrected legal
segment even though the standalone legal profile does not emit a trusted manifest.
Boundary reanalysis routes using the prior analysis revision and only then mints
the successor revision, so its injected profile authority is bound to the prior
revision. The golden DTO round-trip, per-layer allowlist tests, and partial-range
public-profile reanalysis tests are the regression invariant for this boundary.

## Follow-up: unmatched routing-review target

`document_masker_ocr_gui.py` currently uses `next(..., None)` when converting a
routing review into a trusted manifest. An unmatched route can therefore emit
`target_id: null`, which is invalid for the public Rust/TypeScript DTO. Golden
fixture generation rejects this shape; production emission remains unchanged in
this task and needs a separate routing-failure policy decision.
