from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import document_masker_ocr_gui as masker
import masking_extraction


REPO_ROOT = Path(__file__).resolve().parents[1]


def legacy_typescript_source() -> str:
    legacy_root = REPO_ROOT / "src" / "legacy"
    return "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted(legacy_root.rglob("*.ts"))
    )


class CoordinateFeatureRetirementTests(unittest.TestCase):
    def test_coordinate_product_surface_is_removed(self) -> None:
        retired_paths = [
            "coordinate_batch_runner.py",
            "src/components/CoordinateTemplateEditor.tsx",
            "src/services/tauri/coordinateTemplates.ts",
            "src/styles/screen-coordinate.css",
            "scripts/qa_coordinate_save.mjs",
            "scripts/qa_coordinate_run.mjs",
            "docs/COORDINATE_TEMPLATE_BATCH.md",
        ]
        for relative_path in retired_paths:
            self.assertFalse((REPO_ROOT / relative_path).exists(), relative_path)
        for relative_path in ["src/features/coordinate-template", "src/features/coordinate-batch"]:
            self.assertEqual([], list((REPO_ROOT / relative_path).glob("*.ts")), relative_path)
        self.assertEqual([], list((REPO_ROOT / "coordinate_batch").glob("*.py")))

        frontend = "\n".join(
            [
                (REPO_ROOT / "src" / relative_path).read_text(encoding="utf-8")
                for relative_path in [
                    "App.tsx",
                    "components/AppHeader.tsx",
                    "workflowFlow.ts",
                    "main.tsx",
                ]
            ]
            + [legacy_typescript_source()]
        )
        self.assertNotIn("coordinate-template", frontend)
        self.assertNotIn("CoordinateTemplate", frontend)

    def test_coordinate_ipc_names_remain_as_retired_compatibility_commands(self) -> None:
        rust = (REPO_ROOT / "src-tauri" / "src" / "lib.rs").read_text(encoding="utf-8")
        coordinate_templates = (REPO_ROOT / "src-tauri" / "src" / "coordinate_templates.rs").read_text(encoding="utf-8")
        coordinate_batch = (REPO_ROOT / "src-tauri" / "src" / "coordinate_batch.rs").read_text(encoding="utf-8")
        combined = rust + coordinate_templates + coordinate_batch
        for command in [
            "list_coordinate_templates",
            "load_coordinate_template",
            "save_coordinate_template",
            "delete_coordinate_template",
            "enumerate_coordinate_batch_targets",
            "preflight_coordinate_batch",
            "start_coordinate_batch",
            "cancel_coordinate_batch",
            "retry_coordinate_batch",
        ]:
            self.assertIn(command, combined)
        self.assertIn("FeatureRetired", combined)
        self.assertNotIn("mod execution;", coordinate_batch)
        self.assertNotIn("mod template_contract;", coordinate_templates)

    def test_retired_coordinate_ipc_signatures_remain_compatible(self) -> None:
        sources = " ".join(
            (REPO_ROOT / "src-tauri" / "src" / filename).read_text(encoding="utf-8")
            for filename in ["coordinate_templates.rs", "coordinate_batch.rs"]
        )
        normalized = " ".join(sources.split())
        expected_signatures = [
            "fn list_coordinate_templates( app: tauri::AppHandle, ) -> Result<Vec<CoordinateTemplateSummary>, CoordinateTemplateError>",
            "fn load_coordinate_template( app: tauri::AppHandle, id: String, ) -> Result<CoordinateTemplate, CoordinateTemplateError>",
            "fn save_coordinate_template( app: tauri::AppHandle, template: serde_json::Value, ) -> Result<CoordinateTemplateSaveResult, CoordinateTemplateError>",
            "fn delete_coordinate_template( app: tauri::AppHandle, id: String, ) -> Result<(), CoordinateTemplateError>",
            "fn enumerate_coordinate_batch_targets( access: tauri::State<'_, crate::AllowedFileAccess>, target_folder: String, ) -> Result<preflight::CoordinateBatchTargetListing, CoordinateBatchError>",
            "fn preflight_coordinate_batch( access: tauri::State<'_, crate::AllowedFileAccess>, template: serde_json::Value, targets: Vec<preflight::CoordinateBatchPreflightTarget>, output_dir: String, compatible_only: Option<bool>, ) -> Result<preflight::CoordinateBatchPreflightReport, CoordinateBatchError>",
            "fn start_coordinate_batch( app: tauri::AppHandle, access: tauri::State<'_, crate::AllowedFileAccess>, registry: tauri::State<'_, CoordinateBatchRegistry>, request: execution::CoordinateBatchStartRequest, ) -> Result<execution::CoordinateBatchRunResult, CoordinateBatchError>",
            "fn cancel_coordinate_batch( registry: tauri::State<'_, CoordinateBatchRegistry>, request: execution::CoordinateBatchCancelRequest, ) -> Result<execution::CoordinateBatchCancelResult, CoordinateBatchError>",
            "fn retry_coordinate_batch( app: tauri::AppHandle, access: tauri::State<'_, crate::AllowedFileAccess>, registry: tauri::State<'_, CoordinateBatchRegistry>, request: execution::CoordinateBatchStartRequest, ) -> Result<execution::CoordinateBatchRunResult, CoordinateBatchError>",
        ]
        for signature in expected_signatures:
            self.assertIn(signature, normalized, signature)

    def test_retired_coordinate_dto_schemas_remain_compatible(self) -> None:
        sources = " ".join(
            (REPO_ROOT / "src-tauri" / "src" / filename).read_text(encoding="utf-8")
            for filename in ["coordinate_templates.rs", "coordinate_batch.rs"]
        )
        normalized = " ".join(sources.split())
        expected_schemas = [
            "#[serde(rename_all = \"camelCase\", deny_unknown_fields)] pub(crate) struct CoordinateTemplate { schema_version: u32, id: String, name: String, page_geometry: PageGeometry, rects: Vec<NormalizedRect>, }",
            "#[serde(rename_all = \"camelCase\", deny_unknown_fields)] pub(crate) struct PageGeometry { page_count: u32, pages: Vec<PageGeometryEntry>, }",
            "#[serde(rename_all = \"camelCase\", deny_unknown_fields)] pub(crate) struct PageGeometryEntry { page_index: u32, width: f64, height: f64, rotation: u16, crop_box: [f64; 4], }",
            "#[serde(rename_all = \"camelCase\", deny_unknown_fields)] pub(crate) struct NormalizedRect { page_index: u32, x0: f64, y0: f64, x1: f64, y1: f64, tag: Option<String>, }",
            "#[serde(rename_all = \"camelCase\")] pub(crate) struct CoordinateTemplateSummary { id: String, name: String, page_count: u32, rect_count: usize, }",
            "#[serde(rename_all = \"camelCase\")] pub(crate) struct CoordinateTemplateSaveResult { id: String, storage_key: String, saved: bool, }",
            "#[serde(rename_all = \"camelCase\")] pub(crate) struct CoordinateBatchTargetListing { target_count: usize, targets: Vec<CoordinateBatchTarget>, }",
            "#[serde(rename_all = \"camelCase\")] struct CoordinateBatchTarget { id: String, name: String, size_bytes: u64, }",
            "#[serde(rename_all = \"snake_case\")] enum CoordinatePreflightStatus { Compatible, IncompatiblePageCount, IncompatiblePageSize, IncompatibleRotation, Encrypted, InvalidPdf, BoxOutOfBounds, OutputConflict, }",
            "#[serde(rename_all = \"camelCase\")] pub(crate) struct CoordinateBatchPreflightTarget { id: String, name: String, page_count: Option<u32>, width: Option<f64>, height: Option<f64>, rotation: Option<u16>, encrypted: Option<bool>, invalid_pdf: Option<bool>, box_out_of_bounds: Option<bool>, }",
            "#[serde(rename_all = \"camelCase\")] struct CoordinateBatchPreflightItem { id: String, basename: String, status: CoordinatePreflightStatus, output_basename: String, }",
            "#[serde(rename_all = \"camelCase\")] pub(crate) struct CoordinateBatchPreflightReport { status: CoordinatePreflightStatus, compatible_count: usize, blocked_count: usize, compatible_only: bool, targets: Vec<CoordinateBatchPreflightItem>, }",
            "#[serde(rename_all = \"camelCase\")] pub(crate) struct CoordinateBatchStartRequest { output_dir: String, template: serde_json::Value, display_mode: Option<String>, targets: Vec<CoordinateBatchExecutionTarget>, target_ids: Option<Vec<String>>, }",
            "#[serde(rename_all = \"camelCase\")] struct CoordinateBatchExecutionTarget { id: String, name: String, }",
            "#[serde(rename_all = \"camelCase\")] pub(crate) struct CoordinateBatchCancelRequest { #[serde(alias = \"runId\")] session_id: String, }",
            "#[serde(rename_all = \"camelCase\")] pub(crate) struct CoordinateBatchCancelResult { session_id: String, cancelled: bool, }",
            "#[serde(rename_all = \"camelCase\")] struct CoordinateBatchFileResult { id: String, input_basename: String, output_basename: String, status: String, error_code: Option<String>, }",
            "#[serde(rename_all = \"camelCase\")] pub(crate) struct CoordinateBatchRunResult { #[serde(rename = \"sessionId\", alias = \"runId\")] session_id: String, status: String, total: usize, completed: usize, failed: usize, cancelled: usize, result_basename: String, event_basename: String, files: Vec<CoordinateBatchFileResult>, }",
        ]
        for schema in expected_schemas:
            with self.subTest(schema=schema.split("{")[0].strip()):
                self.assertIn(schema, normalized)

    def test_packaged_engine_has_no_coordinate_batch_entrypoint(self) -> None:
        entry = (REPO_ROOT / "scripts" / "masking_engine_entry.py").read_text(encoding="utf-8")
        spec = (REPO_ROOT / "packaging" / "pyinstaller" / "masking_engine.spec").read_text(encoding="utf-8")
        self.assertNotIn("--coordinate-batch", entry)
        self.assertNotIn("coordinate_batch_runner", entry + spec)
        self.assertNotIn('"coordinate_batch"', spec)


class MaskedTextOptionTests(unittest.TestCase):
    def test_legacy_raw_text_modes_fail_closed_to_safe_pdf_report(self) -> None:
        raw_aliases = ["txt만", "txt+pdf", "txt+report", "txt+pdf+report", "TXT만 저장", "TXT + PDF"]
        for alias in raw_aliases:
            with self.subTest(alias=alias):
                self.assertEqual({"pdf", "report"}, masker.resolve_output_artifacts({"output_artifacts": alias}))
        self.assertFalse(any(label in masker.OUTPUT_ARTIFACT_LABELS for label in [
            "TXT만 저장", "TXT + PDF", "TXT + 리포트", "고급: TXT + PDF + 리포트",
        ]))

    def test_legacy_raw_text_mode_never_creates_extracted_or_masked_txt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "input.txt"
            source.write_text("연락처 010-1234-5678", encoding="utf-8")
            extracted_path, masked_path, report_path, _report = masker.process_file(
                str(source),
                outdir=str(root),
                opts={
                    "output_artifacts": "txt만",
                    "deidentification_policy": "token",
                    "pdf_redaction": False,
                },
            )

            self.assertIsNone(extracted_path)
            self.assertIsNone(masked_path)
            self.assertIsNotNone(report_path)
            self.assertEqual([], list(root.glob("*.extracted.*.txt")))
            self.assertEqual([], list(root.glob("*.masked.*.txt")))

    def test_runner_boundaries_reject_raw_text_preview_without_echoing_input(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "private_document_name.txt"
            source.write_text("연락처 010-1234-5678", encoding="utf-8")
            opts = json.dumps(
                {
                    "output_artifacts": "pdf_safe_report",
                    "deidentification_policy": "token",
                    "pdf_redaction": False,
                    "return_text_preview": True,
                },
                ensure_ascii=False,
            )
            commands = [
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts" / "run_masking_pipeline.py"),
                    "--repo-root",
                    str(REPO_ROOT),
                    "--input",
                    str(source),
                    "--outdir",
                    str(root),
                    "--opts",
                    opts,
                ],
                [
                    sys.executable,
                    str(REPO_ROOT / "scripts" / "masking_engine_entry.py"),
                    "--repo-root",
                    str(REPO_ROOT),
                    "--input",
                    str(source),
                    "--outdir",
                    str(root),
                    "--opts",
                    opts,
                ],
            ]
            for command in commands:
                with self.subTest(script=Path(command[1]).name):
                    completed = subprocess.run(command, capture_output=True, text=True, check=False)
                    self.assertNotEqual(0, completed.returncode)
                    self.assertEqual("", completed.stdout)
                    self.assertIn("FAILED", completed.stderr)
                    self.assertNotIn(source.name, completed.stderr)
                    self.assertNotIn("010-1234-5678", completed.stderr)

    def test_masked_text_artifact_never_writes_raw_extracted_text(self) -> None:
        source_text = "연락처 010-1234-5678"
        expected_markers = {
            "token": "[PHONE]",
            "partial": "010-****-5678",
            "pseudonym": "010-0000-",
        }
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source = root / "input.txt"
            source.write_text(source_text, encoding="utf-8")
            detection_counts: dict[str, int] = {}
            masked_outputs: dict[str, str] = {}
            for policy, marker in expected_markers.items():
                output_dir = root / policy
                output_dir.mkdir()
                extracted_path, masked_path, _report_path, report = masker.process_file(
                    str(source),
                    outdir=str(output_dir),
                    opts={
                        "profile": "official",
                        "extract_engine": "auto",
                        "output_artifacts": "pdf_masked_txt_safe_report",
                        "deidentification_policy": policy,
                        "pdf_redaction": False,
                    },
                )

                self.assertIsNone(extracted_path, policy)
                self.assertIsNone(report["outputs"]["extracted_file"], policy)
                self.assertIsNotNone(masked_path, policy)
                masked_text = Path(str(masked_path)).read_text(encoding="utf-8")
                self.assertIn(marker, masked_text, policy)
                self.assertNotIn("010-1234-5678", masked_text, policy)
                self.assertEqual(policy, report["text_deidentification"]["policy"])
                self.assertEqual([], list(output_dir.glob("*.extracted.txt")), policy)
                detection_counts[policy] = sum(int(value) for value in report["counts"].values())
                masked_outputs[policy] = masked_text

            self.assertGreater(detection_counts["token"], 0)
            self.assertEqual(detection_counts["token"], detection_counts["partial"])
            self.assertEqual(detection_counts["token"], detection_counts["pseudonym"])
            self.assertEqual(3, len(set(masked_outputs.values())))

    def test_frontend_exposes_explicit_masked_text_export_option(self) -> None:
        markup = (REPO_ROOT / "src" / "components" / "MaskingSettingsScreen.tsx").read_text(encoding="utf-8")
        legacy_source = legacy_typescript_source()
        settings = (REPO_ROOT / "src" / "settingsState.ts").read_text(encoding="utf-8")
        finalization = (REPO_ROOT / "src" / "features" / "finalization" / "finalizationController.ts").read_text(encoding="utf-8")

        self.assertIn('id="settings-export-masked-text"', markup)
        self.assertIn("비식별 TXT 함께 저장", markup)
        self.assertIn("pdf_masked_txt_safe_report", settings)
        self.assertIn("maskingOutputArtifacts(settingsExportMaskedTextEl.checked)", legacy_source)
        self.assertIn("exportMaskedText", legacy_source)
        self.assertIn("extractedPath: \"\"", finalization)
        self.assertIn("선택한 방식의 비식별 TXT가 아직 없습니다", finalization)
        self.assertNotIn("원문 TXT 저장 전 확인", markup)

    def test_manual_pdf_adjustment_invalidates_masked_text_provenance(self) -> None:
        controller = (REPO_ROOT / "src" / "features" / "manual-adjustment" / "manualAdjustmentController.ts").read_text(encoding="utf-8")
        adoption = controller.index("state.documentProvenance = adoptManualPreview")
        invalidated_path = controller.index('state.latestMaskedPath = "";', adoption)
        invalidated_policy = controller.index('state.latestMaskedTextPolicy = "";', adoption)
        self.assertGreater(invalidated_path, adoption)
        self.assertGreater(invalidated_policy, adoption)


class PrivacySafeErrorBoundaryTests(unittest.TestCase):
    def test_marker_subprocess_errors_never_echo_stderr(self) -> None:
        canary = "private_document_010-1234-5678.pdf"
        with (
            tempfile.TemporaryDirectory() as temporary_directory,
            patch.object(masking_extraction.shutil, "which", return_value="/usr/bin/marker_single"),
            patch.object(masking_extraction, "_run_cmd", return_value=(1, "", canary)),
        ):
            with self.assertRaisesRegex(RuntimeError, "^EXTRACTION_MARKER_FAILED$") as raised:
                masking_extraction._extract_pdf_with_marker("/allowed/input.pdf", temporary_directory)
            self.assertNotIn(canary, str(raised.exception))


if __name__ == "__main__":
    unittest.main()
